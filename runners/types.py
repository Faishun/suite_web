from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from suite_web.models import ModelProfile, Run, ToolKind
from suite_web.settings import Settings


@dataclass(frozen=True)
class RunContext:
    settings: Settings
    run: Run
    model_profile: ModelProfile | None
    artifacts_dir: Path
    log_path: Path
    params: dict[str, Any]
    update_run: Callable[..., None]
    get_profile: Callable[[int], ModelProfile | None]
    is_cancelled: Callable[[], bool]


class RunnerError(RuntimeError):
    pass


class ToolRunner:
    tool_kind: ToolKind

    def run(self, ctx: RunContext) -> list[tuple[str, Path, str]]:
        """
        Execute the tool.\n\n
        Returns artifacts as a list of tuples: (kind, path, mime)\n
        The worker always registers the log as an artifact separately.\n
        """
        raise NotImplementedError

