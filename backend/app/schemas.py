import datetime

from pydantic import BaseModel, ConfigDict

from app.models import RecordingStatus


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str | None
    name: str | None


class RecordingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: RecordingStatus
    transcript_text: str | None
    summary_text: str | None
    error_message: str | None
    is_live: bool
    is_paused: bool
    auto_summary: bool
    language: str
    share_expires_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class RecordingUpdateIn(BaseModel):
    title: str


class ShareLinkIn(BaseModel):
    # 分享有效期（小时）：24 / 72 / 168
    hours: int


class MergeRecordingsIn(BaseModel):
    # 要并入主记录的来源记录 id 列表（主记录由 URL 指定）
    source_ids: list[int]


class ShareLinkOut(BaseModel):
    token: str
    expires_at: datetime.datetime


class SharedSegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    text: str


class SharedRecordingOut(BaseModel):
    """免登录分享页可见的内容（只读，不含用户信息与提问功能）。"""

    title: str
    language: str
    transcript_text: str | None
    summary_text: str | None
    segments: list[SharedSegmentOut]
    expires_at: datetime.datetime


class SegmentSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    seq: int
    text: str
    created_at: datetime.datetime


class AskQuestionIn(BaseModel):
    question: str


class QaRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    question: str
    answer: str
    created_at: datetime.datetime
