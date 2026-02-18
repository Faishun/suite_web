from __future__ import annotations

import sys
from pathlib import Path

from suite_web.model_profiles import api_key_for_openai_env, decrypt_api_key
from suite_web.models import ProviderKind, ToolKind
from suite_web.netutil import http_get_json
from suite_web.runners.types import RunContext, RunnerError, ToolRunner
from suite_web.subprocesses import run_command_streaming


class LocalGuardRunner(ToolRunner):
    tool_kind = ToolKind.localguard

    def run(self, ctx: RunContext) -> list[tuple[str, Path, str]]:
        """
        Params (JSON):
          - mode: full|report-only (default full)
          - use_cache: bool (default True). If False, disables history so phases are not skipped.
          - timeout_s: float (default 6h)
        """
        if ctx.model_profile is None:
            raise RunnerError("LocalGuard requires a model_profile")

        mode = str(ctx.params.get("mode", "full")).strip() or "full"
        provider = _localguard_provider_key(ctx.model_profile.provider_kind)
        model = ctx.model_profile.model.strip()
        if not model:
            raise RunnerError("LocalGuard requires model_profile.model")

        judge_id_raw = ctx.params.get("judge_model_profile_id")
        if judge_id_raw is None:
            raise RunnerError("LocalGuard requires params.judge_model_profile_id")
        try:
            judge_id = int(judge_id_raw)
        except Exception as e:
            raise RunnerError("LocalGuard judge_model_profile_id must be an int") from e

        judge_profile = ctx.get_profile(judge_id)
        if judge_profile is None:
            raise RunnerError("LocalGuard judge model profile not found")

        out_dir = ctx.artifacts_dir / "localguard"
        out_dir.mkdir(parents=True, exist_ok=True)

        env = dict(**{k: v for k, v in __import__("os").environ.items()})
        use_cache = ctx.params.get("use_cache", True)
        if not use_cache:
            env["LOCALGUARD_DISABLE_HISTORY"] = "1"
        else:
            # Shared history path so re-runs with same model/judge skip completed phases.
            history_dir = ctx.settings.artifacts_dir / "localguard_history"
            history_dir.mkdir(parents=True, exist_ok=True)
            env["LOCALGUARD_HISTORY_FILE"] = str(history_dir / f"scan_user_{ctx.run.owner_user_id}.json")
        api_key = decrypt_api_key(ctx.settings, ctx.model_profile)

        # LocalGuard reads keys from env via LocalGuard/config.py
        if ctx.model_profile.provider_kind == ProviderKind.openai and api_key:
            env["OPENAI_API_KEY"] = api_key
        if ctx.model_profile.provider_kind == ProviderKind.anthropic and api_key:
            env["ANTHROPIC_API_KEY"] = api_key
        if ctx.model_profile.provider_kind == ProviderKind.google and api_key:
            env["GOOGLE_API_KEY"] = api_key
        if ctx.model_profile.provider_kind == ProviderKind.huggingface and api_key:
            env["HF_TOKEN"] = api_key

        # For local targets, prefer OpenAI-compatible base URL via OPENAI_BASE_URL or OLLAMA_URL as needed.
        if ctx.model_profile.base_url:
            # LocalGuard's garak subprocess looks at OPENAI_BASE_URL -> OPENAI_API_BASE mapping.
            env["OPENAI_BASE_URL"] = ctx.model_profile.base_url.rstrip("/")
            check, body = http_get_json(env["OPENAI_BASE_URL"].rstrip("/") + "/models", timeout_s=3.0)
            with ctx.log_path.open("a", encoding="utf-8") as f:
                f.write(f"[preflight] target GET {env['OPENAI_BASE_URL']}/models ok={check.ok} status={check.status} ms={check.duration_ms} err={check.error}\n")

        # Configure local judge (see LocalGuard/.env.example)
        env["LOCAL_JUDGE_PROVIDER"] = _inspect_provider_key(judge_profile.provider_kind)
        env["LOCAL_JUDGE_MODEL"] = judge_profile.model.strip()
        judge_key = api_key_for_openai_env(ctx.settings, judge_profile)
        if judge_key is None and env["LOCAL_JUDGE_PROVIDER"] == "openai":
            # Client requires a non-empty key. Use override from env if set (your server may validate the value),
            # else fallback. If you get 401 "Incorrect API key": your model server is validating the key;
            # set SUITE_WEB_LOCALGUARD_OPENAI_API_KEY in .env to the value it accepts, or disable key validation in the server.
            judge_key = (
                getattr(ctx.settings, "localguard_openai_api_key_override", None) or "sk-local"
            )
        if judge_profile.base_url:
            # Note: LocalGuard uses OPENAI_BASE_URL for OpenAI provider; this is global, so
            # two different base URLs (target vs judge) is not supported cleanly.
            if "OPENAI_BASE_URL" in env and env["OPENAI_BASE_URL"] != judge_profile.base_url.rstrip("/"):
                with ctx.log_path.open("a", encoding="utf-8") as f:
                    f.write(
                        "[localguard] warning: target and judge have different OPENAI_BASE_URL values; "
                        "LocalGuard may not support that reliably.\n"
                    )
            env["OPENAI_BASE_URL"] = judge_profile.base_url.rstrip("/")
            check, body = http_get_json(env["OPENAI_BASE_URL"].rstrip("/") + "/models", timeout_s=3.0)
            with ctx.log_path.open("a", encoding="utf-8") as f:
                f.write(f"[preflight] judge GET {env['OPENAI_BASE_URL']}/models ok={check.ok} status={check.status} ms={check.duration_ms} err={check.error}\n")
        if judge_key and env["LOCAL_JUDGE_PROVIDER"] in {"openai", "anthropic", "google", "hf"}:
            # LocalGuard reads OPENAI_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY / HF_TOKEN.
            if judge_profile.provider_kind in {ProviderKind.openai, ProviderKind.openai_compat}:
                env["OPENAI_API_KEY"] = judge_key
            elif judge_profile.provider_kind == ProviderKind.anthropic:
                env["ANTHROPIC_API_KEY"] = judge_key
            elif judge_profile.provider_kind == ProviderKind.google:
                env["GOOGLE_API_KEY"] = judge_key
            elif judge_profile.provider_kind == ProviderKind.huggingface:
                env["HF_TOKEN"] = judge_key

        cmd = [
            sys.executable,
            "-u",
            "-m",
            "localguard_cli",
            "--provider",
            provider,
            "--model",
            model,
            "--mode",
            mode,
            "--out-dir",
            str(out_dir),
        ]

        result, pid = run_command_streaming(
            cmd=cmd,
            cwd=ctx.settings.localguard_dir,
            env=env,
            log_path=ctx.log_path,
            timeout_s=float(ctx.params.get("timeout_s", 6 * 3600)),
            pid_hook=lambda pid: ctx.update_run(pid=pid),
            should_cancel=ctx.is_cancelled,
        )
        if result.exit_code != 0:
            raise RunnerError(f"LocalGuard failed with exit_code={result.exit_code}")

        artifacts: list[tuple[str, Path, str]] = []
        for p in sorted(out_dir.glob("LocalGuard_Report_*.pdf")):
            artifacts.append(("pdf", p, "application/pdf"))
        for p in sorted(out_dir.glob("LocalGuard_Report_*.html")):
            artifacts.append(("html", p, "text/html"))
        summary = out_dir / "localguard_summary.json"
        if summary.exists():
            artifacts.append(("json", summary, "application/json"))
        history = out_dir / "scan_history.json"
        if history.exists():
            artifacts.append(("json", history, "application/json"))
        return artifacts


def _localguard_provider_key(kind: ProviderKind) -> str:
    if kind == ProviderKind.openai_compat:
        # LocalGuard config doesn't have an explicit openai_compat; use openai and set OPENAI_BASE_URL.
        return "openai"
    # LocalGuard's provider selection expects these keys:
    # ollama/openai/anthropic/google/huggingface/vllm
    return kind.value


def _inspect_provider_key(kind: ProviderKind) -> str:
    if kind == ProviderKind.openai_compat:
        return "openai"
    if kind == ProviderKind.huggingface:
        return "hf"
    return kind.value

