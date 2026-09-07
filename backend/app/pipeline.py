"""录音处理流水线：转写 -> 总结，运行在后台线程池中，避免阻塞事件循环。"""

import asyncio
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Recording, RecordingStatus
from app.services.llm_service import summarize_transcript
from app.services.whisper_service import transcribe_audio

logger = logging.getLogger(__name__)


def _update_status(db: Session, recording: Recording, status: RecordingStatus, **fields) -> None:
    recording.status = status
    for key, value in fields.items():
        setattr(recording, key, value)
    db.add(recording)
    db.commit()


async def process_recording(recording_id: int, file_path: str) -> None:
    db = SessionLocal()
    try:
        recording = db.get(Recording, recording_id)
        if recording is None:
            return

        try:
            _update_status(db, recording, RecordingStatus.TRANSCRIBING)
            transcript = await asyncio.to_thread(transcribe_audio, file_path, preset)
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
