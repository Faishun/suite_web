from __future__ import annotations

from suite_web.models import ToolKind
from suite_web.runners.types import ToolRunner


_RUNNERS: dict[ToolKind, ToolRunner] = {}


def register_runner(runner: ToolRunner) -> None:
    _RUNNERS[runner.tool_kind] = runner


def get_runner(tool_kind: ToolKind) -> ToolRunner | None:
    return _RUNNERS.get(tool_kind)


def load_builtin_runners() -> None:
    # Register tool runners. If a runner fails to import (missing deps),
    # keep a stub so the UI can still queue/inspect runs.
    from suite_web.runners.stub import StubRunner

    try:
        from suite_web.runners.garak_runner import GarakRunner

        register_runner(GarakRunner())
    except Exception:
        register_runner(StubRunner(ToolKind.garak))

    try:
        from suite_web.runners.augustus_runner import AugustusRunner

        register_runner(AugustusRunner())
    except Exception:
        register_runner(StubRunner(ToolKind.augustus))

    try:
        from suite_web.runners.agentdojo_runner import AgentDojoRunner

        register_runner(AgentDojoRunner())
    except Exception:
        register_runner(StubRunner(ToolKind.agentdojo))

    try:
        from suite_web.runners.localguard_runner import LocalGuardRunner

        register_runner(LocalGuardRunner())
    except Exception:
        register_runner(StubRunner(ToolKind.localguard))

