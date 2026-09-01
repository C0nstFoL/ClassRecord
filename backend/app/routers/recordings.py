import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_current_user_ws
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.live_pipeline import LiveSession, finalize_live_recording, generate_segment_summary
from app.models import QaRecord, Recording, RecordingStatus, User
from app.pipeline import process_recording, retry_summarize
from app.schemas import AskQuestionIn, QaRecordOut, RecordingOut, SegmentSummaryOut
from app.services.llm_service import answer_question

router = APIRouter(prefix="/api/recordings", tags=["recordings"])
settings = get_settings()
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4"}


@router.get("", response_model=list[RecordingOut])
async def list_recordings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(Recording)
        .filter(Recording.user_id == user.id)
        .order_by(Recording.created_at.desc())
        .all()
    )


@router.get("/{recording_id}", response_model=RecordingOut)
async def get_recording(
    recording_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    return recording


@router.post("", response_model=RecordingOut)
async def upload_recording(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    title: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式：{suffix}")

    stored_name = f"{uuid.uuid4().hex}{suffix}"
    stored_path = Path(settings.storage_dir) / stored_name

    with stored_path.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    recording = Recording(
        user_id=user.id,
        title=title,
        filename=stored_name,
        status=RecordingStatus.UPLOADED,
    )
    db.add(recording)
    db.commit()
    db.refresh(recording)

    background_tasks.add_task(process_recording, recording.id, str(stored_path))

    return recording


@router.post("/{recording_id}/retry", response_model=RecordingOut)
async def retry_recording(
    recording_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    if recording.status != RecordingStatus.FAILED:
        raise HTTPException(status_code=400, detail="只有处理失败的记录才能重试")
    if not recording.transcript_text:
        raise HTTPException(status_code=400, detail="转写阶段失败且原始音频已被清理，无法重试，请重新上传")

    background_tasks.add_task(retry_summarize, recording.id)
    return recording


@router.get("/{recording_id}/qa", response_model=list[QaRecordOut])
async def list_qa(
    recording_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    return recording.qa_items


@router.get("/{recording_id}/segments", response_model=list[SegmentSummaryOut])
async def list_segments(
    recording_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    return recording.segments


@router.post("/live", response_model=RecordingOut)
async def create_live_recording(
    title: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建一条实时录制记录的占位行，随后前端通过 WebSocket 连接 /ws/recordings/{id}/stream 推流。"""
    stored_name = f"{uuid.uuid4().hex}.webm"
    recording = Recording(
        user_id=user.id,
        title=title,
        filename=stored_name,
        status=RecordingStatus.RECORDING,
        is_live=True,
    )
    db.add(recording)
    db.commit()
    db.refresh(recording)
    return recording


@router.websocket("/ws/{recording_id}/stream")
async def stream_recording(websocket: WebSocket, recording_id: int):
    """接收浏览器持续推送的音频块，增量转写并推送文本/分段小结，断开时触发整体总结。

    协议：
    - 二进制帧：音频数据块（webm/opus），按接收顺序追加写入同一音频文件。
    - 文本帧 "stop"：客户端主动结束录制，服务端完成最后一次转写与整体总结后关闭连接。
    - 服务端下行 JSON 消息：
        {"type": "transcript_delta", "text": "..."}       增量转写文本
        {"type": "segment_summary", "seq": 0, "text": "..."} 分段小结
        {"type": "error", "message": "..."}                错误信息
        {"type": "done"}                                   整体总结已生成，可关闭连接
    """
    await websocket.accept()

    db = SessionLocal()
    try:
        user = get_current_user_ws(websocket, db)
        if user is None:
            await websocket.close(code=4401, reason="未登录")
            return

        recording = db.get(Recording, recording_id)
        if recording is None or recording.user_id != user.id:
            await websocket.close(code=4404, reason="记录不存在")
            return
    finally:
        db.close()

    audio_path = Path(settings.storage_dir) / recording.filename
    session = LiveSession(recording_id, audio_path)
    transcribe_task: asyncio.Task | None = None

    async def run_transcribe_loop():
        while True:
            await asyncio.sleep(settings.live_transcribe_interval_seconds)
            try:
                delta = await session.transcribe_increment()
            except Exception:
                logger.exception("实时转写录音 %s 增量失败", recording_id)
                continue
            if delta:
                await websocket.send_json({"type": "transcript_delta", "text": delta})
                _persist_transcript(recording_id, session.transcript_text)
                if session.should_trigger_segment():
                    record = await generate_segment_summary(session)
                    if record is not None:
                        session.mark_segment_done()
                        await websocket.send_json(
                            {"type": "segment_summary", "seq": record.seq, "text": record.text}
                        )

    try:
        transcribe_task = asyncio.create_task(run_transcribe_loop())
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if "bytes" in message and message["bytes"] is not None:
                session.write_chunk(message["bytes"])
            elif "text" in message and message["text"] == "stop":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if transcribe_task is not None:
            transcribe_task.cancel()
        session.close_file()
        # 结束前再跑一次转写，确保收尾的音频片段也被识别
        try:
            delta = await session.transcribe_increment()
            if delta:
                _persist_transcript(recording_id, session.transcript_text)
        except Exception:
            logger.exception("实时转写录音 %s 收尾转写失败", recording_id)

        try:
            await websocket.send_json({"type": "done"})
        except Exception:
            pass
        await finalize_live_recording(recording_id)
        try:
            await websocket.close()
        except Exception:
            pass


def _persist_transcript(recording_id: int, transcript_text: str) -> None:
    db = SessionLocal()
    try:
        recording = db.get(Recording, recording_id)
        if recording is not None:
            recording.transcript_text = transcript_text
            db.add(recording)
            db.commit()
    finally:
        db.close()


@router.post("/{recording_id}/ask", response_model=QaRecordOut)
async def ask_question(
    recording_id: int,
    payload: AskQuestionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    if not recording.transcript_text:
        raise HTTPException(status_code=400, detail="该记录还没有转写文本，暂无法提问")
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    answer = await answer_question(recording.transcript_text, question)
    qa = QaRecord(recording_id=recording.id, question=question, answer=answer)
    db.add(qa)
    db.commit()
    db.refresh(qa)
    return qa


@router.delete("/{recording_id}")
async def delete_recording(
    recording_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.delete(recording)
    db.commit()
    return {"ok": True}
