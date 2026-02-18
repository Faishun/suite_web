from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from suite_web.garak_custom_probe_gen import run_uses_custom_probes
from suite_web.model_profiles import decrypt_api_key, rest_chat_completions_url
from suite_web.models import ProviderKind, ToolKind
from suite_web.netutil import http_get_json
from suite_web.probe_catalog import strip_ansi
from suite_web.runners.types import RunContext, RunnerError, ToolRunner
from suite_web.subprocesses import run_command_streaming


class GarakRunner(ToolRunner):
    tool_kind = ToolKind.garak

    def run(self, ctx: RunContext) -> list[tuple[str, Path, str]]:
        """
        Params (JSON):
          - probes: "malwaregen.Evasion" or "dan,promptinject" (default: "dan,promptinject")
          - generations: int (default: 5)
          - report_prefix: str (default: "garak_out")
          - timeout_s: float (default: 3600)
        """
        probes_param = ctx.params.get("probes", "dan,promptinject")
        if isinstance(probes_param, list):
            probes = ",".join(_normalize_probe(str(p)) for p in probes_param if str(p).strip())
        else:
            probes = _normalize_probe(str(probes_param))
        if not probes:
            probes = "dan,promptinject"
        generations = int(ctx.params.get("generations", 5))
        report_prefix = str(ctx.params.get("report_prefix", "garak_out")).strip() or "garak_out"
        timeout_s = float(ctx.params.get("timeout_s", 3600))

        if ctx.model_profile is None:
            raise RunnerError("Garak requires a model_profile")

        # We keep MVP behavior simple: rest generator via OpenAI-compatible endpoint.
        if ctx.model_profile.provider_kind not in {ProviderKind.openai_compat, ProviderKind.openai}:
            # Many providers can work via litellm, but that depends on garak install.
            raise RunnerError(f"Garak runner MVP only supports provider_kind=openai_compat/openai, got {ctx.model_profile.provider_kind}")

        if not ctx.model_profile.base_url:
            raise RunnerError("Model profile base_url is required for Garak (OpenAI-compatible endpoint)")

        model_id = ctx.model_profile.model.strip() or "local-model"
        uri = rest_chat_completions_url(ctx.model_profile.base_url)
        # Preflight: try listing models (OpenAI-compatible servers usually expose /v1/models).
        base = ctx.model_profile.base_url.rstrip("/")
        models_url = base if base.endswith("/v1") else base + "/v1"
        check, body = http_get_json(models_url.rstrip("/") + "/models", timeout_s=3.0)
        with ctx.log_path.open("a", encoding="utf-8") as f:
            f.write(f"[preflight] GET {models_url}/models ok={check.ok} status={check.status} ms={check.duration_ms} err={check.error}\n")

        use_proxy = bool(ctx.params.get("use_proxy", False))
        if use_proxy:
            # Proxy flattens OpenAI responses to {"text": "..."} (see garak-local-lmstudio/lmstudio_garak_proxy.py)
            uri = str(ctx.params.get("proxy_url", "http://localhost:9000/generate")).strip() or "http://localhost:9000/generate"

        cfg = {
            "rest": {
                "RestGenerator": {
                    "name": model_id,
                    "uri": uri,
                    "method": "post",
                    "headers": {"Content-Type": "application/json"},
                    # garak's RestGenerator uses request_timeout (seconds)
                    "request_timeout": int(ctx.params.get("request_timeout", 120)),
                    "req_template_json_object": {
                        "model": model_id,
                        "messages": [{"role": "user", "content": "$INPUT"}],
                        "temperature": float(ctx.params.get("temperature", 0.7)),
                        "max_tokens": int(ctx.params.get("max_tokens", 512)),
                    },
                    "response_json": True,
                    # Use JSONPath to extract a *string* from OpenAI-compatible responses.
                    # If you extract `choices` directly, it becomes a list and detectors will crash.
                    "response_json_field": ("text" if use_proxy else "$.choices[0].message.content"),
                }
            }
        }

        cfg_path = ctx.artifacts_dir / "garak_rest_config.json"
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        env = dict(_clean_env())
        api_key = decrypt_api_key(ctx.settings, ctx.model_profile)
        if api_key:
            # Some OpenAI-compatible servers ignore this, but keep it for completeness.
            env["OPENAI_API_KEY"] = api_key

        # Only prepend custom probe dir when this run uses at least one custom probe,
        # so built-in probes run with real garak and are not affected by our shim.
        custom_dir = Path(ctx.settings.custom_probes_dir) / str(ctx.run.owner_user_id)
        if run_uses_custom_probes(custom_dir, probes_param):
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = str(custom_dir) + (os.pathsep + existing if existing else "")

        cmd = [
            sys.executable,
            "-u",
            "-m",
            "garak",
            "--model_type",
            "rest",
            "-G",
            str(cfg_path),
            "--probes",
            probes,
            "--generations",
            str(generations),
            "--report_prefix",
            str(ctx.artifacts_dir / report_prefix),
        ]

        result, pid = run_command_streaming(
            cmd=cmd,
            cwd=None,
            env=env,
            log_path=ctx.log_path,
            timeout_s=timeout_s,
            pid_hook=lambda pid: ctx.update_run(pid=pid),
            should_cancel=ctx.is_cancelled,
        )
        if result.exit_code != 0:
            raise RunnerError(f"garak failed with exit_code={result.exit_code}")

        # Collect report artifacts (best-effort).
        artifacts: list[tuple[str, Path, str]] = []
        for p in sorted(ctx.artifacts_dir.glob(f"{report_prefix}*.report.jsonl")):
            artifacts.append(("jsonl", p, "application/json"))
        for p in sorted(ctx.artifacts_dir.glob(f"{report_prefix}*.report.html")):
            artifacts.append(("html", p, "text/html"))
        return artifacts


def _clean_env() -> dict[str, str]:
    # Keep worker environment, but avoid surprising pythonpath leakage.
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    return env


def _normalize_probe(value: str) -> str:
    v = strip_ansi(value).strip()
    # Some terminals/renderers drop ESC but keep "0m" from "\x1b[0m"
    if v.startswith("0m"):
        v = v[2:]
    return v.strip()

