"""实时流式录制的处理逻辑：接收音频块、增量转写、分段小结、结束时整体总结。

音频传输采用 WebSocket，浏览器端 MediaRecorder 以固定时间片持续产生 webm/mp4 数据块，
这些数据块依次追加写入同一个文件即可组成一个可被 ffmpeg/pyav 正常解码的完整音频文件。

增量转写采用「滑动窗口」架构，保证长课堂（1 小时以上）转写耗时恒定：
1. 每轮先把新增音频增量解码为 16kHz 单声道 PCM，追加到会话级 raw 文件（只处理新数据）；
2. 只对 raw 文件末尾最近 live_window_seconds 秒（含少量重叠上下文）跑一次识别；
3. 窗口转写结果与上一轮结果做公共前缀比较：公共部分归档为「稳定文本」，
   尾部作为「不稳定文本」继续随下轮结果修订。
前端每轮收到 全量文本 = 稳定文本 + 当前窗口未稳定尾部，整体替换渲染。
"""

import asyncio
import logging
import time
import wave
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Recording, RecordingStatus, SegmentSummary
from app.services.llm_service import summarize_segment, summarize_transcript
from app.services.whisper_service import transcribe_audio

logger = logging.getLogger(__name__)
settings = get_settings()

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # s16le mono


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _align_head(pending: str, window_text: str) -> int:
    """计算 window_text 头部被 pending 覆盖（重复）的字符数 consumed。

    pending 是上一轮已展示但未归档的文本，本轮无条件归档（保证文本只增不减）。
    新窗口会重新转写与 pending 重叠的音频，头部可能与 pending 部分一致，
    consumed 即跳过这段重复的长度；对齐失败时取精确公共前缀。
    """
    if not pending:
        return 0
    from difflib import SequenceMatcher

    head = window_text[: len(pending) + 30]
    matcher = SequenceMatcher(None, pending, head, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size > 0]
    # 在窗口中寻找 pending 的可信匹配块（足够长：短 pending 时允许整段匹配）
    if not blocks or blocks[0].size < min(4, len(pending)):
        return _common_prefix_len(pending, window_text)
    consumed = blocks[0].b + blocks[0].size
    # 沿窗口方向扩展连续匹配块，覆盖 pending 中间因抖动断开的部分
    for b in blocks[1:]:
        if b.b == consumed and b.a >= (blocks[0].a + blocks[0].size):
            consumed += b.size
    return min(consumed, len(window_text))


