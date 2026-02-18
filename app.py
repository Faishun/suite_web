from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from suite_web.db import create_db_engine, ensure_dirs, init_db
from suite_web.routes_admin import router as admin_router
from suite_web.routes_auth import router as auth_router
from suite_web.routes_profiles import router as profiles_router
from suite_web.routes_runs import router as runs_router
from suite_web.routes_garak import router as garak_router
from suite_web.routes_augustus import router as augustus_router
from suite_web.settings import load_settings
from suite_web.templating import create_templates, template_context_base
from sqlmodel import Session


def _repo_root() -> Path:
    # This file lives at <repo>/suite_web/app.py
    return Path(__file__).resolve().parents[1]


def create_app() -> FastAPI:
    repo_root = _repo_root()
    settings = load_settings(repo_root)

    ensure_dirs(settings.artifacts_dir, settings.custom_probes_dir, settings.custom_augustus_templates_dir)

    engine = create_db_engine(settings.db_url)
    init_db(engine)

    app = FastAPI(title="LLM Security Suite")
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

    app.state.settings = settings
    app.state.engine = engine
    app.state.db_session_factory = lambda: Session(engine)  # type: ignore[attr-defined]
    app.state.templates = create_templates(repo_root)

    app.mount("/static", StaticFiles(directory=str(repo_root / "suite_web" / "static")), name="static")

    # Routers
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(profiles_router)
    app.include_router(runs_router)
    app.include_router(garak_router)
    app.include_router(augustus_router)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        templates = request.app.state.templates
        with Session(engine) as session:
            ctx = template_context_base(request, session)
            ctx["title"] = "Dashboard"
            # worker heartbeat indicator
            hb = settings.artifacts_dir / "_worker_heartbeat.txt"
            try:
                ctx["worker_heartbeat_mtime"] = hb.stat().st_mtime
            except Exception:
                ctx["worker_heartbeat_mtime"] = None
            return templates.TemplateResponse("home.html", ctx)

    @app.on_event("startup")
    def _bootstrap_admin():
        # Create an initial admin user if the DB is empty and env vars are set.
        from sqlmodel import Session, select

        from suite_web.auth.passwords import hash_password
        from suite_web.models import User

        with Session(engine) as session:
            any_user = session.exec(select(User.id)).first()
            if any_user is not None:
                return

            u = settings.bootstrap_admin_username
            p = settings.bootstrap_admin_password
            if not u or not p:
                if settings.app_env != "dev":
                    raise RuntimeError(
                        "No users exist yet. Set SUITE_WEB_BOOTSTRAP_ADMIN_USERNAME and "
                        "SUITE_WEB_BOOTSTRAP_ADMIN_PASSWORD to create the first admin."
                    )
                # Dev convenience default (still recommended to set env vars)
                u = "admin"
                p = "admin"

            user = User(username=u, password_hash=hash_password(p), is_admin=True)
            session.add(user)
            session.commit()
            # Avoid printing secrets; just a hint.
            print(f"[suite_web] Bootstrapped initial admin user: {u}")

    return app


app = create_app()


def main():
    # Support: python -m suite_web.app
    import uvicorn

    host = os.getenv("SUITE_WEB_HOST", "0.0.0.0")
    port = int(os.getenv("SUITE_WEB_PORT", "8080"))
    uvicorn.run("suite_web.app:app", host=host, port=port, reload=os.getenv("SUITE_WEB_RELOAD") == "1")


if __name__ == "__main__":
    main()

