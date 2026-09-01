import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import QaRecord, Recording, RecordingStatus, User
from app.pipeline import process_recording, retry_summarize
from app.schemas import AskQuestionIn, QaRecordOut, RecordingOut
from app.services.llm_service import answer_question

router = APIRouter(prefix="/api/recordings", tags=["recordings"])
settings = get_settings()

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