class LiveSession:
    """维护单次实时录制会话的状态（原始音频、PCM 累积、滑动窗口转写、分段小结计时）。"""

    def __init__(
        self,
        recording_id: int,
        audio_path: Path,
        preset: str = "default",
        language: str = "zh",
    ):
        self.recording_id = recording_id
        self.audio_path = audio_path
        self.preset = preset
        self.language = language
        # 稳定文本：窗口转写中已与上轮结果一致而被归档的前缀
        self.stable_text = ""
        # 全量转写文本 = stable_text + 当前窗口未稳定尾部
        self.transcript_text = ""
        # 上一轮窗口转写结果（用于前缀比较）
        self._window_text = ""
        # 锚定窗口起点（raw 文件字节偏移）：窗口攒满归档后才前移
        self._anchor_bytes = 0
        # 上一轮完整窗口文本（用于同锚点纯增长的快速去重）
        self._win_full = ""
        # 累积解码出的 PCM 时长（秒）
        self.pcm_seconds = 0.0
        # 源音频中已解码到的位置（秒，用于增量解码跳过旧数据）
        self._decoded_time = -1.0
        self.last_segment_text_len = 0
        self.last_segment_time = time.monotonic()
        self.segment_seq = 0
        self._file = audio_path.open("wb")
        self._raw_path = audio_path.with_suffix(".raw")
        self._raw = self._raw_path.open("wb")

    def write_chunk(self, data: bytes) -> None:
        self._file.write(data)
        self._file.flush()

    def close_file(self) -> None:
        for f in (self._file, self._raw):
            if not f.closed:
                f.close()

    def _ingest_new_audio(self) -> None:
        """把源音频中尚未解码的新增部分增量解码为 16kHz s16 PCM 追加到 raw 文件。

        每轮重新打开容器并 seek 到上次解码位置附近（回退 2 秒防边界丢帧），
        只解码新数据——保证长课堂下该步骤耗时也恒定。
        """
        import av
        import numpy as np

        if self.audio_path.stat().st_size == 0:
            return
        container = av.open(str(self.audio_path))
        try:
            audio_stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            if self._decoded_time > 0:
                # 回退 2 秒重解一小段，靠 pts 去重，避免 seek 边界丢帧
                back = max(0.0, self._decoded_time - 2.0)
                tb = audio_stream.time_base
                container.seek(int(back / tb), stream=audio_stream, backward=True, any_frame=True)
            for frame in container.decode(audio=0):
                t = frame.time
                if t is not None:
                    if t <= self._decoded_time:
                        continue
                    self._decoded_time = t
                for resampled in resampler.resample(frame):
                    arr = resampled.to_ndarray()
                    pcm = arr.astype(np.int16).tobytes()
                    self._raw.write(pcm)
                    self.pcm_seconds += len(pcm) / (SAMPLE_RATE * BYTES_PER_SAMPLE)
            self._raw.flush()
        finally:
            container.close()

    def _transcribe_window(self) -> str | None:
        """转写锚定窗口（raw 文件从 _anchor_bytes 起到末尾）的音频，返回文本。

        窗口起点固定不动、随新音频增长，攒满 live_window_seconds 后整体归档并
        把锚点前移（保留 overlap_seconds 重叠上下文），保证文本只增不减。
        """
        raw_size = self._raw_path.stat().st_size
        window_pcm = raw_size - self._anchor_bytes
        if window_pcm < SAMPLE_RATE * BYTES_PER_SAMPLE:  # 不足 1 秒不转写
            return None
        tmp_wav = self._raw_path.with_suffix(".window.wav")
        with self._raw_path.open("rb") as src:
            src.seek(self._anchor_bytes)
            pcm = src.read()
        with wave.open(str(tmp_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(BYTES_PER_SAMPLE)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm)
        try:
            return transcribe_audio(str(tmp_wav), self.preset, self.language)
        finally:
            tmp_wav.unlink(missing_ok=True)

    async def transcribe_increment(self) -> str | None:
        """锚定窗口增量转写：每轮只转写自锚点起的音频（最多约 30 秒）。

        同一锚点的相邻两轮结果做公共前缀比较，一致部分归档为稳定文本；
        窗口攒满后整体归档，锚点前移并保留少量重叠。文本只增不减。
        返回相对上一轮全量文本的新增尾部（无新增返回 None）。
        """
        await asyncio.to_thread(self._ingest_new_audio)
        window_text = await asyncio.to_thread(self._transcribe_window)
        if not window_text:
            return None
        window_text = window_text.strip()
        raw_size = self._raw_path.stat().st_size
        window_bytes = raw_size - self._anchor_bytes
        window_seconds = window_bytes / (SAMPLE_RATE * BYTES_PER_SAMPLE)

        # pending：上一轮尚未归档的文本，本轮无条件归档（保证文本只增不减）。
        # 快速路径：同一锚点下窗口纯增长时，新窗口文本以旧窗口全文开头，
        # 直接按旧窗口全文长度跳过重复；否则退回模糊对齐。
        pending = self._window_text
        if self._win_full and window_text.startswith(self._win_full):
            consumed = len(self._win_full)
        else:
            consumed = _align_head(pending, window_text)
        self.stable_text += pending
        unstable = window_text[consumed:]

        if window_seconds >= settings.live_window_seconds:
            # 窗口攒满：未归档文本全部归档，锚点直接跳到最新位置（无重叠切分）。
            # 每段音频只被转写一次，从机制上杜绝窗口边界重复；边界处个别词
            # 可能因上下文缺失略有偏差，但不丢不重。
            self.stable_text += unstable
            self._anchor_bytes = raw_size
            self._window_text = ""
            self._win_full = ""
            unstable = ""
        else:
            self._win_full = window_text

        full_text = self.stable_text + unstable
        prev_full = self.transcript_text
        self.transcript_text = full_text
        delta = full_text[_common_prefix_len(prev_full, full_text):]
        # 归档推进导致的文本缩短不应发生；保险起见若变短则回退上一版全量
        if len(full_text) < len(prev_full) - 2:
            self.transcript_text = prev_full
            return None
        if not delta.strip():
            return None
        return delta

    def append_text(self, text: str) -> str:
        """追加客户端（手机本地识别）推送的文本，返回新增部分。"""
        if not text:
            return ""
        self.transcript_text += text
        return text

    def should_trigger_segment(self) -> bool:
        new_chars = len(self.transcript_text) - self.last_segment_text_len
        elapsed = time.monotonic() - self.last_segment_time
        if new_chars <= 0:
            return False
        return new_chars >= settings.live_segment_max_chars or elapsed >= settings.live_segment_max_seconds

    def mark_segment_done(self) -> None:
        self.last_segment_text_len = len(self.transcript_text)
        self.last_segment_time = time.monotonic()
        self.segment_seq += 1


def _update_status(db: Session, recording: Recording, status_: RecordingStatus, **fields) -> None:
    recording.status = status_
    for key, value in fields.items():
        setattr(recording, key, value)
    db.add(recording)
    db.commit()


async def generate_segment_summary(session: LiveSession) -> SegmentSummary | None:
    """对本段新增的转写文本生成一段小结并存入数据库。"""
    segment_text = session.transcript_text[session.last_segment_text_len :]
    if not segment_text.strip():
        return None
    db = SessionLocal()
    try:
        summary = await summarize_segment(segment_text)
        record = SegmentSummary(
            recording_id=session.recording_id,
            seq=session.segment_seq,
            text=summary,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record
    except Exception:
        logger.exception("生成录音 %s 分段小结失败", session.recording_id)
        return None
    finally:
        db.close()


async def finalize_live_recording(recording_id: int) -> None:
    """实时录制结束：基于完整转写文本生成整体总结，音频文件保留以支持失败重试。"""
    db = SessionLocal()
    try:
        recording = db.get(Recording, recording_id)
        if recording is None:
            return
        if not recording.transcript_text:
            _update_status(db, recording, RecordingStatus.FAILED, error_message="未识别到有效语音内容")
            return
        try:
            _update_status(db, recording, RecordingStatus.SUMMARIZING)
            summary = await summarize_transcript(recording.transcript_text, title=recording.title)
            _update_status(db, recording, RecordingStatus.COMPLETED, summary_text=summary)
        except Exception as exc:  # noqa: BLE001
            logger.exception("实时录制 %s 结束总结失败", recording_id)
            _update_status(db, recording, RecordingStatus.FAILED, error_message=str(exc))
    finally:
        db.close()
