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
        # So LocalGuard can write/read Garak report per run (avoids picking wrong run when cache disabled).
        env = dict(**{k: v for k, v in __import__("os").environ.items()})
        env["LOCALGUARD_GARAK_REPORT_DIR"] = str(out_dir)
        use_cache = ctx.params.get("use_cache", True)
        # Report-only needs history to regenerate report; full scan uses history when cache on.
        history_dir = ctx.settings.artifacts_dir / "localguard_history"
        history_dir.mkdir(parents=True, exist_ok=True)
        if mode == "report-only" or use_cache:
            env["LOCALGUARD_HISTORY_FILE"] = str(history_dir / f"scan_user_{ctx.run.owner_user_id}.json")
        if not use_cache and mode != "report-only":
            env["LOCALGUARD_DISABLE_HISTORY"] = "1"
        # Garak options (Phase 1): generations and optional parallel_attempts
        try:
            env["LOCALGUARD_GARAK_GENERATIONS"] = str(max(1, int(ctx.params.get("garak_generations", 1))))
        except (TypeError, ValueError):
            env["LOCALGUARD_GARAK_GENERATIONS"] = "1"
        pa = ctx.params.get("garak_parallel_attempts")
        if pa is not None and str(pa).strip():
            try:
                env["LOCALGUARD_GARAK_PARALLEL_ATTEMPTS"] = str(int(pa))
            except (TypeError, ValueError):
                pass
        # Target key: use api_key_for_openai_env so placeholders (lmstudio, ollama, etc.) become None.
        # We never send placeholders to real APIs; local servers get override or "sk-local".
        target_key = api_key_for_openai_env(ctx.settings, ctx.model_profile)
        if ctx.model_profile.provider_kind in {ProviderKind.openai, ProviderKind.openai_compat}:
            env["OPENAI_API_KEY"] = (
                target_key
                or getattr(ctx.settings, "localguard_openai_api_key_override", None)
                or "sk-local"
            )
        if ctx.model_profile.provider_kind == ProviderKind.anthropic and decrypt_api_key(ctx.settings, ctx.model_profile):
            env["ANTHROPIC_API_KEY"] = decrypt_api_key(ctx.settings, ctx.model_profile)
        if ctx.model_profile.provider_kind == ProviderKind.google and decrypt_api_key(ctx.settings, ctx.model_profile):
            env["GOOGLE_API_KEY"] = decrypt_api_key(ctx.settings, ctx.model_profile)
        if ctx.model_profile.provider_kind == ProviderKind.huggingface and decrypt_api_key(ctx.settings, ctx.model_profile):
            env["HF_TOKEN"] = decrypt_api_key(ctx.settings, ctx.model_profile)

        # For local targets, prefer OpenAI-compatible base URL via OPENAI_BASE_URL or OLLAMA_URL as needed.
        if ctx.model_profile.base_url:
            # LocalGuard's garak subprocess looks at OPENAI_BASE_URL -> OPENAI_API_BASE mapping.
            env["OPENAI_BASE_URL"] = ctx.model_profile.base_url.rstrip("/")
            target_headers = None
            if target_key and ctx.model_profile.provider_kind in {ProviderKind.openai, ProviderKind.openai_compat}:
                target_headers = {"Authorization": f"Bearer {target_key}"}
            check, body = http_get_json(
                env["OPENAI_BASE_URL"].rstrip("/") + "/models",
                timeout_s=3.0,
                headers=target_headers,
            )
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
            judge_base = judge_profile.base_url.rstrip("/")
            env["LOCAL_JUDGE_OPENAI_BASE_URL"] = judge_base
            if env.get("OPENAI_BASE_URL") != judge_base:
                with ctx.log_path.open("a", encoding="utf-8") as f:
                    f.write(
                        "[localguard] target uses OPENAI_BASE_URL; judge uses LOCAL_JUDGE_OPENAI_BASE_URL.\n"
                    )
            preflight_headers = None
            if judge_key and judge_profile.provider_kind in {ProviderKind.openai, ProviderKind.openai_compat}:
                preflight_headers = {"Authorization": f"Bearer {judge_key}"}
            check, body = http_get_json(
                judge_base + "/models",
                timeout_s=3.0,
                headers=preflight_headers,
            )
            with ctx.log_path.open("a", encoding="utf-8") as f:
                f.write(f"[preflight] judge GET {judge_base}/models ok={check.ok} status={check.status} ms={check.duration_ms} err={check.error}\n")
        if judge_key and env["LOCAL_JUDGE_PROVIDER"] in {"openai", "anthropic", "google", "hf"}:
            # Judge key: set LOCAL_JUDGE_OPENAI_API_KEY so Garak/target keep OPENAI_API_KEY (target key or sk-local).
            if judge_profile.provider_kind in {ProviderKind.openai, ProviderKind.openai_compat}:
                env["LOCAL_JUDGE_OPENAI_API_KEY"] = judge_key
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
        for p in sorted(out_dir.glob("garak_out*.report.jsonl")):
            artifacts.append(("jsonl", p, "application/json"))
        for p in sorted(out_dir.glob("garak_out*.report.html")):
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

