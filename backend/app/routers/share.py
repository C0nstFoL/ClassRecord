"""免登录的分享链接访问接口：凭 URL 中的随机 token 查看记录内容（只读）。"""

import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Recording
from app.schemas import SharedRecordingOut, SharedSegmentOut

router = APIRouter(prefix="/api/share", tags=["share"])


@router.get("/{token}", response_model=SharedRecordingOut)
async def view_shared_recording(token: str, db: Session = Depends(get_db)):
    recording = db.query(Recording).filter(Recording.share_token == token).first()
    if recording is None:
        raise HTTPException(status_code=404, detail="分享链接不存在或已被撤销")
    if recording.share_expires_at is None or recording.share_expires_at < datetime.datetime.utcnow():
        raise HTTPException(status_code=410, detail="分享链接已过期")
    return SharedRecordingOut(
        title=recording.title,
        language=recording.language,
        transcript_text=recording.transcript_text,
        summary_text=recording.summary_text,
        segments=[SharedSegmentOut.model_validate(seg) for seg in recording.segments],
        expires_at=recording.share_expires_at,
    )
