from __future__ import annotations

from fastapi import Request

SESSION_USER_ID_KEY = "user_id"


def get_session_user_id(request: Request) -> int | None:
    raw = request.session.get(SESSION_USER_ID_KEY)  # type: ignore[attr-defined]
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def set_session_user_id(request: Request, user_id: int) -> None:
    request.session[SESSION_USER_ID_KEY] = int(user_id)  # type: ignore[attr-defined]


def clear_session(request: Request) -> None:
    request.session.clear()  # type: ignore[attr-defined]

