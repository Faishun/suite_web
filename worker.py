from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from suite_web.db import ensure_dirs
from suite_web.jsonutil import json_loads
from suite_web.models import Artifact, ModelProfile, Run, RunStatus
from suite_web.runners.registry import get_runner, load_builtin_runners
from suite_web.runners.types import RunContext, RunnerError
from suite_web.settings import load_settings


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _now() -> datetime:
    return datetime.utcnow()


def _write_log_line(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def _prepare_run_paths(settings, run_id: int) -> tuple[Path, Path]:
    run_dir = settings.artifacts_dir / str(run_id)
    ensure_dirs(run_dir)
    log_path = run_dir / "run.log"
    return run_dir, log_path


def _register_artifact(session: Session, run_id: int, kind: str, path: Path, mime: str) -> None:
    art = Artifact(run_id=run_id, kind=kind, path=str(path), mime=mime)
    session.add(art)
    session.commit()


def _claim_next_run(session: Session) -> Run | None:
    # Simple single-worker claim: pick oldest pending.
    run = session.exec(select(Run).where(Run.status == RunStatus.pending).order_by(Run.created_at.asc())).first()
    if run is None:
        return None

    # If user already cancelled while pending, skip.
    if run.status == RunStatus.cancelled:
        return None

    run.status = RunStatus.running
    run.started_at = _now()
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _load_model_profile(session: Session, run: Run) -> ModelProfile | None:
    if run.model_profile_id is None:
        return None
    return session.exec(select(ModelProfile).where(ModelProfile.id == run.model_profile_id)).first()


def run_worker_loop() -> None:
    settings = load_settings(_repo_root())
    from suite_web.db import create_db_engine, init_db

    engine = create_db_engine(settings.db_url)
    init_db(engine)
    ensure_dirs(settings.artifacts_dir, settings.custom_probes_dir, settings.custom_augustus_templates_dir)

    load_builtin_runners()

    poll_seconds = float(os.getenv("SUITE_WEB_WORKER_POLL_SECONDS", "1.0"))
    heartbeat_path = settings.artifacts_dir / "_worker_heartbeat.txt"

    print("[suite_web.worker] starting worker loop")
    while True:
        # Heartbeat for UI visibility.
        try:
            heartbeat_path.write_text(f"{_now().isoformat()}Z\n", encoding="utf-8")
        except Exception:
            pass

        with Session(engine) as session:
            run = _claim_next_run(session)
            if run is None:
                pass
            else:
                run_dir, log_path = _prepare_run_paths(settings, run.id or 0)
                run.log_path = str(log_path)
                session.add(run)
                session.commit()

                # Log is always an artifact.
                _register_artifact(session, run.id or 0, kind="log", path=log_path, mime="text/plain")

                mp = _load_model_profile(session, run)
                params = json_loads(run.params_json, default={})

                def _update_run(**fields):
                    # Persist selected fields immediately (e.g. PID).
                    with Session(engine) as s2:
                        db_run = s2.exec(select(Run).where(Run.id == run.id)).first()
                        if db_run is None:
                            return
                        for k, v in fields.items():
                            if not hasattr(db_run, k):
                                continue
                            setattr(db_run, k, v)
                        s2.add(db_run)
                        s2.commit()

                def _get_profile(profile_id: int) -> ModelProfile | None:
                    with Session(engine) as s3:
                        return s3.exec(select(ModelProfile).where(ModelProfile.id == profile_id)).first()

                def _is_cancelled() -> bool:
                    with Session(engine) as s4:
                        db_run = s4.exec(select(Run).where(Run.id == run.id)).first()
                        return db_run is not None and db_run.status == RunStatus.cancelled

                ctx = RunContext(
                    settings=settings,
                    run=run,
                    model_profile=mp,
                    artifacts_dir=run_dir,
                    log_path=log_path,
                    params=params,
                    update_run=_update_run,
                    get_profile=_get_profile,
                    is_cancelled=_is_cancelled,
                )

                _write_log_line(log_path, f"[worker] claimed run #{run.id} tool={run.tool_kind.value}")
                _write_log_line(log_path, f"[worker] params={json.dumps(params, ensure_ascii=True, sort_keys=True)}")
                if mp is not None:
                    _write_log_line(
                        log_path,
                        f"[worker] scanned_profile id={mp.id} provider={mp.provider_kind.value} model={mp.model} base_url={mp.base_url}",
                    )
                try:
                    if _is_cancelled():
                        _write_log_line(log_path, "[worker] run was cancelled before start; skipping execution")
                        run.exit_code = 130
                        run.status = RunStatus.cancelled
                        continue
                    runner = get_runner(run.tool_kind)
                    if runner is None:
                        raise RunnerError(f"No runner registered for tool {run.tool_kind.value}")

                    produced = runner.run(ctx)
                    for kind, path, mime in produced:
                        _register_artifact(session, run.id or 0, kind=kind, path=path, mime=mime)

                    # If stub runner ran, it wrote log; exit_code remains None, but mark as failed so UI is clear.
                    if "[stub runner]" in _safe_read_text(log_path):
                        run.exit_code = 2
                        run.status = RunStatus.failed
                    else:
                        run.exit_code = 0
                        # If user requested cancellation, honor that even if the process ended naturally.
                        latest = session.exec(select(Run).where(Run.id == run.id)).first()
                        if latest is not None and latest.status == RunStatus.cancelled:
                            run.status = RunStatus.cancelled
                        else:
                            run.status = RunStatus.succeeded
                except Exception as e:
                    _write_log_line(log_path, f"[worker] error: {e!r}")
                    run.exit_code = 1
                    run.status = RunStatus.failed
                finally:
                    run.finished_at = _now()
                    session.add(run)
                    session.commit()

        time.sleep(poll_seconds)


def _safe_read_text(path: Path, limit: int = 64_000) -> str:
    try:
        data = path.read_bytes()[:limit]
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def main():
    # Support: python -m suite_web.worker
    try:
        run_worker_loop()
    except KeyboardInterrupt:
        print("\n[suite_web.worker] shutting down")
        raise SystemExit(0)


if __name__ == "__main__":
    main()

