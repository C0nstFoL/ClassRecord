import asyncio
import datetime
import json
import logging
import re
import secrets
import uuid
from datetime import timedelta
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
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_current_user_ws
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.live_pipeline import LiveSession, finalize_live_recording, generate_segment_summary
from app.models import HomeworkExtractionJob, HomeworkTask, QaRecord, Recording, RecordingStatus, SegmentSummary, User
from app.pipeline import merge_recordings, process_recording, remove_recording_files, retry_summarize
from app.schemas import (
    AskQuestionIn,
    ExtractHomeworkIn,
    HomeworkOut,
    HomeworkTaskOut,
    HomeworkTaskSourceOut,
    HomeworkTaskUpdateIn,
    MergeRecordingsIn,
    QaRecordOut,
    RecordingOut,
    RecordingUpdateIn,
    SegmentSummaryOut,
    ShareLinkIn,
    ShareLinkOut,
)
from app.services.llm_service import answer_question, extract_homework

router = APIRouter(prefix="/api/recordings", tags=["recordings"])
settings = get_settings()
logger = logging.getLogger(__name__)

# 活跃实时录制会话注册表（进程内）：同一记录只允许一个 WS 连接，
# 防止双连接并发打开同一音频文件互相截断/交叉转写
_active_live_recordings: set[int] = set()

ALLOWED_EXTENSIONS = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4"}


@router.get("", response_model=list[RecordingOut])
async def list_recordings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(Recording)
        .filter(Recording.user_id == user.id)
        .order_by(Recording.created_at.desc())
        .all()
    )


@router.get("/presets")
async def list_presets(_user: User = Depends(get_current_user)):
    """返回可选的课程热词预设（key + 展示名），供前端录制页下拉框使用。"""
    return [
        {"key": key, "label": conf.get("label", key)}
        for key, conf in settings.whisper_presets.items()
    ]


@router.get("/{recording_id}", response_model=RecordingOut)
async def get_recording(
    recording_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    return recording


@router.patch("/{recording_id}", response_model=RecordingOut)
async def update_recording(
    recording_id: int,
    payload: RecordingUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改记录标题。"""
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    recording.title = title[:255]
    db.add(recording)
    db.commit()
    db.refresh(recording)
    return recording


@router.post("/{recording_id}/merge", response_model=RecordingOut)
async def merge_into_recording(
    recording_id: int,
    payload: MergeRecordingsIn,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把多段中断拆分的记录合并进主记录：拼接转写、迁移小结/提问后重新总结。"""
    main = db.get(Recording, recording_id)
    if main is None or main.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    if main.status == RecordingStatus.RECORDING:
        raise HTTPException(status_code=400, detail="主记录正在录制中，无法合并")
    if not payload.source_ids or recording_id in payload.source_ids:
        raise HTTPException(status_code=400, detail="来源记录列表无效")
    sources = db.query(Recording).filter(Recording.id.in_(payload.source_ids)).all()
    if len(sources) != len(set(payload.source_ids)):
        raise HTTPException(status_code=404, detail="部分来源记录不存在")
    for source in sources:
        if source.user_id != user.id:
            raise HTTPException(status_code=403, detail="存在不属于当前用户的记录")
        if source.status == RecordingStatus.RECORDING:
            raise HTTPException(status_code=400, detail=f"「{source.title}」正在录制中，无法合并")
        if not source.transcript_text or not source.transcript_text.strip():
            raise HTTPException(status_code=400, detail=f"「{source.title}」没有转写文本，无法合并")
    background_tasks.add_task(merge_recordings, recording_id, payload.source_ids)
    db.refresh(main)
    return main


async def _run_homework_job(job_id: str, recordings: list[tuple[int, str, str]]) -> None:
    with SessionLocal() as db:
        job = db.get(HomeworkExtractionJob, job_id)
        if job is None:
            return
        if job.status == "completed" and job.result_recording_id is not None:
            return
        job.status = "processing"
        db.commit()

    try:
        result = await extract_homework([(title, summary) for _, title, summary in recordings])
    except Exception as exc:  # noqa: BLE001 - 将 LLM 错误转为前端可读状态
        logger.exception("提取作业任务 %s 失败", job_id)
        with SessionLocal() as db:
            job = db.get(HomeworkExtractionJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error = str(exc)
                db.commit()
        return

    with SessionLocal() as db:
        job = db.get(HomeworkExtractionJob, job_id)
        if job is not None:
            result_recording = Recording(
                user_id=job.user_id,
                title=result.title,
                filename=f"homework-{job_id}.md",
                status=RecordingStatus.COMPLETED,
                transcript_text=None,
                summary_text=result.markdown,
                record_type="homework",
                is_live=False,
                is_paused=False,
                auto_summary=False,
                language="zh",
            )
            db.add(result_recording)
            db.flush()
            for sort_order, task_data in enumerate(result.tasks):
                source_ids = [recordings[index - 1][0] for index in task_data.source_indexes]
                db.add(
                    HomeworkTask(
                        recording_id=result_recording.id,
                        content=task_data.content,
                        deadline=task_data.deadline,
                        details=task_data.details,
                        source_recording_ids_json=json.dumps(source_ids),
                        completed=False,
                        sort_order=sort_order,
                    )
                )
            job.status = "completed"
            job.homework = result.markdown
            job.error = None
            job.result_recording_id = result_recording.id
            db.commit()


def _homework_job_out(job: HomeworkExtractionJob) -> HomeworkOut:
    return HomeworkOut(
        job_id=job.id,
        status=job.status,
        recording_names=json.loads(job.recording_names_json),
        homework=job.homework,
        error=job.error,
        result_recording_id=job.result_recording_id,
    )


@router.post("/homework", response_model=HomeworkOut)
async def extract_homework_from_recordings(
    payload: ExtractHomeworkIn,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """从用户选择的多条课堂记录中提取作业清单。"""
    recording_ids = list(dict.fromkeys(payload.recording_ids))
    if len(recording_ids) < 2:
        raise HTTPException(status_code=400, detail="请至少选择 2 条记录")

    recordings = db.query(Recording).filter(Recording.id.in_(recording_ids)).all()
    if len(recordings) != len(recording_ids):
        raise HTTPException(status_code=404, detail="部分记录不存在")
    for recording in recordings:
        if recording.user_id != user.id:
            raise HTTPException(status_code=403, detail="存在不属于当前用户的记录")
        if recording.record_type == "homework":
            raise HTTPException(status_code=400, detail=f"「{recording.title}」是整理结果，不能重复参与整理")
        if recording.status == RecordingStatus.RECORDING:
            raise HTTPException(status_code=400, detail=f"「{recording.title}」正在录制中，暂不能提取作业")
        if not recording.summary_text or not recording.summary_text.strip():
            raise HTTPException(status_code=400, detail=f"「{recording.title}」还没有课堂总结")

    ordered = sorted(recordings, key=lambda item: (item.created_at, item.id))
    job_id = uuid.uuid4().hex
    recording_names = [recording.title for recording in ordered]
    job = HomeworkExtractionJob(
        id=job_id,
        user_id=user.id,
        status="pending",
        recording_names_json=json.dumps(recording_names, ensure_ascii=False),
    )
    db.add(job)
    db.commit()
    background_tasks.add_task(
        _run_homework_job,
        job_id,
        [(recording.id, recording.title, recording.summary_text.strip()) for recording in ordered],
    )
    return HomeworkOut(
        job_id=job_id,
        status="pending",
        recording_names=recording_names,
    )


@router.get("/homework/latest", response_model=HomeworkOut | None)
async def get_latest_homework_job(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回当前用户最近一次作业提取任务，供页面刷新后恢复。"""
    job = (
        db.query(HomeworkExtractionJob)
        .filter(HomeworkExtractionJob.user_id == user.id)
        .order_by(HomeworkExtractionJob.created_at.desc())
        .first()
    )
    return _homework_job_out(job) if job is not None else None


@router.get("/homework/{job_id}", response_model=HomeworkOut)
async def get_homework_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查询当前用户发起的作业提取任务。"""
    job = db.get(HomeworkExtractionJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="作业提取任务不存在或已过期")
    return _homework_job_out(job)


def _homework_task_out(task: HomeworkTask, user_id: int, db: Session) -> HomeworkTaskOut:
    try:
        source_ids = json.loads(task.source_recording_ids_json)
    except (TypeError, ValueError):
        source_ids = []
    sources_by_id = {
        recording.id: recording
        for recording in db.query(Recording)
        .filter(Recording.id.in_(source_ids), Recording.user_id == user_id)
        .all()
    } if source_ids else {}
    return HomeworkTaskOut(
        id=task.id,
        content=task.content,
        deadline=task.deadline,
        details=task.details,
        completed=task.completed,
        sort_order=task.sort_order,
        sources=[
            HomeworkTaskSourceOut(id=source_id, title=sources_by_id[source_id].title)
            for source_id in source_ids
            if source_id in sources_by_id
        ],
    )


def _sync_homework_markdown_checkboxes(
    recording: Recording,
    tasks: list[HomeworkTask],
) -> None:
    """让分享与导出的 Markdown 勾选状态和结构化待办保持一致。"""
    if not recording.summary_text:
        return
    lines = recording.summary_text.splitlines()
    task_index = 0
    for line_index, line in enumerate(lines):
        if task_index >= len(tasks):
            break
        if re.match(r"^- \[[ xX]\] ", line):
            marker = "x" if tasks[task_index].completed else " "
            lines[line_index] = re.sub(r"^- \[[ xX]\]", f"- [{marker}]", line, count=1)
            task_index += 1
    recording.summary_text = "\n".join(lines)


@router.get("/{recording_id}/homework-tasks", response_model=list[HomeworkTaskOut])
async def list_homework_tasks(
    recording_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id or recording.record_type != "homework":
        raise HTTPException(status_code=404, detail="待办作业记录不存在")
    tasks = (
        db.query(HomeworkTask)
        .filter(HomeworkTask.recording_id == recording_id)
        .order_by(HomeworkTask.sort_order, HomeworkTask.id)
        .all()
    )
    return [_homework_task_out(task, user.id, db) for task in tasks]


@router.patch("/{recording_id}/homework-tasks/{task_id}", response_model=HomeworkTaskOut)
async def update_homework_task(
    recording_id: int,
    task_id: int,
    payload: HomeworkTaskUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id or recording.record_type != "homework":
        raise HTTPException(status_code=404, detail="待办作业记录不存在")
    task = db.get(HomeworkTask, task_id)
    if task is None or task.recording_id != recording_id:
        raise HTTPException(status_code=404, detail="待办事项不存在")
    task.completed = payload.completed
    tasks = (
        db.query(HomeworkTask)
        .filter(HomeworkTask.recording_id == recording_id)
        .order_by(HomeworkTask.sort_order, HomeworkTask.id)
        .all()
    )
    _sync_homework_markdown_checkboxes(recording, tasks)
    db.add(task)
    db.add(recording)
    db.commit()
    db.refresh(task)
    return _homework_task_out(task, user.id, db)


@router.post("/{recording_id}/finish", response_model=RecordingOut)
async def finish_recording(
    recording_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """结束一条（通常处于暂停中、连接已丢失的）实时录制并生成总结。"""
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    if recording.status != RecordingStatus.RECORDING:
        raise HTTPException(status_code=400, detail="该记录不在录制中")
    recording.is_paused = False
    db.add(recording)
    db.commit()
    db.refresh(recording)
    background_tasks.add_task(finalize_live_recording, recording.id)
    return recording


@router.post("/{recording_id}/share", response_model=ShareLinkOut)
async def create_share_link(
    recording_id: int,
    payload: ShareLinkIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """生成限时分享链接：随机 token，到期后自动失效；重复生成会替换旧链接。"""
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    if payload.hours not in (24, 72, 168):
        raise HTTPException(status_code=400, detail="有效期仅支持 24 / 72 / 168 小时")
    recording.share_token = secrets.token_urlsafe(24)
    recording.share_expires_at = datetime.datetime.utcnow() + timedelta(hours=payload.hours)
    db.add(recording)
    db.commit()
    return ShareLinkOut(token=recording.share_token, expires_at=recording.share_expires_at)


@router.delete("/{recording_id}/share", response_model=RecordingOut)
async def revoke_share_link(
    recording_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """撤销分享链接：清空 token，链接立即失效。"""
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    recording.share_token = None
    recording.share_expires_at = None
    db.add(recording)
    db.commit()
    db.refresh(recording)
    return recording


@router.post("", response_model=RecordingOut)
async def upload_recording(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    title: str = Form(...),
    preset: str = Form("default"),
    language: str = Form("zh"),
    auto_summary: bool = Form(True),
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
        language=language,
        auto_summary=auto_summary,
    )
    db.add(recording)
    db.commit()
    db.refresh(recording)

    background_tasks.add_task(process_recording, recording.id, str(stored_path), preset, language)

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


@router.post("/{recording_id}/summarize", response_model=RecordingOut)
async def summarize_recording(
    recording_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """为已有转写的记录手动生成（或重新生成）总结。"""
    recording = db.get(Recording, recording_id)
    if recording is None or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="记录不存在")
    if recording.status == RecordingStatus.RECORDING:
        raise HTTPException(status_code=400, detail="录制尚未结束，无法生成总结")
    if not recording.transcript_text:
        raise HTTPException(status_code=400, detail="该记录还没有转写文本，无法生成总结")

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
    language: str = Form("zh"),
    auto_summary: bool = Form(True),
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
        language=language,
        auto_summary=auto_summary,
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
        {"type": "transcript_full", "text": "..."}       全量转写文本（周期性整体刷新，含对前文的修订）
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
        # 同一记录只允许一个录制连接（双开会导致音频互相截断、转写交叉）
        if recording_id in _active_live_recordings:
            await websocket.close(code=4409, reason="该记录已在另一连接中录制")
            return
        _active_live_recordings.add(recording_id)
        # 重连恢复（页面刷新后续录）：预先取分段小结的最大 seq，供新会话顺延编号
        nonlocal_max_seq = (
            db.query(func.max(SegmentSummary.seq)).filter(SegmentSummary.recording_id == recording.id).scalar()
        )
    finally:
        db.close()

    audio_path = Path(settings.storage_dir) / recording.filename
    # 语言与热词预设由前端在录制开始前选定，通过 WS 查询参数传入
    preset = websocket.query_params.get("preset", "default")
    language = recording.language or "zh"
    # 重连恢复（页面刷新后续录）：播种历史转写与分段序号，新会话只转写恢复后的音频。
    # session_tag 每个连接都唯一：首连接也用独立文件名，避免重连时（转写文本
    # 尚未生成）以 "wb" 截断已接收的音频。首连接 transcript_text 为空，播种无副作用。
    session_tag = f"-s{recording_id}-{uuid.uuid4().hex[:8]}"
    session = LiveSession(
        recording_id,
        audio_path,
        preset=preset,
        language=language,
        initial_text=recording.transcript_text or "",
        segment_seq_start=(nonlocal_max_seq + 1) if nonlocal_max_seq is not None else 0,
        # 每次连接使用独立 raw 文件与音频分片基名，互不干扰
        session_tag=session_tag,
    )
    transcribe_task: asyncio.Task | None = None
    explicit_stop = False
    paused = False

    async def run_transcribe_loop():
        while True:
            await asyncio.sleep(settings.live_transcribe_interval_seconds)
            try:
                delta = await session.transcribe_increment()
            except Exception:
                logger.exception("实时转写录音 %s 增量失败", recording_id)
                continue
            # 每轮无条件落库刷新 updated_at：课堂静音时转写文本无新增，
            # 若不刷新会被僵死清理（10 分钟阈值）误判为异常中断
            _persist_transcript(recording_id, session.transcript_text)
            if delta:
                # 以全量文本刷新前端：重新转写可能修正更早的识别结果，
                # 整体替换才能呈现“逐字更新 + 修订”的效果
                try:
                    await websocket.send_json({"type": "transcript_full", "text": session.transcript_text})
                except Exception:
                    logger.exception("实时转写录音 %s 推送转写失败", recording_id)
                if session.should_trigger_segment():
                    record = await generate_segment_summary(session)
                    if record is not None:
                        session.mark_segment_done()
                        try:
                            await websocket.send_json(
                                {"type": "segment_summary", "seq": record.seq, "text": record.text}
                            )
                        except Exception:
                            logger.exception("实时转写录音 %s 推送分段小结失败", recording_id)

    try:
        transcribe_task = asyncio.create_task(run_transcribe_loop())
        while True:
            try:
                # 60 秒超时容错：客户端心跳间隔 30 秒，超时不意味着断连，继续等待
                message = await asyncio.wait_for(websocket.receive(), timeout=60.0)
            except asyncio.TimeoutError:
                continue  # 客户端可能切后台暂时无数据，保持连接不断开
            if message["type"] == "websocket.disconnect":
                break
            if "bytes" in message and message["bytes"] is not None:
                # 暂停期间不应有音频块到达，防御性忽略
                if not paused:
                    session.write_chunk(message["bytes"])
            elif "text" in message and message["text"] == "stop":
                explicit_stop = True
                break
            elif "text" in message and message["text"]:
                try:
                    data = json.loads(message["text"])
                except (ValueError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("type") == "pause":
                    paused = True
                    session.pause()
                    _set_paused(recording_id, True)
                    continue
                if data.get("type") == "resume":
                    paused = False
                    session.resume()
                    _set_paused(recording_id, False)
                    continue
                # 原生 STT 模式：客户端识别出文本后以 JSON 文本帧推送 {"type":"stt_text","text":"..."}
                # 服务器只做持久化 + 分段小结 + 整课总结，不做 Whisper 转写、不写音频
                if data.get("type") != "stt_text":
                    continue
                text = (data.get("text") or "").strip()
                if not text:
                    continue
                session.append_text(text + " ")
                _persist_transcript(recording_id, session.transcript_text)
                if session.should_trigger_segment():
                    record = await generate_segment_summary(session)
                    if record is not None:
                        session.mark_segment_done()
                        try:
                            await websocket.send_json(
                                {"type": "segment_summary", "seq": record.seq, "text": record.text}
                            )
                        except Exception:
                            logger.exception("实时转写录音 %s 推送分段小结失败", recording_id)
            # 忽略客户端心跳空字符串
    except WebSocketDisconnect:
        pass
    finally:
        _active_live_recordings.discard(recording_id)
        if transcribe_task is not None:
            transcribe_task.cancel()
            # 必须等转写任务真正退出：cancel 不能中断已进入 to_thread 的解码线程，
            # 不等待的话它会与下面的收尾转写并发写同一 raw 文件导致 PCM 损坏
            try:
                await transcribe_task
            except (asyncio.CancelledError, Exception):
                pass
        # 结束前再跑一次转写，确保收尾的音频片段也被识别（需在文件关闭前执行）
        try:
            delta = await session.transcribe_increment()
            if delta:
                _persist_transcript(recording_id, session.transcript_text)
        except Exception:
            logger.exception("实时转写录音 %s 收尾转写失败", recording_id)
        session.close_file()
        # 会话级 raw PCM 与临时窗口文件在收尾转写后不再需要，及时清理防磁盘增长
        session.delete_raw_files()

        try:
            await websocket.send_json({"type": "done"})
        except Exception:
            pass
        # 暂停状态下断开（如页面刷新）：保留 RECORDING 状态等待用户恢复或结束，
        # 不做收尾总结；显式 stop 才正常收尾。
        if explicit_stop or not paused:
            await finalize_live_recording(recording_id)
        try:
            await websocket.close()
        except Exception:
            pass


def _set_paused(recording_id: int, value: bool) -> None:
    db = SessionLocal()
    try:
        recording = db.get(Recording, recording_id)
        if recording is not None:
            recording.is_paused = value
            db.add(recording)
            db.commit()
    finally:
        db.close()


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
    filename = recording.filename
    if recording.record_type == "homework":
        db.query(HomeworkTask).filter(HomeworkTask.recording_id == recording.id).delete()
        db.query(HomeworkExtractionJob).filter(
            HomeworkExtractionJob.result_recording_id == recording.id
        ).update({HomeworkExtractionJob.result_recording_id: None})
    db.delete(recording)
    db.commit()
    # 数据库行删除后清理其全部音频/分片/PCM 文件
    remove_recording_files(filename)
    return {"ok": True}
