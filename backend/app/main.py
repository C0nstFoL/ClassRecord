import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse

from app.config import get_settings
from app.database import Base, engine
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
