"""Zitadel OIDC 登录集成（Authorization Code + PKCE）。"""

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User

settings = get_settings()

OIDC_STATE_SESSION_KEY = "oidc_state"
OIDC_VERIFIER_SESSION_KEY = "oidc_verifier"
USER_ID_SESSION_KEY = "user_id"


class OIDCDiscovery:
    """缓存 Zitadel 的 OIDC discovery 文档。"""

    _document: dict | None = None

    @classmethod
    async def get(cls) -> dict:
        if cls._document is None:
            issuer = settings.zitadel_issuer.rstrip("/")
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{issuer}/.well-known/openid-configuration")
                resp.raise_for_status()
                cls._document = resp.json()
        return cls._document


def generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8").rstrip("=")
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


async def build_authorize_url(request: Request) -> str:
    document = await OIDCDiscovery.get()
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(24)

    request.session[OIDC_STATE_SESSION_KEY] = state
    request.session[OIDC_VERIFIER_SESSION_KEY] = verifier

    params = {
        "client_id": settings.zitadel_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "response_type": "code",
        "scope": "openid profile email",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{document['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code_for_tokens(code: str, verifier: str) -> dict:
    document = await OIDCDiscovery.get()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.zitadel_client_id,
        "code_verifier": verifier,
    }
    auth = None
    if settings.zitadel_client_secret:
        auth = (settings.zitadel_client_id, settings.zitadel_client_secret)

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(document["token_endpoint"], data=data, auth=auth)
        resp.raise_for_status()
        return resp.json()


async def fetch_userinfo(access_token: str) -> dict:
    document = await OIDCDiscovery.get()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            document["userinfo_endpoint"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def get_or_create_user(db: Session, userinfo: dict) -> User:
    sub = userinfo["sub"]
    user = db.query(User).filter(User.sub == sub).one_or_none()
    if user is None:
        user = User(sub=sub, email=userinfo.get("email"), name=userinfo.get("name"))
        db.add(user)
    else:
        user.email = userinfo.get("email")
        user.name = userinfo.get("name")
    db.commit()
    db.refresh(user)
    return user


def _get_or_create_dev_user(db: Session) -> User:
    """DISABLE_AUTH=true 时使用的本地测试用户，仅用于跳过登录调试。"""
    sub = "dev-local-test-user"
    user = db.query(User).filter(User.sub == sub).one_or_none()
    if user is None:
        user = User(sub=sub, email="dev@local.test", name="本地测试用户")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    if settings.disable_auth:
        return _get_or_create_dev_user(db)
    user_id = request.session.get(USER_ID_SESSION_KEY)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user


def get_current_user_ws(websocket: WebSocket, db: Session) -> User | None:
    """WebSocket 场景下从 session cookie 中获取当前用户，鉴权失败返回 None（由调用方关闭连接）。"""
    if settings.disable_auth:
        return _get_or_create_dev_user(db)
    user_id = websocket.session.get(USER_ID_SESSION_KEY)
    if not user_id:
        return None
    return db.get(User, user_id)
