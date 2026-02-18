from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from suite_web.augustus_custom_template_gen import AugustusTemplateSpec, validate_template_id, write_template_file
from suite_web.auth.deps import get_db_session, require_user
from suite_web.probe_catalog import list_augustus_detectors
from suite_web.models import CustomAugustusTemplate, User
from suite_web.templating import template_context_base
from suite_web.web import flash, redirect


router = APIRouter(prefix="/augustus")


@router.get("/custom", response_class=HTMLResponse)
def augustus_custom_list(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "Custom Augustus probes"
    ctx["templates"] = session.exec(
        select(CustomAugustusTemplate)
        .where(CustomAugustusTemplate.owner_user_id == user.id)
        .order_by(CustomAugustusTemplate.updated_at.desc())
    ).all()
    return templates.TemplateResponse("augustus/custom_list.html", ctx)


@router.get("/custom/new", response_class=HTMLResponse)
def augustus_custom_new_page(
    request: Request,
    _: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    settings = request.app.state.settings  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "New Augustus template"
    ctx["tmpl"] = None
    detectors, _ = list_augustus_detectors(settings)
    ctx["augustus_detectors"] = detectors
    return templates.TemplateResponse("augustus/custom_edit.html", ctx)


@router.post("/custom/new")
def augustus_custom_new_submit(
    request: Request,
    template_id: str = Form(...),
    name: str = Form(...),
    author: str = Form("suite_web"),
    description: str = Form(""),
    goal: str = Form(""),
    detector: str = Form("always.Always"),
    severity: str = Form("info"),
    tags_csv: str = Form(""),
    prompts_text: str = Form(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    try:
        tid = validate_template_id(template_id)
    except Exception as e:
        flash(request, f"Invalid template id: {e}", level="error")
        return redirect("/augustus/custom/new")

    tmpl = CustomAugustusTemplate(
        owner_user_id=user.id or 0,
        template_id=tid,
        name=name.strip(),
        author=author.strip() or "suite_web",
        description=description.strip(),
        goal=goal.strip(),
        detector=detector.strip() or "always.Always",
        severity=severity.strip() or "info",
        tags_csv=tags_csv.strip(),
        prompts_text=prompts_text.strip(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(tmpl)
    session.commit()

    _write_yaml(request, user, tmpl)
    flash(request, "Custom Augustus template created", level="success")
    return redirect("/augustus/custom")


@router.get("/custom/{tmpl_id}/edit", response_class=HTMLResponse)
def augustus_custom_edit_page(
    request: Request,
    tmpl_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    tmpl = session.exec(
        select(CustomAugustusTemplate).where(CustomAugustusTemplate.id == tmpl_id, CustomAugustusTemplate.owner_user_id == user.id)
    ).first()
    if tmpl is None:
        flash(request, "Template not found", level="error")
        return redirect("/augustus/custom")

    settings = request.app.state.settings  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "Edit Augustus template"
    ctx["tmpl"] = tmpl
    detectors, _ = list_augustus_detectors(settings)
    ctx["augustus_detectors"] = detectors
    return templates.TemplateResponse("augustus/custom_edit.html", ctx)


@router.post("/custom/{tmpl_id}/edit")
def augustus_custom_edit_submit(
    request: Request,
    tmpl_id: int,
    template_id: str = Form(...),
    name: str = Form(...),
    author: str = Form("suite_web"),
    description: str = Form(""),
    goal: str = Form(""),
    detector: str = Form("always.Always"),
    severity: str = Form("info"),
    tags_csv: str = Form(""),
    prompts_text: str = Form(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    tmpl = session.exec(
        select(CustomAugustusTemplate).where(CustomAugustusTemplate.id == tmpl_id, CustomAugustusTemplate.owner_user_id == user.id)
    ).first()
    if tmpl is None:
        flash(request, "Template not found", level="error")
        return redirect("/augustus/custom")

    try:
        tid = validate_template_id(template_id)
    except Exception as e:
        flash(request, f"Invalid template id: {e}", level="error")
        return redirect(f"/augustus/custom/{tmpl_id}/edit")

    tmpl.template_id = tid
    tmpl.name = name.strip()
    tmpl.author = author.strip() or "suite_web"
    tmpl.description = description.strip()
    tmpl.goal = goal.strip()
    tmpl.detector = detector.strip() or "always.Always"
    tmpl.severity = severity.strip() or "info"
    tmpl.tags_csv = tags_csv.strip()
    tmpl.prompts_text = prompts_text.strip()
    tmpl.updated_at = datetime.utcnow()
    session.add(tmpl)
    session.commit()

    _write_yaml(request, user, tmpl)
    flash(request, "Template saved", level="success")
    return redirect("/augustus/custom")


def _write_yaml(request: Request, user: User, tmpl: CustomAugustusTemplate) -> None:
    base = Path(request.app.state.settings.custom_augustus_templates_dir) / str(user.id)  # type: ignore[attr-defined]
    tags = [t.strip() for t in (tmpl.tags_csv or "").split(",") if t.strip()]
    prompts = [p.strip() for p in (tmpl.prompts_text or "").splitlines() if p.strip()]
    spec = AugustusTemplateSpec(
        template_id=tmpl.template_id,
        name=tmpl.name,
        author=tmpl.author,
        description=tmpl.description,
        goal=tmpl.goal,
        detector=tmpl.detector,
        severity=tmpl.severity.lower(),
        tags=tags,
        prompts=prompts,
    )
    # Use db id in filename to avoid collisions.
    filename = f"custom_{tmpl.id or 0}"
    write_template_file(base, filename, spec)

