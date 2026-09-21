import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.models import RecordingStatus


class ApiModel(BaseModel):
    """API 中的数据库时间均按 UTC 存储，并显式输出时区标记。"""

    @field_serializer("*", check_fields=False, when_used="json")
    def serialize_utc_datetimes(self, value):
        if not isinstance(value, datetime.datetime):
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.UTC)
        else:
            value = value.astimezone(datetime.UTC)
        return value.isoformat().replace("+00:00", "Z")


class UserOut(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str | None
    name: str | None


class RecordingOut(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: RecordingStatus
    transcript_text: str | None
    summary_text: str | None
    error_message: str | None
    record_type: str
    is_live: bool
    is_paused: bool
    auto_summary: bool
    language: str
    share_expires_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class RecordingListOut(ApiModel):
    """记录列表的轻量响应，不携带可能很大的转写与总结正文。"""

    id: int
    title: str
    status: RecordingStatus
    record_type: str
    is_live: bool
    is_paused: bool
    has_summary: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class RecordingUpdateIn(ApiModel):
    title: str


class ShareLinkIn(ApiModel):
    # 分享有效期（小时）：24 / 72 / 168
    hours: int


class MergeRecordingsIn(ApiModel):
    # 要并入主记录的来源记录 id 列表（主记录由 URL 指定）
    source_ids: list[int]


class ExtractHomeworkIn(ApiModel):
    recording_ids: list[int]


class HomeworkOut(ApiModel):
    job_id: str
    status: Literal["pending", "processing", "completed", "failed"]
    recording_names: list[str] = Field(default_factory=list)
    homework: str | None = None
    error: str | None = None
    result_recording_id: int | None = None


class HomeworkTaskUpdateIn(ApiModel):
    completed: bool


class HomeworkTaskSourceOut(ApiModel):
    id: int
    title: str


class HomeworkTaskOut(ApiModel):
    id: int
    content: str
    deadline: str | None
    details: str | None
    completed: bool
    sort_order: int
    sources: list[HomeworkTaskSourceOut] = Field(default_factory=list)


class ShareLinkOut(ApiModel):
    token: str
    expires_at: datetime.datetime


class SharedSegmentOut(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    text: str


class SharedRecordingOut(ApiModel):
    """免登录分享页可见的内容（只读，不含用户信息与提问功能）。"""

    title: str
    language: str
    transcript_text: str | None
    summary_text: str | None
    segments: list[SharedSegmentOut]
    expires_at: datetime.datetime


class SegmentSummaryOut(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    seq: int
    text: str
    created_at: datetime.datetime


class AskQuestionIn(ApiModel):
    question: str


class QaRecordOut(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    question: str
    answer: str
    created_at: datetime.datetime
