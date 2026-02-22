from __future__ import annotations

import json
import os
import signal
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from sqlmodel import Session, select

from suite_web.agentdojo_catalog import get_agentdojo_catalog
from suite_web.auth.deps import get_db_session, require_user
from suite_web.jsonutil import json_dumps
from suite_web.models import Artifact, CustomAugustusTemplate, CustomGarakProbe, ModelProfile, Run, RunStatus, ToolKind, User
from suite_web.probe_catalog import (
    ProbeOption,
    get_augustus_probe_descriptions,
    get_garak_probe_descriptions,
    list_augustus_probes,
    list_garak_probes,
)
from suite_web.templating import template_context_base
from suite_web.web import flash, redirect


router = APIRouter(prefix="/runs")


def _read_log_tail(path: str, max_bytes: int = 64_000) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    size = p.stat().st_size
    start = max(size - max_bytes, 0)
    with p.open("rb") as f:
        f.seek(start)
        data = f.read()
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


@router.get("", response_class=HTMLResponse)
def runs_list(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    ctx = template_context_base(request, session)
    ctx["title"] = "Runs"
    ctx["runs"] = session.exec(
        select(Run).where(Run.owner_user_id == user.id).order_by(Run.created_at.desc())
    ).all()
    return templates.TemplateResponse("runs/list.html", ctx)


def _runs_new_full_context(request: Request, session: Session, user: User) -> dict:
    """Build full context for New run form (probes, agentdojo catalog). Used for fragment and legacy full page."""
    ctx = template_context_base(request, session)
    ctx["title"] = "New run"
    ctx["tool_kinds"] = [t.value for t in ToolKind]
    ctx["profiles"] = session.exec(
        select(ModelProfile).where(ModelProfile.owner_user_id == user.id).order_by(ModelProfile.created_at.desc())
    ).all()
    settings = request.app.state.settings  # type: ignore[attr-defined]
    garak_probes, garak_err = list_garak_probes()
    augustus_probes, augustus_err = list_augustus_probes(settings)
    garak_descriptions = get_garak_probe_descriptions(garak_probes) if garak_probes else {}
    augustus_descriptions = get_augustus_probe_descriptions(settings) if augustus_probes else {}

    def _probe_option(value: str, label: str, description: str = "") -> dict:
        return {"value": value, "label": label, "description": description or ""}

    custom = session.exec(
        select(CustomGarakProbe).where(CustomGarakProbe.owner_user_id == user.id).order_by(CustomGarakProbe.updated_at.desc())
    ).all()
    custom_opts = [
        _probe_option(f"{p.module_name}.{p.class_name}", f"{p.title} ({p.module_name}.{p.class_name})", p.title or "")
        for p in custom
    ]
    custom_aug = session.exec(
        select(CustomAugustusTemplate)
        .where(CustomAugustusTemplate.owner_user_id == user.id)
        .order_by(CustomAugustusTemplate.updated_at.desc())
    ).all()
    custom_aug_opts = [
        _probe_option(t.template_id, f"{t.name} ({t.template_id})", (t.description or t.goal or t.name or "").strip())
        for t in custom_aug
    ]

    def _builtin_opts(names: list[str], descriptions: dict) -> list[dict]:
        out = []
        for v in names:
            meta = descriptions.get(v) or {}
            desc = (meta.get("description") or meta.get("goal") or "").strip()
            out.append(_probe_option(v, v, desc))
        return out

    ctx["probe_groups"] = [
        {"label": "Custom probes (Garak)", "kind": "garak", "options": custom_opts},
        {"label": "Garak probes (built-in)", "kind": "garak", "options": _builtin_opts(garak_probes, garak_descriptions)},
        {"label": "Custom probes (Augustus)", "kind": "augustus", "options": custom_aug_opts},
        {"label": "Augustus probes (built-in)", "kind": "augustus", "options": _builtin_opts(augustus_probes, augustus_descriptions)},
    ]
    probe_error = ""
    if garak_err:
        probe_error += garak_err
    if augustus_err:
        probe_error += (("; " if probe_error else "") + augustus_err)
    ctx["probe_error"] = probe_error

    agentdojo_dir = getattr(settings, "agentdojo_dir", None) if settings else None
    ctx["agentdojo_catalog"] = get_agentdojo_catalog(agentdojo_dir)
    ctx["agentdojo_catalog_json"] = json_dumps(ctx["agentdojo_catalog"])
    return ctx


@router.get("/new", response_class=HTMLResponse)
def runs_new_page(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    fragment = request.query_params.get("fragment") == "1"

    if fragment:
        ctx = _runs_new_full_context(request, session, user)
        return templates.TemplateResponse("runs/new_form.html", ctx)

    # Lightweight shell: show "Initializing suites..." and load form via fetch
    ctx = template_context_base(request, session)
    ctx["title"] = "New run"
    ctx["tool_kinds"] = [t.value for t in ToolKind]
    ctx["profiles"] = session.exec(
        select(ModelProfile).where(ModelProfile.owner_user_id == user.id).order_by(ModelProfile.created_at.desc())
    ).all()
    return templates.TemplateResponse("runs/new_shell.html", ctx)


@router.post("/new")
def runs_new_submit(
    request: Request,
    tool_kind: str = Form(...),
    model_profile_id: str = Form(""),
    judge_model_profile_id: str = Form(""),
    localguard_mode: str = Form("full"),
    localguard_use_cache: str = Form("1"),
    localguard_garak_generations: str = Form("1"),
    localguard_garak_parallel_attempts: str = Form(""),
    garak_generations: str = Form("5"),
    garak_parallel_attempts: str = Form(""),
    probes: list[str] = Form([]),
    params_json: str = Form("{}"),
    agentdojo_benchmark_version: str = Form(""),
    agentdojo_suites: list[str] = Form([]),
    agentdojo_user_tasks: list[str] = Form([]),
    agentdojo_injection_tasks: list[str] = Form([]),
    agentdojo_attack: str = Form(""),
    agentdojo_defense: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    try:
        tk = ToolKind(tool_kind)
    except Exception:
        flash(request, "Invalid tool kind", level="error")
        return redirect("/runs/new")

    # Advanced params JSON is optional; it merges on top of form fields.
    parsed: dict = {}
    if params_json.strip():
        try:
            parsed = json.loads(params_json or "{}")
        except Exception:
            flash(request, "Advanced params JSON is not valid JSON", level="error")
            return redirect("/runs/new")

    if tk == ToolKind.agentdojo:
        if agentdojo_benchmark_version.strip():
            parsed["benchmark_version"] = agentdojo_benchmark_version.strip()
        suites = [s for s in agentdojo_suites if s and s.strip()]
        if suites:
            parsed["suites"] = suites
        if agentdojo_user_tasks:
            parsed["user_tasks"] = [t for t in agentdojo_user_tasks if t and t.strip()]
        if agentdojo_injection_tasks:
            parsed["injection_tasks"] = [t for t in agentdojo_injection_tasks if t and t.strip()]
        if agentdojo_attack.strip():
            parsed["attack"] = agentdojo_attack.strip()
        if agentdojo_defense.strip():
            parsed["defense"] = agentdojo_defense.strip()

    mp_id: int | None = None
    if model_profile_id.strip():
        try:
            mp_id = int(model_profile_id)
        except Exception:
            flash(request, "Invalid model profile id", level="error")
            return redirect("/runs/new")

        profile = session.exec(
            select(ModelProfile).where(ModelProfile.id == mp_id, ModelProfile.owner_user_id == user.id)
        ).first()
        if profile is None:
            flash(request, "Model profile not found", level="error")
            return redirect("/runs/new")

    # LocalGuard needs a judge model profile too.
    if tk == ToolKind.localguard:
        if mp_id is None:
            flash(request, "LocalGuard requires a scanned model profile", level="error")
            return redirect("/runs/new")
        if not judge_model_profile_id.strip():
            flash(request, "LocalGuard requires a judge model profile", level="error")
            return redirect("/runs/new")
        try:
            judge_id = int(judge_model_profile_id)
        except Exception:
            flash(request, "Invalid judge model profile id", level="error")
            return redirect("/runs/new")
        judge = session.exec(
            select(ModelProfile).where(ModelProfile.id == judge_id, ModelProfile.owner_user_id == user.id)
        ).first()
        if judge is None:
            flash(request, "Judge model profile not found", level="error")
            return redirect("/runs/new")
        parsed["judge_model_profile_id"] = judge_id
        mode_val = (localguard_mode or "full").strip().lower()
        parsed["mode"] = "report-only" if mode_val == "report-only" else "full"
        _use_cache_val = localguard_use_cache if isinstance(localguard_use_cache, str) else (localguard_use_cache[-1] if localguard_use_cache else "1")
        parsed["use_cache"] = str(_use_cache_val).strip().lower() in ("1", "true", "yes", "on")
        try:
            parsed["garak_generations"] = max(1, int((localguard_garak_generations or "1").strip()))
        except (ValueError, TypeError):
            parsed["garak_generations"] = 1
        if (localguard_garak_parallel_attempts or "").strip():
            try:
                parsed["garak_parallel_attempts"] = int(localguard_garak_parallel_attempts.strip())
            except (ValueError, TypeError):
                pass
    else:
        # Non-LocalGuard tools: probes dropdown is available.
        if probes:
            parsed["probes"] = [p for p in probes if p and p.strip()]
        if tk == ToolKind.garak:
            try:
                parsed["generations"] = max(1, int((garak_generations or "5").strip()))
            except (ValueError, TypeError):
                parsed["generations"] = 5
            if (garak_parallel_attempts or "").strip():
                try:
                    parsed["parallel_attempts"] = int(garak_parallel_attempts.strip())
                except (ValueError, TypeError):
                    pass

    run = Run(
        owner_user_id=user.id or 0,
        model_profile_id=mp_id,
        tool_kind=tk,
        status=RunStatus.pending,
        params_json=json_dumps(parsed),
    )
    session.add(run)
    session.commit()
    flash(request, f"Queued run #{run.id}", level="success")
    return redirect(f"/runs/{run.id}")


@router.get("/{run_id}", response_class=HTMLResponse)
def run_view(
    request: Request,
    run_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    templates = request.app.state.templates  # type: ignore[attr-defined]
    run = session.exec(select(Run).where(Run.id == run_id, Run.owner_user_id == user.id)).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    artifacts = session.exec(select(Artifact).where(Artifact.run_id == run.id).order_by(Artifact.created_at.desc())).all()
    ctx = template_context_base(request, session)
    ctx["title"] = f"Run #{run.id}"
    ctx["run"] = run
    ctx["artifacts"] = artifacts
    ctx["log_text"] = _read_log_tail(run.log_path)
    return templates.TemplateResponse("runs/view.html", ctx)


@router.get("/{run_id}/log")
def run_log_sse(
    request: Request,
    run_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    run = session.exec(select(Run).where(Run.id == run_id, Run.owner_user_id == user.id)).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    log_path = run.log_path

    engine = request.app.state.engine  # type: ignore[attr-defined]

    async def event_stream():
        # SSE loop: follow the log file.
        last_size = 0
        current_log_path = log_path
        while True:
            # client disconnect
            if await request.is_disconnected():
                break

            # Reload run status occasionally so we can exit when done.
            from sqlmodel import Session as _Session

            with _Session(engine) as s:
                db_run = s.exec(select(Run).where(Run.id == run_id)).first()
            if db_run is not None and db_run.log_path and db_run.log_path != current_log_path:
                # log_path becomes available after the worker claims the run.
                current_log_path = db_run.log_path
                last_size = 0

            done = db_run is not None and db_run.status in {RunStatus.succeeded, RunStatus.failed, RunStatus.cancelled}

            if current_log_path:
                p = Path(current_log_path)
                if p.exists() and p.is_file():
                    size = p.stat().st_size
                    if size > last_size:
                        with p.open("rb") as f:
                            f.seek(last_size)
                            chunk = f.read(size - last_size)
                        last_size = size
                        text = chunk.decode("utf-8", errors="replace").splitlines()
                        for line in text:
                            yield f"data: {line}\n\n"
                    else:
                        # keepalive so proxies/browsers don't buffer forever
                        yield "event: ping\ndata: 1\n\n"
                else:
                    # log not created yet, still keep the connection alive
                    yield "event: ping\ndata: 1\n\n"
            else:
                # log_path not known yet, keep the connection alive
                yield "event: ping\ndata: 1\n\n"

            if done:
                break

            await _sleep(0.8)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _sleep(seconds: float) -> None:
    # tiny wrapper to avoid importing asyncio in sync contexts
    import asyncio

    await asyncio.sleep(seconds)


@router.post("/{run_id}/cancel")
def run_cancel(
    request: Request,
    run_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    run = session.exec(select(Run).where(Run.id == run_id, Run.owner_user_id == user.id)).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status not in {RunStatus.pending, RunStatus.running}:
        flash(request, "Run is not cancellable", level="error")
        return redirect(f"/runs/{run.id}")

    # Mark cancelled and best-effort kill the process group if PID is known.
    run.status = RunStatus.cancelled
    session.add(run)
    session.commit()

    if run.pid:
        _kill_process_group_best_effort(int(run.pid))
    flash(request, "Cancellation requested", level="success")
    return redirect(f"/runs/{run.id}")


@router.post("/{run_id}/delete")
def run_delete(
    request: Request,
    run_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    run = session.exec(select(Run).where(Run.id == run_id, Run.owner_user_id == user.id)).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == RunStatus.running:
        flash(request, "Stop the run before deleting it", level="error")
        return redirect(f"/runs/{run.id}")

    # Delete artifacts on disk (best-effort).
    artifacts_dir = Path(request.app.state.settings.artifacts_dir) / str(run.id)  # type: ignore[attr-defined]
    try:
        _rm_tree(artifacts_dir)
    except Exception:
        pass

    # Delete artifact rows first.
    arts = session.exec(select(Artifact).where(Artifact.run_id == run.id)).all()
    for a in arts:
        session.delete(a)
    session.delete(run)
    session.commit()

    flash(request, f"Deleted run #{run_id}", level="success")
    return redirect("/runs")


def _kill_process_group_best_effort(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        return
    # Optional: escalate quickly if still alive.
    try:
        import time

        time.sleep(1.0)
        os.killpg(pid, 0)
    except Exception:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        return


def _rm_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_file() or path.is_symlink():
        path.unlink(missing_ok=True)  # type: ignore[arg-type]
        return
    for child in path.iterdir():
        _rm_tree(child)
    path.rmdir()


@router.get("/{run_id}/artifact/{artifact_id}")
def run_artifact_download(
    run_id: int,
    artifact_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    run = session.exec(select(Run).where(Run.id == run_id, Run.owner_user_id == user.id)).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    art = session.exec(select(Artifact).where(Artifact.id == artifact_id, Artifact.run_id == run.id)).first()
    if art is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    p = Path(art.path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Artifact file missing on disk")

    return FileResponse(path=str(p), media_type=art.mime, filename=p.name)


@router.get("/{run_id}/log_tail")
def run_log_tail(
    run_id: int,
    offset: int = 0,
    user: User = Depends(require_user),
    session: Session = Depends(get_db_session),
):
    """
    Incremental log polling fallback for environments where SSE is buffered.\n\n
    Returns JSON: {offset: <new_offset>, data: <text>}\n
    """
    run = session.exec(select(Run).where(Run.id == run_id, Run.owner_user_id == user.id)).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not run.log_path:
        return JSONResponse({"offset": offset, "data": ""})

    p = Path(run.log_path)
    if not p.exists() or not p.is_file():
        return JSONResponse({"offset": offset, "data": ""})

    try:
        size = p.stat().st_size
        offset = max(0, min(int(offset), size))
        with p.open("rb") as f:
            f.seek(offset)
            chunk = f.read()
        new_offset = offset + len(chunk)
        text = chunk.decode("utf-8", errors="replace")
        return JSONResponse({"offset": new_offset, "data": text})
    except Exception:
        return JSONResponse({"offset": offset, "data": ""})

