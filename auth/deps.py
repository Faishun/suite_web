from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from suite_web.auth.sessions import get_session_user_id
from suite_web.models import User


def get_db_session(request: Request):
    session: Session = request.app.state.db_session_factory()  # type: ignore[attr-defined]
    try:
        yield session
    finally:
        session.close()


def require_user(
    request: Request,
    session: Session = Depends(get_db_session),
) -> User:
    user_id = get_session_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = session.exec(select(User).where(User.id == user_id)).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    return user

