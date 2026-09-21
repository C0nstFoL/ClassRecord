import datetime
import enum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RecordingStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"
    RECORDING = "recording"  # 实时录制中，音频流仍在持续接收


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

    recordings: Mapped[list["Recording"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[RecordingStatus] = mapped_column(
        Enum(RecordingStatus), default=RecordingStatus.UPLOADED
    )
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # class：普通课堂记录；homework：由多条课堂总结整理生成的待办作业记录
    record_type: Mapped[str] = mapped_column(String(16), default="class", index=True)
    is_live: Mapped[bool] = mapped_column(default=False)  # 是否为实时流式录制产生的记录
    is_paused: Mapped[bool] = mapped_column(default=False)  # 实时录制是否处于暂停（暂停中不参与僵死清理）
    # 转写完成后是否自动生成总结；关闭时停在「已转写」，可在详情页手动生成
    auto_summary: Mapped[bool] = mapped_column(default=True)
    language: Mapped[str] = mapped_column(String(8), default="zh")  # 识别语言：zh/en/ja/ko/yue
    # 分享链接：token 为空表示未开启分享；expires_at 过期后公开访问失效
    share_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    share_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    user: Mapped["User"] = relationship(back_populates="recordings")
    qa_items: Mapped[list["QaRecord"]] = relationship(
        back_populates="recording", cascade="all, delete-orphan", order_by="QaRecord.created_at"
    )
    segments: Mapped[list["SegmentSummary"]] = relationship(
        back_populates="recording", cascade="all, delete-orphan", order_by="SegmentSummary.seq"
    )


class QaRecord(Base):
    __tablename__ = "qa_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

    recording: Mapped["Recording"] = relationship(back_populates="qa_items")


class SegmentSummary(Base):
    """实时录制过程中，转写文本累积到一定量后生成的分段小结。"""

    __tablename__ = "segment_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

    recording: Mapped["Recording"] = relationship(back_populates="segments")


class HomeworkExtractionJob(Base):
    """跨页面刷新保存的作业提取任务与结果。"""

    __tablename__ = "homework_extraction_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    recording_names_json: Mapped[str] = mapped_column(Text)
    homework: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_recording_id: Mapped[int | None] = mapped_column(
        ForeignKey("recordings.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )


class HomeworkTask(Base):
    """待办作业记录中的可勾选列表项。"""

    __tablename__ = "homework_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    deadline: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_recording_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    completed: Mapped[bool] = mapped_column(default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )
