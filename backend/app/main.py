import logging
import json
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.routers import app_update as app_update_router
from app.routers import auth as auth_router
from app.routers import recordings as recordings_router
from app.routers import share as share_router

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            settings.log_file_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        ),
    ],
)

Base.metadata.create_all(bind=engine)


def _ensure_sqlite_columns() -> None:
    """轻量迁移：为旧库补齐新增列（create_all 不会修改已存在的表）。"""
    if not settings.database_url.startswith("sqlite"):
        return
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "recordings" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("recordings")}
    with engine.begin() as conn:
        if "language" not in columns:
            conn.execute(text("ALTER TABLE recordings ADD COLUMN language VARCHAR(8) DEFAULT 'zh'"))
        if "share_token" not in columns:
            conn.execute(text("ALTER TABLE recordings ADD COLUMN share_token VARCHAR(64)"))
        if "share_expires_at" not in columns:
            conn.execute(text("ALTER TABLE recordings ADD COLUMN share_expires_at DATETIME"))
        if "is_paused" not in columns:
            conn.execute(text("ALTER TABLE recordings ADD COLUMN is_paused BOOLEAN DEFAULT 0"))
        if "auto_summary" not in columns:
            conn.execute(text("ALTER TABLE recordings ADD COLUMN auto_summary BOOLEAN DEFAULT 1"))
        if "record_type" not in columns:
            conn.execute(
                text("ALTER TABLE recordings ADD COLUMN record_type VARCHAR(16) DEFAULT 'class'")
            )

    if "homework_extraction_jobs" not in inspector.get_table_names():
        return
    job_columns = {c["name"] for c in inspector.get_columns("homework_extraction_jobs")}
    with engine.begin() as conn:
        if "result_recording_id" not in job_columns:
            conn.execute(
                text("ALTER TABLE homework_extraction_jobs ADD COLUMN result_recording_id INTEGER")
            )


def _fail_stale_recordings() -> None:
    """把僵死的实时录制行标记为失败：App 被直接杀掉时 WebSocket 收尾不会执行，
    status 会永远停在 recording，导致所有设备都显示「正在由其他设备录制」。
    正常录制中的记录每 3 秒都会持久化转写并刷新 updated_at，不会命中 10 分钟阈值。
    暂停中的记录（is_paused=1）没有新转写落库属正常现象，跳过清理，等待用户
    恢复或手动结束。
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE recordings SET status = 'FAILED', error_message = '录制会话异常中断' "
                "WHERE status = 'RECORDING' AND (is_paused IS NULL OR is_paused = 0) "
                "AND updated_at < datetime('now', '-600 seconds')"
            )
        )
        if result.rowcount:
            logging.getLogger(__name__).warning("已清理 %s 条僵死的录制状态记录", result.rowcount)


_ensure_sqlite_columns()


def _backfill_homework_recordings() -> None:
    """把升级前已完成的整理结果幂等转换为带标签的记录。"""
    from app.models import HomeworkExtractionJob, Recording, RecordingStatus

    with SessionLocal() as db:
        jobs = (
            db.query(HomeworkExtractionJob)
            .filter(
                HomeworkExtractionJob.status == "completed",
                HomeworkExtractionJob.homework.is_not(None),
                HomeworkExtractionJob.result_recording_id.is_(None),
            )
            .all()
        )
        for job in jobs:
            try:
                source_count = len(json.loads(job.recording_names_json))
            except (TypeError, ValueError):
                source_count = 0
            title = (
                f"待办与作业 · {source_count} 节课"
                if source_count
                else "待办与作业"
            )
            recording = Recording(
                user_id=job.user_id,
                title=title,
                filename=f"homework-{job.id}.md",
                status=RecordingStatus.COMPLETED,
                summary_text=job.homework,
                record_type="homework",
                is_live=False,
                is_paused=False,
                auto_summary=False,
                language="zh",
                created_at=job.created_at,
            )
            db.add(recording)
            db.flush()
            job.result_recording_id = recording.id
        if jobs:
            db.commit()
            logging.getLogger(__name__).info("已回填 %s 条待办作业记录", len(jobs))


_backfill_homework_recordings()


def _backfill_homework_tasks() -> None:
    """把旧版待办作业记录中的 Markdown 复选项转换为结构化待办。"""
    from app.models import HomeworkTask, Recording

    task_pattern = re.compile(r"^- \[([ xX])\] \*\*(.+?)\*\*(?: — (.+))?$")
    with SessionLocal() as db:
        recordings_with_tasks = {
            recording_id for (recording_id,) in db.query(HomeworkTask.recording_id).distinct()
        }
        homework_recordings = (
            db.query(Recording)
            .filter(Recording.record_type == "homework")
            .all()
        )
        created_count = 0
        for recording in homework_recordings:
            if recording.id in recordings_with_tasks or not recording.summary_text:
                continue
            lines = recording.summary_text.splitlines()
            for line_index, line in enumerate(lines):
                match = task_pattern.match(line)
                if match is None:
                    continue
                completed_mark, content, source_label = match.groups()
                deadline = None
                details = None
                for metadata_line in lines[line_index + 1 : line_index + 3]:
                    stripped = metadata_line.strip()
                    if stripped.startswith("- 截止时间："):
                        value = stripped.removeprefix("- 截止时间：").strip()
                        deadline = value if value and value != "未注明" else None
                    elif stripped.startswith("- 要求："):
                        value = stripped.removeprefix("- 要求：").strip()
                        details = value if value and value != "未注明" else None
                source_names = [name.strip() for name in (source_label or "").split("、") if name.strip()]
                source_ids = [
                    source.id
                    for source in db.query(Recording)
                    .filter(
                        Recording.user_id == recording.user_id,
                        Recording.record_type == "class",
                        Recording.title.in_(source_names),
                    )
                    .order_by(Recording.created_at)
                    .all()
                ] if source_names else []
                db.add(
                    HomeworkTask(
                        recording_id=recording.id,
                        content=content.strip(),
                        deadline=deadline,
                        details=details,
                        source_recording_ids_json=json.dumps(source_ids),
                        completed=completed_mark.lower() == "x",
                        sort_order=created_count,
                    )
                )
                created_count += 1
        if created_count:
            db.commit()
            logging.getLogger(__name__).info("已回填 %s 条结构化待办", created_count)


_backfill_homework_tasks()


def _backfill_homework_task_sources() -> None:
    """用任务保存的课程标题为旧待办补齐来源记录链接。"""
    from app.models import HomeworkExtractionJob, HomeworkTask, Recording

    task_pattern = re.compile(r"^- \[[ xX]\] \*\*(.+?)\*\*(?: — (.+))?$")
    updated_count = 0
    with SessionLocal() as db:
        jobs = (
            db.query(HomeworkExtractionJob)
            .filter(HomeworkExtractionJob.result_recording_id.is_not(None))
            .all()
        )
        for job in jobs:
            recording = db.get(Recording, job.result_recording_id)
            if recording is None or not recording.summary_text:
                continue
            try:
                saved_source_titles = json.loads(job.recording_names_json)
            except (TypeError, ValueError):
                saved_source_titles = []
            task_source_labels = [
                match.group(2) or ""
                for line in recording.summary_text.splitlines()
                if (match := task_pattern.match(line)) is not None
            ]
            tasks = (
                db.query(HomeworkTask)
                .filter(HomeworkTask.recording_id == recording.id)
                .order_by(HomeworkTask.sort_order, HomeworkTask.id)
                .all()
            )
            class_recordings = (
                db.query(Recording)
                .filter(
                    Recording.user_id == recording.user_id,
                    Recording.record_type == "class",
                )
                .all()
            )
            recordings_by_title = {item.title: item.id for item in class_recordings}
            for task, source_label in zip(tasks, task_source_labels, strict=False):
                try:
                    if json.loads(task.source_recording_ids_json):
                        continue
                except (TypeError, ValueError):
                    pass
                labels = [part.strip() for part in source_label.split("、") if part.strip()]
                matched_titles = [
                    title
                    for title in saved_source_titles
                    if any(
                        title == label or title.startswith(label) or label.startswith(title)
                        for label in labels
                    )
                ]
                source_ids = [
                    recordings_by_title[title]
                    for title in matched_titles
                    if title in recordings_by_title
                ]
                if source_ids:
                    task.source_recording_ids_json = json.dumps(source_ids)
                    db.add(task)
                    updated_count += 1
        if updated_count:
            db.commit()
            logging.getLogger(__name__).info("已补齐 %s 条待办的课程来源链接", updated_count)


_backfill_homework_task_sources()


def _backfill_homework_titles() -> None:
    """为旧版通用标题的待办记录生成基于内容的标题。"""
    from app.models import HomeworkTask, Recording

    generic_title = re.compile(r"^待办与作业(?: · \d+ 节课)?$")
    updated_count = 0
    with SessionLocal() as db:
        recordings = db.query(Recording).filter(Recording.record_type == "homework").all()
        for recording in recordings:
            if not generic_title.match(recording.title):
                continue
            tasks = (
                db.query(HomeworkTask)
                .filter(HomeworkTask.recording_id == recording.id)
                .order_by(HomeworkTask.sort_order, HomeworkTask.id)
                .all()
            )
            if not tasks:
                continue
            recording.title = (
                tasks[0].content[:60]
                if len(tasks) == 1
                else f"{tasks[0].content[:36]}等 {len(tasks)} 项待办"
            )
            db.add(recording)
            updated_count += 1
        if updated_count:
            db.commit()
            logging.getLogger(__name__).info("已更新 %s 条待办作业记录标题", updated_count)


_backfill_homework_titles()
_fail_stale_recordings()

import asyncio

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_: FastAPI):
    """每 5 分钟周期性清理僵死的录制状态行（App 异常退出时 WS 收尾不会执行）。"""
    task = asyncio.create_task(_stale_recording_sweeper())
    yield
    task.cancel()


async def _stale_recording_sweeper() -> None:
    while True:
        await asyncio.sleep(300)
        try:
            _fail_stale_recordings()
        except Exception:
            logging.getLogger(__name__).exception("周期清理僵死录制记录失败")


app = FastAPI(title="ClassRecord", lifespan=lifespan)

# 反向代理部署在 HTTPS 之后，cookie 需要 secure=True 且 samesite=lax 以支持 OIDC 回跳。
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret_key,
    session_cookie=settings.session_cookie_name,
    max_age=settings.session_max_age,
    same_site="none",  # Android App WebView 跨站 OIDC 回跳需携带 session cookie，Lax 会被 WebView 丢弃
    https_only=settings.cookie_secure,
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth_router.router)
app.include_router(recordings_router.router)
app.include_router(share_router.router)
app.include_router(app_update_router.router)

# App 安装包托管：backend/data/apk/ 下的 APK 通过 /downloads/apk/{filename} 下载
apk_dir = Path(settings.apk_dir)
apk_dir.mkdir(parents=True, exist_ok=True)
app.mount("/downloads/apk", StaticFiles(directory=apk_dir), name="apk")

frontend_dist = Path(__file__).resolve().parent.parent / "static"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # 前端 SPA 路由回退：非 /api、/auth 的路径统一返回 index.html，由前端路由处理。
        # 防路径穿越：解析后的真实路径必须仍位于静态目录内（拒绝绝对路径与 .. 上跳）
        candidate = (frontend_dist / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(frontend_dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")
