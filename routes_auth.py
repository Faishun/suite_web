from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from suite_web.auth.deps import get_db_session
from suite_web.auth.passwords import verify_password
from suite_web.auth.sessions import clear_session, set_session_user_id
from suite_web.templating import template_context_base
from suite_web.web import flash, redirect


router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, session: Session = Depends(get_db_session)):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "Login"
    return templates.TemplateResponse("auth/login.html", ctx)


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_db_session),
):
    from suite_web.models import User

    user = session.exec(select(User).where(User.username == username)).first()
    if user is None or not verify_password(password, user.password_hash):
        flash(request, "Invalid username or password", level="error")
        return redirect("/login")

    set_session_user_id(request, user.id or 0)
    return redirect("/")


@router.get("/logout")
def logout(request: Request):
    clear_session(request)
    return redirect("/login")

