from __future__ import annotations

from pathlib import Path

from suite_web.models import ToolKind
from suite_web.runners.types import RunContext, ToolRunner


class StubRunner(ToolRunner):
    def __init__(self, tool_kind: ToolKind):
        self.tool_kind = tool_kind

    def run(self, ctx: RunContext) -> list[tuple[str, Path, str]]:
        msg = (
            f"[stub runner] Tool integration for {self.tool_kind.value} is not implemented yet.\n"
            f"Params: {ctx.params}\n"
        )
        ctx.log_path.parent.mkdir(parents=True, exist_ok=True)
        with ctx.log_path.open("a", encoding="utf-8") as f:
            f.write(msg)
        return []

