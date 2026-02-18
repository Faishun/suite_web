from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from suite_web.auth.deps import get_db_session, require_admin
from suite_web.auth.passwords import hash_password
from suite_web.models import User
from suite_web.templating import template_context_base
from suite_web.web import flash, redirect


router = APIRouter(prefix="/admin")


@router.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    _: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "Users"
    ctx["users"] = session.exec(select(User).order_by(User.created_at.desc())).all()
    return templates.TemplateResponse("admin/users.html", ctx)


@router.post("/users/create")
def users_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    is_admin: str | None = Form(None),
    _: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
):
    username = username.strip()
    if not username:
        flash(request, "Username required", level="error")
        return redirect("/admin/users")

    existing = session.exec(select(User).where(User.username == username)).first()
    if existing is not None:
        flash(request, "Username already exists", level="error")
        return redirect("/admin/users")

    user = User(
        username=username,
        password_hash=hash_password(password),
        is_admin=(is_admin == "1"),
    )
    session.add(user)
    session.commit()
    flash(request, f"Created user {username}", level="success")
    return redirect("/admin/users")

