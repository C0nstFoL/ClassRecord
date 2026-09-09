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
    language: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


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
