from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import Session, select

from suite_web.auth.deps import get_db_session, require_user
from suite_web.crypto import decrypt_str, encrypt_str
from suite_web.jsonutil import json_dumps
from suite_web.models import ModelProfile, ProviderKind, Run, User
from suite_web.templating import template_context_base
from suite_web.web import flash, redirect


router = APIRouter(prefix="/profiles")


@router.get("", response_class=HTMLResponse)
def profiles_list(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "Model profiles"
    profiles = session.exec(
        select(ModelProfile).where(ModelProfile.owner_user_id == user.id).order_by(ModelProfile.created_at.desc())
    ).all()
    ctx["profiles"] = profiles
    return templates.TemplateResponse("profiles/list.html", ctx)


@router.get("/new", response_class=HTMLResponse)
def profile_new_page(
    request: Request,
    _: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "New profile"
    ctx["profile"] = None
    ctx["provider_kinds"] = [k.value for k in ProviderKind]
    return templates.TemplateResponse("profiles/edit.html", ctx)


@router.post("/new")
def profile_new_submit(
    request: Request,
    name: str = Form(...),
    provider_kind: str = Form(...),
    model: str = Form(""),
    base_url: str = Form(""),
    api_key: str = Form(""),
    extra_json: str = Form("{}"),
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    try:
        pk = ProviderKind(provider_kind)
    except Exception:
        flash(request, "Invalid provider kind", level="error")
        return redirect("/profiles/new")

    # Validate extra_json is JSON.
    try:
        json.loads(extra_json or "{}")
    except Exception:
        flash(request, "Extra JSON is not valid JSON", level="error")
        return redirect("/profiles/new")

    api_key_enc = ""
    if api_key.strip():
        api_key_enc = encrypt_str(request.app.state.settings.master_key, api_key.strip())  # type: ignore[attr-defined]

    profile = ModelProfile(
        owner_user_id=user.id or 0,
        name=name.strip(),
        provider_kind=pk,
        model=model.strip(),
        base_url=base_url.strip(),
        api_key_enc=api_key_enc,
        extra_json=json_dumps(json.loads(extra_json or "{}")),
    )
    session.add(profile)
    session.commit()
    flash(request, "Profile created", level="success")
    return redirect("/profiles")


@router.get("/{profile_id}/edit", response_class=HTMLResponse)
def profile_edit_page(
    request: Request,
    profile_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    profile = session.exec(
        select(ModelProfile).where(ModelProfile.id == profile_id, ModelProfile.owner_user_id == user.id)
    ).first()
    if profile is None:
        flash(request, "Profile not found", level="error")
        return redirect("/profiles")

    ctx = template_context_base(request, session)
    ctx["title"] = "Edit profile"
    ctx["profile"] = profile
    ctx["provider_kinds"] = [k.value for k in ProviderKind]
    return templates.TemplateResponse("profiles/edit.html", ctx)


@router.get("/{profile_id}/api-key")
def profile_reveal_api_key(
    request: Request,
    profile_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    """Return decrypted API key for the profile (owner only). Used by edit page reveal button."""
    profile = session.exec(
        select(ModelProfile).where(ModelProfile.id == profile_id, ModelProfile.owner_user_id == user.id)
    ).first()
    if profile is None or not profile.api_key_enc:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    try:
        key = decrypt_str(request.app.state.settings.master_key, profile.api_key_enc)  # type: ignore[attr-defined]
    except Exception:
        return JSONResponse({"detail": "Could not decrypt key"}, status_code=500)
    return JSONResponse({"api_key": key})


@router.post("/{profile_id}/edit")
def profile_edit_submit(
    request: Request,
    profile_id: int,
    name: str = Form(...),
    provider_kind: str = Form(...),
    model: str = Form(""),
    base_url: str = Form(""),
    api_key: str = Form(""),
    extra_json: str = Form("{}"),
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    profile = session.exec(
        select(ModelProfile).where(ModelProfile.id == profile_id, ModelProfile.owner_user_id == user.id)
    ).first()
    if profile is None:
        flash(request, "Profile not found", level="error")
        return redirect("/profiles")

    try:
        pk = ProviderKind(provider_kind)
    except Exception:
        flash(request, "Invalid provider kind", level="error")
        return redirect(f"/profiles/{profile_id}/edit")

    try:
        parsed_extra = json.loads(extra_json or "{}")
    except Exception:
        flash(request, "Extra JSON is not valid JSON", level="error")
        return redirect(f"/profiles/{profile_id}/edit")

    profile.name = name.strip()
    profile.provider_kind = pk
    profile.model = model.strip()
    profile.base_url = base_url.strip()
    profile.extra_json = json_dumps(parsed_extra)
    # Always persist API key from form (empty = clear); ensures values like "lmstudio" are saved.
    profile.api_key_enc = (
        encrypt_str(request.app.state.settings.master_key, api_key.strip())  # type: ignore[attr-defined]
        if api_key.strip()
        else ""
    )

    session.add(profile)
    session.commit()
    flash(request, "Profile saved", level="success")
    return redirect("/profiles")


@router.post("/{profile_id}/delete")
def profile_delete(
    request: Request,
    profile_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    profile = session.exec(
        select(ModelProfile).where(ModelProfile.id == profile_id, ModelProfile.owner_user_id == user.id)
    ).first()
    if profile is None:
        flash(request, "Profile not found", level="error")
        return redirect("/profiles")
    # Unlink runs that use this profile
    runs = session.exec(select(Run).where(Run.model_profile_id == profile_id)).all()
    for run in runs:
        run.model_profile_id = None
        session.add(run)
    session.delete(profile)
    session.commit()
    flash(request, "Profile deleted", level="success")
    return redirect("/profiles")

