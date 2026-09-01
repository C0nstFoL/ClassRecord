from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    OIDC_STATE_SESSION_KEY,
    OIDC_VERIFIER_SESSION_KEY,
    USER_ID_SESSION_KEY,
    build_authorize_url,
    exchange_code_for_tokens,
    fetch_userinfo,
    get_current_user,
    get_or_create_user,
)
from app.config import get_settings
from app.database import get_db
from app.models import User
from app.schemas import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


@router.get("/login")
async def login(request: Request):
    url = await build_authorize_url(request)
    return RedirectResponse(url)


@router.get("/callback")
async def callback(request: Request, code: str, state: str, db: Session = Depends(get_db)):
    expected_state = request.session.get(OIDC_STATE_SESSION_KEY)
    verifier = request.session.get(OIDC_VERIFIER_SESSION_KEY)
    if not expected_state or state != expected_state or not verifier:
        raise HTTPException(status_code=400, detail="登录状态校验失败，请重新登录")

    tokens = await exchange_code_for_tokens(code, verifier)
    userinfo = await fetch_userinfo(tokens["access_token"])
    user = get_or_create_user(db, userinfo)

    request.session.pop(OIDC_STATE_SESSION_KEY, None)
    request.session.pop(OIDC_VERIFIER_SESSION_KEY, None)
    request.session[USER_ID_SESSION_KEY] = user.id

    return RedirectResponse(settings.frontend_after_login_url)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
