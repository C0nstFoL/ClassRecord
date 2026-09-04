"""实时流式录制的处理逻辑：接收音频块、增量转写、分段小结、结束时整体总结。

音频传输采用 WebSocket，浏览器端 MediaRecorder 以固定时间片持续产生 webm/opus 数据块，
这些数据块依次追加写入同一个文件即可组成一个可被 ffmpeg/faster-whisper 正常解码的完整音频文件。
增量转写策略：每累积一定时长的新音频后，对累积至今的完整文件重新跑一次 whisper 转写，
再与上一次转写结果做公共前缀比较，只把新增的文本部分作为“增量”推送给前端，避免边界处重复。
"""

import asyncio
import logging
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Recording, RecordingStatus, SegmentSummary
from app.services.llm_service import summarize_segment, summarize_transcript
from app.services.whisper_service import transcribe_audio

logger = logging.getLogger(__name__)
settings = get_settings()


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class LiveSession:
    """维护单次实时录制会话的状态（音频缓冲文件、累积转写文本、分段小结计时）。"""

    def __init__(self, recording_id: int, audio_path: Path):
        self.recording_id = recording_id
        self.audio_path = audio_path
        self.transcript_text = ""
        self.last_segment_text_len = 0
        self.last_segment_time = time.monotonic()
        self.segment_seq = 0
        self._file = audio_path.open("wb")

    def write_chunk(self, data: bytes) -> None:
        self._file.write(data)
        self._file.flush()

    def close_file(self) -> None:
        if not self._file.closed:
            self._file.close()

    async def transcribe_increment(self) -> str | None:
        """对累积至今的音频重新转写，返回相对上次的新增文本（若无新增则返回 None）。"""
        if self.audio_path.stat().st_size == 0:
            return None
        full_text = await asyncio.to_thread(transcribe_audio, str(self.audio_path))
        prefix_len = _common_prefix_len(self.transcript_text, full_text)
        delta = full_text[prefix_len:]
        self.transcript_text = full_text
        if not delta.strip():
            return None
        return delta

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
