from __future__ import annotations

import tarfile
import sys
from pathlib import Path

from suite_web.model_profiles import decrypt_api_key, extract_port, normalize_openai_base_url
from suite_web.models import ProviderKind, ToolKind
from suite_web.runners.types import RunContext, RunnerError, ToolRunner
from suite_web.subprocesses import run_command_streaming


class AgentDojoRunner(ToolRunner):
    tool_kind = ToolKind.agentdojo

    def run(self, ctx: RunContext) -> list[tuple[str, Path, str]]:
        """
        Params (JSON) are mapped to `python -m agentdojo.scripts.benchmark`.\n\n
        Common keys:\n
          - suites: list[str] or string (maps to -s/--suite; default: [])\n
          - user_tasks: list[str] (maps to -ut)\n
          - injection_tasks: list[str] (maps to -it)\n
          - attack: str|None\n
          - defense: str|None\n
          - benchmark_version: str (default v1.2.2)\n
          - tool_delimiter: str (default tool)\n
          - max_workers: int (default 1)\n
          - force_rerun: bool\n
        Model selection:\n
          - model: str (ModelsEnum choice, default gpt-4o-2024-05-13)\n
          - model_id: str (for local)\n
        """
        logdir = ctx.artifacts_dir / "agentdojo_runs"
        logdir.mkdir(parents=True, exist_ok=True)

        cmd = [sys.executable, "-u", "-m", "agentdojo.scripts.benchmark"]
        cmd += ["--logdir", str(logdir)]
        cmd += ["--benchmark-version", str(ctx.params.get("benchmark_version", "v1.2.2"))]

        suites = ctx.params.get("suites", [])
        if isinstance(suites, str):
            suites = [suites]
        for s in suites:
            if str(s).strip():
                cmd += ["--suite", str(s)]

        for ut in _as_list(ctx.params.get("user_tasks", [])):
            cmd += ["-ut", ut]
        for it in _as_list(ctx.params.get("injection_tasks", [])):
            cmd += ["-it", it]

        if ctx.params.get("attack"):
            cmd += ["--attack", str(ctx.params["attack"])]
        if ctx.params.get("defense"):
            cmd += ["--defense", str(ctx.params["defense"])]

        if ctx.params.get("tool_delimiter"):
            cmd += ["--tool-delimiter", str(ctx.params["tool_delimiter"])]
        if ctx.params.get("max_workers"):
            cmd += ["--max-workers", str(int(ctx.params["max_workers"]))]
        if bool(ctx.params.get("force_rerun", False)):
            cmd += ["--force-rerun"]

        env = dict(**{k: v for k, v in __import__("os").environ.items()})
        _apply_model_env(ctx, env, cmd)

        result, pid = run_command_streaming(
            cmd=cmd,
            cwd=ctx.settings.agentdojo_dir,
            env=env,
            log_path=ctx.log_path,
            timeout_s=float(ctx.params.get("timeout_s", 6 * 3600)),
            pid_hook=lambda pid: ctx.update_run(pid=pid),
            should_cancel=ctx.is_cancelled,
        )
        if result.exit_code != 0:
            raise RunnerError(f"agentdojo failed with exit_code={result.exit_code}")

        # Tar up logdir for download.
        tar_path = ctx.artifacts_dir / "agentdojo_runs.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(logdir, arcname="agentdojo_runs")

        return [("tar.gz", tar_path, "application/gzip")]


def _apply_model_env(ctx: RunContext, env: dict[str, str], cmd: list[str]) -> None:
    prof = ctx.model_profile
    # AgentDojo can be run without a profile, but typically needs one.
    if prof is None:
        # Allow running with whatever env is already set.
        if ctx.params.get("model"):
            cmd += ["--model", str(ctx.params["model"])]
        if ctx.params.get("model_id"):
            cmd += ["--model-id", str(ctx.params["model_id"])]
        return

    provider = prof.provider_kind

    # Default model value if user didn't specify.
    if ctx.params.get("model"):
        cmd += ["--model", str(ctx.params["model"])]
    else:
        # Reasonable defaults; can be overridden per run.
        if provider == ProviderKind.openai:
            cmd += ["--model", prof.model or "gpt-4o-2024-05-13"]
        elif provider == ProviderKind.openai_compat:
            cmd += ["--model", "LOCAL"]
        else:
            cmd += ["--model", prof.model or "gpt-4o-2024-05-13"]

    if provider == ProviderKind.openai:
        api_key = decrypt_api_key(ctx.settings, prof)
        if api_key:
            env["OPENAI_API_KEY"] = api_key
        if prof.base_url:
            env["OPENAI_BASE_URL"] = normalize_openai_base_url(prof.base_url)
    elif provider == ProviderKind.openai_compat:
        # AgentDojo local provider uses localhost:LOCAL_LLM_PORT by default.
        if prof.base_url:
            port = extract_port(prof.base_url)
            if port:
                env["LOCAL_LLM_PORT"] = str(port)
        if ctx.params.get("model_id"):
            cmd += ["--model-id", str(ctx.params["model_id"])]
        elif prof.model:
            cmd += ["--model-id", prof.model]
    else:
        # Best-effort: for other providers, rely on agentdojo's own provider env vars.
        api_key = decrypt_api_key(ctx.settings, prof)
        if provider == ProviderKind.anthropic and api_key:
            env["ANTHROPIC_API_KEY"] = api_key
        if provider == ProviderKind.google and api_key:
            env["GOOGLE_API_KEY"] = api_key
        if provider == ProviderKind.huggingface and api_key:
            env["HF_TOKEN"] = api_key


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, tuple):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    return [str(v)]

