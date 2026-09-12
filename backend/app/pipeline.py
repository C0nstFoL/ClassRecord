"""录音处理流水线：转写 -> 总结，运行在后台线程池中，避免阻塞事件循环。"""

import asyncio
import logging
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Recording, RecordingStatus, SegmentSummary
from app.services.llm_service import summarize_transcript
from app.services.whisper_service import transcribe_audio

logger = logging.getLogger(__name__)
settings = get_settings()


def _update_status(db: Session, recording: Recording, status: RecordingStatus, **fields) -> None:
    recording.status = status
    for key, value in fields.items():
        setattr(recording, key, value)
    db.add(recording)
    db.commit()


async def process_recording(
    recording_id: int,
    file_path: str,
    preset: str = "default",
    language: str = "zh",
) -> None:
    db = SessionLocal()
    try:
        recording = db.get(Recording, recording_id)
        if recording is None:
            return

        try:
            _update_status(db, recording, RecordingStatus.TRANSCRIBING)
            transcript = await asyncio.to_thread(
                transcribe_audio, file_path, preset, language
            )
            _update_status(db, recording, RecordingStatus.TRANSCRIBED, transcript_text=transcript)

            _update_status(db, recording, RecordingStatus.SUMMARIZING)
            summary = await summarize_transcript(transcript, title=recording.title)
            _update_status(db, recording, RecordingStatus.COMPLETED, summary_text=summary)
        except Exception as exc:  # noqa: BLE001 - 记录任意处理失败原因供前端展示
            logger.exception("处理录音 %s 失败", recording_id)
            _update_status(db, recording, RecordingStatus.FAILED, error_message=str(exc))
        finally:
            audio_path = Path(file_path)
            # 转写完成后可删除原始音频以节省空间；如需保留原始录音可注释掉这一行。
            if audio_path.exists():
                audio_path.unlink(missing_ok=True)
    finally:
        db.close()


async def retry_summarize(recording_id: int) -> None:
    """转写已完成但总结失败时，仅重新执行总结步骤（音频文件此时已被删除）。"""
    db = SessionLocal()
    try:
        recording = db.get(Recording, recording_id)
        if recording is None or not recording.transcript_text:
            return

        try:
            _update_status(db, recording, RecordingStatus.SUMMARIZING, error_message=None)
            summary = await summarize_transcript(recording.transcript_text, title=recording.title)
            _update_status(db, recording, RecordingStatus.COMPLETED, summary_text=summary)
        except Exception as exc:  # noqa: BLE001
            logger.exception("重试总结录音 %s 失败", recording_id)
            _update_status(db, recording, RecordingStatus.FAILED, error_message=str(exc))
    finally:
        db.close()


async def merge_recordings(main_id: int, source_ids: list[int]) -> None:
    """把多条中断拆分的记录合并进主记录（文本级合并，不重新转写音频）。

    1. 按创建时间顺序拼接各段转写文本（带段号标记，辅助 LLM 总结）；
    2. 来源记录的分段小结按 seq 顺延迁移到主记录，提问记录一并迁移；
    3. 删除来源记录及其音频文件；
    4. 基于合并后的完整文本重新生成整体总结。
    """
    db = SessionLocal()
    try:
        main = db.get(Recording, main_id)
        if main is None:
            return
        sources = [db.get(Recording, sid) for sid in source_ids]
        sources = [s for s in sources if s is not None]

        # 按课堂时间顺序拼接转写文本
        parts = sorted([main, *sources], key=lambda r: r.created_at)
        transcripts = [
            f"【第 {i + 1} 段】\n{r.transcript_text.strip()}"
            for i, r in enumerate(parts)
            if r.transcript_text and r.transcript_text.strip()
        ]
        merged_transcript = "\n\n".join(transcripts)
        if not merged_transcript:
            logger.warning("合并录音 %s 失败：所有记录都没有转写文本", main_id)
            return

        # 分段小结顺延迁移；提问记录一并迁移。
        # 注意：必须通过关系属性（recording=main）赋值，让 ORM 把对象从来源
        # 集合移入主记录集合——直接改 recording_id 外键不会更新集合，随后
        # db.delete(source) 的 delete-orphan 级联会按旧集合把这些行删掉。
        base_seq = db.query(func.max(SegmentSummary.seq)).filter(
            SegmentSummary.recording_id == main.id
        ).scalar()
        base_seq = (base_seq + 1) if base_seq is not None else 0
        for source in sorted(sources, key=lambda r: r.created_at):
            # 迭代副本：移动元素会修改原集合，直接迭代会漏项
            for seg in list(source.segments):
                seg.recording = main
                seg.seq = base_seq
                base_seq += 1
            for qa in list(source.qa_items):
                qa.recording = main

        # 删除来源记录及其音频/PCM 文件
        for source in sources:
            for path in (
                Path(settings.storage_dir) / source.filename,
                (Path(settings.storage_dir) / source.filename).with_suffix(".raw"),
            ):
                path.unlink(missing_ok=True)
            db.delete(source)

        main.transcript_text = merged_transcript
        main.summary_text = None
        db.add(main)
        db.commit()

        try:
            _update_status(db, main, RecordingStatus.SUMMARIZING)
            summary = await summarize_transcript(merged_transcript, title=main.title)
            _update_status(db, main, RecordingStatus.COMPLETED, summary_text=summary)
        except Exception as exc:  # noqa: BLE001
            logger.exception("合并录音 %s 重新总结失败", main_id)
            _update_status(db, main, RecordingStatus.FAILED, error_message=str(exc))
    finally:
        db.close()
