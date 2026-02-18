from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from suite_web.auth.deps import get_db_session, require_admin, require_user
from suite_web.probe_catalog import list_garak_detectors
from suite_web.garak_custom_probe_gen import (
    PromptOnlyProbeSpec,
    probe_module_path as _probe_module_path_from_gen,
    safe_module_name,
    validate_class_name,
    write_probe_module,
)
from suite_web.models import CustomGarakProbe, User
from suite_web.templating import template_context_base
from suite_web.web import flash, redirect


router = APIRouter(prefix="/garak")

_CACHE = {"ts": 0.0, "text": "", "error": ""}


@router.get("/probes", response_class=HTMLResponse)
def garak_probes_builtin(
    request: Request,
    _: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "Garak probes"

    now = time.time()
    if now - _CACHE["ts"] > 30:
        try:
            # Best-effort: list probes installed in the server python env.
            out = subprocess.check_output([sys.executable, "-m", "garak", "--list_probes"], stderr=subprocess.STDOUT, text=True)
            _CACHE.update({"ts": now, "text": out, "error": ""})
        except Exception as e:
            _CACHE.update({"ts": now, "text": "", "error": f"Failed to run garak: {e!r}"})

    ctx["probes_text"] = _CACHE["text"]
    ctx["error"] = _CACHE["error"]
    return templates.TemplateResponse("garak/probes.html", ctx)


@router.get("/custom", response_class=HTMLResponse)
def garak_custom_list(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "Custom probes"
    ctx["allow_admin_probe_code"] = bool(request.app.state.settings.allow_admin_probe_code)  # type: ignore[attr-defined]
    ctx["probes"] = session.exec(
        select(CustomGarakProbe).where(CustomGarakProbe.owner_user_id == user.id).order_by(CustomGarakProbe.updated_at.desc())
    ).all()
    return templates.TemplateResponse("garak/custom_list.html", ctx)


@router.get("/custom/new", response_class=HTMLResponse)
def garak_custom_new_page(
    request: Request,
    _: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "New custom probe"
    ctx["probe"] = None
    detectors, _ = list_garak_detectors()
    ctx["garak_detectors"] = detectors
    return templates.TemplateResponse("garak/custom_edit.html", ctx)


@router.post("/custom/new")
def garak_custom_new_submit(
    request: Request,
    title: str = Form(...),
    module_name: str = Form(""),
    class_name: str = Form("CustomProbe"),
    doc_uri: str = Form(""),
    goal: str = Form(""),
    tags_csv: str = Form(""),
    primary_detector: str = Form("always.Fail"),
    active: str | None = Form(None),
    prompts_text: str = Form(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    mn = (module_name or "").strip() or safe_module_name(title)
    cn = validate_class_name(class_name)

    probe = CustomGarakProbe(
        owner_user_id=user.id or 0,
        module_name=mn,
        class_name=cn,
        title=title.strip(),
        doc_uri=doc_uri.strip(),
        goal=goal.strip(),
        tags_csv=tags_csv.strip(),
        primary_detector=primary_detector.strip() or "always.Fail",
        active=(active == "1"),
        prompts_text=prompts_text.strip(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(probe)
    session.commit()

    _generate_probe_module(request, user, probe)
    flash(request, "Custom probe created", level="success")
    return redirect("/garak/custom")


@router.get("/custom/{probe_id}/edit", response_class=HTMLResponse)
def garak_custom_edit_page(
    request: Request,
    probe_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    probe = session.exec(
        select(CustomGarakProbe).where(CustomGarakProbe.id == probe_id, CustomGarakProbe.owner_user_id == user.id)
    ).first()
    if probe is None:
        flash(request, "Probe not found", level="error")
        return redirect("/garak/custom")

    ctx = template_context_base(request, session)
    ctx["title"] = "Edit custom probe"
    ctx["probe"] = probe
    detectors, _ = list_garak_detectors()
    ctx["garak_detectors"] = detectors
    return templates.TemplateResponse("garak/custom_edit.html", ctx)


@router.post("/custom/{probe_id}/edit")
def garak_custom_edit_submit(
    request: Request,
    probe_id: int,
    title: str = Form(...),
    module_name: str = Form(""),
    class_name: str = Form("CustomProbe"),
    doc_uri: str = Form(""),
    goal: str = Form(""),
    tags_csv: str = Form(""),
    primary_detector: str = Form("always.Fail"),
    active: str | None = Form(None),
    prompts_text: str = Form(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    probe = session.exec(
        select(CustomGarakProbe).where(CustomGarakProbe.id == probe_id, CustomGarakProbe.owner_user_id == user.id)
    ).first()
    if probe is None:
        flash(request, "Probe not found", level="error")
        return redirect("/garak/custom")

    probe.title = title.strip()
    probe.module_name = (module_name or "").strip() or safe_module_name(title)
    probe.class_name = validate_class_name(class_name)
    probe.doc_uri = doc_uri.strip()
    probe.goal = goal.strip()
    probe.tags_csv = tags_csv.strip()
    probe.primary_detector = primary_detector.strip() or "always.Fail"
    probe.active = (active == "1")
    probe.prompts_text = prompts_text.strip()
    probe.updated_at = datetime.utcnow()
    session.add(probe)
    session.commit()

    _generate_probe_module(request, user, probe)
    flash(request, "Custom probe saved", level="success")
    return redirect("/garak/custom")


@router.get("/custom/{probe_id}/code", response_class=HTMLResponse)
def garak_custom_code_page(
    request: Request,
    probe_id: int,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
):
    if not bool(request.app.state.settings.allow_admin_probe_code):  # type: ignore[attr-defined]
        raise HTTPException(status_code=404, detail="Not found")

    probe = session.exec(select(CustomGarakProbe).where(CustomGarakProbe.id == probe_id)).first()
    if probe is None:
        raise HTTPException(status_code=404, detail="Probe not found")

    module_path = _probe_module_path(request, probe.owner_user_id, probe.module_name)
    code = module_path.read_text(encoding="utf-8") if module_path.exists() else ""

    templates = request.app.state.templates  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "Custom probe code"
    ctx["probe"] = probe
    ctx["module_path"] = str(module_path)
    ctx["code"] = code
    return templates.TemplateResponse("garak/custom_code.html", ctx)


@router.post("/custom/{probe_id}/code")
def garak_custom_code_submit(
    request: Request,
    probe_id: int,
    code: str = Form(""),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db_session),
):
    if not bool(request.app.state.settings.allow_admin_probe_code):  # type: ignore[attr-defined]
        raise HTTPException(status_code=404, detail="Not found")

    probe = session.exec(select(CustomGarakProbe).where(CustomGarakProbe.id == probe_id)).first()
    if probe is None:
        raise HTTPException(status_code=404, detail="Probe not found")

    module_path = _probe_module_path(request, probe.owner_user_id, probe.module_name)
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(code, encoding="utf-8")
    probe.updated_at = datetime.utcnow()
    session.add(probe)
    session.commit()

    flash(request, "Code saved", level="success")
    return redirect("/garak/custom")


def _probe_module_path(request: Request, owner_user_id: int, module_name: str) -> Path:
    base = Path(request.app.state.settings.custom_probes_dir)  # type: ignore[attr-defined]
    target_dir = base / str(owner_user_id)
    return _probe_module_path_from_gen(target_dir, module_name)


def _generate_probe_module(request: Request, user: User, probe: CustomGarakProbe) -> None:
    target_dir = Path(request.app.state.settings.custom_probes_dir) / str(user.id)  # type: ignore[attr-defined]
    tags = [t.strip() for t in (probe.tags_csv or "").split(",") if t.strip()]
    prompts = [p.strip() for p in (probe.prompts_text or "").splitlines() if p.strip()]
    spec = PromptOnlyProbeSpec(
        module_name=probe.module_name,
        class_name=probe.class_name,
        title=probe.title,
        doc_uri=probe.doc_uri,
        goal=probe.goal,
        tags=tags,
        primary_detector=probe.primary_detector,
        active=probe.active,
        prompts=prompts,
    )
    write_probe_module(target_dir, spec)

