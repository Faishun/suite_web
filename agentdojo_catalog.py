"""
Load AgentDojo suites, attacks, defenses, and models for the run-creation UI.
Uses agentdojo-quickstart sources (adds agentdojo_dir/src to path if needed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Benchmark versions supported by agentdojo load_suites (from _SUITES keys).
BENCHMARK_VERSIONS = ["v1", "v1.1", "v1.1.1", "v1.1.2", "v1.2", "v1.2.1", "v1.2.2"]


def get_agentdojo_catalog(agentdojo_dir: Path | None = None) -> dict[str, Any]:
    """
    Return a catalog of suites (per version), attacks, defenses, and models
    for use in the run-creation form. If agentdojo cannot be imported,
    returns a minimal structure with empty lists and an error message.
    """
    out: dict[str, Any] = {
        "benchmark_versions": list(BENCHMARK_VERSIONS),
        "suites_by_version": {},
        "attacks": [],
        "defenses": [],
        "models": [],
        "error": None,
    }

    def _ensure_agentdojo_on_path() -> bool:
        if agentdojo_dir is not None:
            src = agentdojo_dir / "src"
            if src.is_dir() and str(src) not in __import__("sys").path:
                __import__("sys").path.insert(0, str(src))
        try:
            __import__("agentdojo.task_suite.load_suites")
            return True
        except Exception:
            return False

    if not _ensure_agentdojo_on_path():
        out["error"] = "AgentDojo not importable. Set SUITE_WEB_AGENTDOJO_DIR or install agentdojo."
        return out

    try:
        from agentdojo.agent_pipeline.agent_pipeline import DEFENSES
        from agentdojo.attacks.attack_registry import ATTACKS
        from agentdojo.models import ModelsEnum
        from agentdojo.task_suite.load_suites import get_suites
    except Exception as e:
        out["error"] = str(e)
        return out

    out["attacks"] = sorted(ATTACKS.keys())
    out["defenses"] = list(DEFENSES)
    out["models"] = [{"value": m.value, "label": _model_label(m.value)} for m in ModelsEnum]

    for version in BENCHMARK_VERSIONS:
        try:
            suites = get_suites(version)
        except Exception:
            continue
        out["suites_by_version"][version] = {}
        for suite_name, suite in suites.items():
            user_task_list = [
                {"id": tid, "prompt": getattr(suite.user_tasks[tid], "PROMPT", "") or ""}
                for tid in sorted(suite.user_tasks.keys())
            ]
            injection_task_list = [
                {"id": tid, "goal": getattr(suite.injection_tasks[tid], "GOAL", "") or ""}
                for tid in sorted(suite.injection_tasks.keys())
            ]
            out["suites_by_version"][version][suite_name] = {
                "user_tasks": user_task_list,
                "injection_tasks": injection_task_list,
            }

    return out


def _model_label(value: str) -> str:
    try:
        from agentdojo.models import MODEL_NAMES
        return MODEL_NAMES.get(value, value)
    except Exception:
        return value
