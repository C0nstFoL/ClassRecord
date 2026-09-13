"""应用内更新服务：App 启动时检查服务器上的最新 APK 版本。

使用方式（服务器端）：
1. 把新 APK 放入 backend/data/apk/（如 classrecord-1.1.0.apk）；
2. 同目录创建/更新 version.json：
   {
     "version_name": "1.1.0",
     "version_code": 5,
     "apk_filename": "classrecord-1.1.0.apk",
     "changelog": "新增xx功能；修复xx问题",
     "force": false
   }
App 凭 versionCode 数字比较判断是否有更新（versionCode 与发布 tag 同步递增）。
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(prefix="/api/app", tags=["app-update"])
settings = get_settings()
logger = logging.getLogger(__name__)


@router.get("/check-update")
async def check_update(current_version_code: int = 0):
    """免登录接口：App 启动时携带本地 versionCode 查询是否有新版本。"""
    meta_path = Path(settings.apk_dir) / "version.json"
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
    except FileNotFoundError:
        return {"has_update": False}
    except Exception:
        logger.exception("解析 version.json 失败: %s", meta_path)
        return {"has_update": False}

    latest = int(meta.get("version_code", 0))
    filename = meta.get("apk_filename", "")
    return {
        "has_update": latest > current_version_code,
        "version_name": meta.get("version_name", ""),
        "version_code": latest,
        "apk_url": f"/downloads/apk/{filename}" if filename else None,
        "changelog": meta.get("changelog", ""),
        "force": bool(meta.get("force", False)),
    }
