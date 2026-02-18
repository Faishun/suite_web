from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from suite_web.auth.sessions import get_session_user_id
from suite_web.models import User


def create_templates(repo_root: Path) -> Jinja2Templates:
    return Jinja2Templates(directory=str(repo_root / "suite_web" / "templates"))


def pop_flashes(request: Request) -> list[dict[str, str]]:
    flashes = request.session.pop("_flashes", [])  # type: ignore[attr-defined]
    if not isinstance(flashes, list):
        return []
    # Defensive filtering
    out: list[dict[str, str]] = []
    for f in flashes:
        if not isinstance(f, dict):
            continue
        msg = str(f.get("message", ""))
        lvl = str(f.get("level", "info"))
        out.append({"message": msg, "level": lvl})
    return out


def get_current_user(request: Request, session: Session) -> User | None:
    user_id = get_session_user_id(request)
    if user_id is None:
        return None
    return session.exec(select(User).where(User.id == user_id)).first()


def template_context_base(request: Request, session: Session) -> dict[str, Any]:
    return {
        "request": request,
        "current_user": get_current_user(request, session),
        "flashes": pop_flashes(request),
    }

