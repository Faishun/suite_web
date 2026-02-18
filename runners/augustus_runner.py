from __future__ import annotations

import json
from pathlib import Path

from suite_web.model_profiles import decrypt_api_key, normalize_openai_base_url
from suite_web.models import ProviderKind, ToolKind
from suite_web.netutil import http_get_json
from suite_web.probe_catalog import strip_ansi
from suite_web.runners.types import RunContext, RunnerError, ToolRunner
from suite_web.subprocesses import run_command_streaming


class AugustusRunner(ToolRunner):
    tool_kind = ToolKind.augustus

    def run(self, ctx: RunContext) -> list[tuple[str, Path, str]]:
        """
        Params (JSON):
          - generator: e.g. "openai.OpenAI" (default inferred from provider kind)
          - probes_glob: e.g. "dan.*,encoding.*" (required)
          - buffs_glob: e.g. "encoding.*" (optional)
          - format: "jsonl" (default jsonl)
          - timeout: e.g. "30m" (default 30m)
          - concurrency: int (default 10)
          - probe_timeout: e.g. "5m" (default 5m)
        """
        if ctx.model_profile is None:
            raise RunnerError("Augustus requires a model_profile")

        # Prefer explicit probes list from the run creation dropdown.
        probes_list = ctx.params.get("probes")
        probes: list[str] = []
        if isinstance(probes_list, list):
            probes = [_normalize_probe(str(p)) for p in probes_list if str(p).strip()]

        probes_glob = str(ctx.params.get("probes_glob", "")).strip()
        if not probes and not probes_glob:
            raise RunnerError("augustus requires either params.probes (list) or params.probes_glob (string)")

        buffs_glob = str(ctx.params.get("buffs_glob", "")).strip()
        out_jsonl = ctx.artifacts_dir / "augustus.results.jsonl"
        out_html = ctx.artifacts_dir / "augustus.report.html"

        generator = str(ctx.params.get("generator", "")).strip()
        if not generator:
            generator = _default_generator(ctx.model_profile.provider_kind)

        gen_config = _build_generator_config(ctx, generator)
        # Preflight for OpenAI-compatible base_url
        if ctx.model_profile.base_url:
            base = normalize_openai_base_url(ctx.model_profile.base_url)
            check, body = http_get_json(base.rstrip("/") + "/models", timeout_s=3.0)
            with ctx.log_path.open("a", encoding="utf-8") as f:
                f.write(f"[preflight] GET {base}/models ok={check.ok} status={check.status} ms={check.duration_ms} err={check.error}\n")

        cmd = []
        cwd = ctx.settings.augustus_dir
        if ctx.settings.augustus_bin:
            cmd = [ctx.settings.augustus_bin]
        else:
            cmd = ["go", "run", "./cmd/augustus"]

        cmd += [
            "scan",
            generator,
            "--format",
            "jsonl",
            "--output",
            str(out_jsonl),
            "--html",
            str(out_html),
            "--config",
            json.dumps(gen_config),
        ]

        if probes:
            for p in probes:
                cmd += ["--probe", p]
        else:
            cmd += ["--probes-glob", probes_glob]

        if buffs_glob:
            cmd += ["--buffs-glob", buffs_glob]

        if "timeout" in ctx.params:
            cmd += ["--timeout", str(ctx.params["timeout"])]
        if "concurrency" in ctx.params:
            cmd += ["--concurrency", str(int(ctx.params["concurrency"]))]
        if "probe_timeout" in ctx.params:
            cmd += ["--probe-timeout", str(ctx.params["probe_timeout"])]

        if bool(ctx.params.get("verbose", True)):
            cmd += ["--verbose"]

        env = dict(**{k: v for k, v in __import__("os").environ.items()})
        # Load user-provided YAML templates (custom probes) at runtime.
        custom_dir = Path(ctx.settings.custom_augustus_templates_dir) / str(ctx.run.owner_user_id)
        if custom_dir.exists():
            env["AUGUSTUS_TEMPLATE_DIR"] = str(custom_dir)
            with ctx.log_path.open("a", encoding="utf-8") as f:
                f.write(f"[augustus] AUGUSTUS_TEMPLATE_DIR={custom_dir}\n")

        result, pid = run_command_streaming(
            cmd=cmd,
            cwd=cwd,
            env=env,
            log_path=ctx.log_path,
            timeout_s=float(ctx.params.get("timeout_s", 7200)),
            pid_hook=lambda pid: ctx.update_run(pid=pid),
            should_cancel=ctx.is_cancelled,
        )
        if result.exit_code != 0:
            raise RunnerError(f"augustus failed with exit_code={result.exit_code}")

        artifacts: list[tuple[str, Path, str]] = []
        if out_jsonl.exists():
            artifacts.append(("jsonl", out_jsonl, "application/json"))
        if out_html.exists():
            artifacts.append(("html", out_html, "text/html"))
        return artifacts


def _default_generator(provider_kind: ProviderKind) -> str:
    if provider_kind in {ProviderKind.openai, ProviderKind.openai_compat}:
        return "openai.OpenAI"
    if provider_kind == ProviderKind.ollama:
        return "ollama.OllamaChat"
    if provider_kind == ProviderKind.anthropic:
        return "anthropic.Anthropic"
    if provider_kind == ProviderKind.google:
        return "vertex.Vertex"  # best guess; user can override
    if provider_kind == ProviderKind.huggingface:
        return "huggingface.Inference"  # best guess; user can override
    return "openai.OpenAI"


def _build_generator_config(ctx: RunContext, generator: str) -> dict:
    prof = ctx.model_profile
    assert prof is not None
    model = prof.model.strip()

    cfg: dict = {"model": model}
    api_key = decrypt_api_key(ctx.settings, prof)
    if api_key:
        cfg["api_key"] = api_key

    # OpenAI-compatible base_url support.
    if prof.base_url:
        cfg["base_url"] = normalize_openai_base_url(prof.base_url)

    # Optional knobs
    if "temperature" in ctx.params:
        cfg["temperature"] = float(ctx.params["temperature"])
    if "max_tokens" in ctx.params:
        cfg["max_tokens"] = int(ctx.params["max_tokens"])

    return cfg


def _normalize_probe(value: str) -> str:
    v = strip_ansi(value).strip()
    if v.startswith("0m"):
        v = v[2:]
    return v.strip()

