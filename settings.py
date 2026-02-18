import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


def _env_bool(name: str, default: bool = False) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    # Core
    app_env: str
    base_url: str
    session_secret: str
    master_key: str

    # DB
    db_url: str

    # Paths
    artifacts_dir: Path
    custom_probes_dir: Path
    custom_augustus_templates_dir: Path

    # Tool paths / working dirs
    augustus_dir: Path
    agentdojo_dir: Path
    localguard_dir: Path

    # Optional: API key sent to local OpenAI-compat judge when profile has none.
    # Some local servers require a header but validate the value; set this to what your server accepts.
    localguard_openai_api_key_override: str | None

    # Optional: path to augustus binary, otherwise use go run.
    augustus_bin: str | None

    # Bootstrap admin
    bootstrap_admin_username: str | None
    bootstrap_admin_password: str | None

    # Safety controls
    allow_admin_probe_code: bool


def load_settings(repo_root: Path) -> Settings:
    # Repo-root anchored defaults (so running from anywhere is consistent).
    artifacts_dir = Path(_env("SUITE_WEB_ARTIFACTS_DIR", str(repo_root / "suite_web" / "artifacts"))).resolve()
    custom_probes_dir = Path(_env("SUITE_WEB_CUSTOM_PROBES_DIR", str(repo_root / "suite_web" / "custom_probes"))).resolve()
    custom_augustus_templates_dir = Path(
        _env(
            "SUITE_WEB_CUSTOM_AUGUSTUS_TEMPLATES_DIR",
            str(repo_root / "suite_web" / "custom_augustus_templates"),
        )
    ).resolve()

    default_db_path = repo_root / "suite_web.sqlite3"
    db_url = _env("SUITE_WEB_DB_URL", f"sqlite:///{default_db_path}") or f"sqlite:///{default_db_path}"

    session_secret = _env("SUITE_WEB_SESSION_SECRET", "dev-insecure-session-secret") or "dev-insecure-session-secret"
    master_key = _env("SUITE_WEB_MASTER_KEY", "") or ""

    # In team/internal mode, we require a master key to encrypt API keys.
    if master_key == "" and _env_bool("SUITE_WEB_ALLOW_INSECURE_NO_MASTER_KEY", False) is False:
        raise RuntimeError(
            "SUITE_WEB_MASTER_KEY is required (set SUITE_WEB_ALLOW_INSECURE_NO_MASTER_KEY=1 to bypass for dev)."
        )

    return Settings(
        app_env=_env("SUITE_WEB_ENV", "dev") or "dev",
        base_url=_env("SUITE_WEB_BASE_URL", "http://localhost:8080") or "http://localhost:8080",
        session_secret=session_secret,
        master_key=master_key,
        db_url=db_url,
        artifacts_dir=artifacts_dir,
        custom_probes_dir=custom_probes_dir,
        custom_augustus_templates_dir=custom_augustus_templates_dir,
        augustus_dir=Path(_env("SUITE_WEB_AUGUSTUS_DIR", str(repo_root / "augustus-local-llm-openai"))).resolve(),
        agentdojo_dir=Path(_env("SUITE_WEB_AGENTDOJO_DIR", str(repo_root / "agentdojo-quickstart"))).resolve(),
        localguard_dir=Path(_env("SUITE_WEB_LOCALGUARD_DIR", str(repo_root / "LocalGuard"))).resolve(),
        localguard_openai_api_key_override=_env("SUITE_WEB_LOCALGUARD_OPENAI_API_KEY"),
        augustus_bin=_env("SUITE_WEB_AUGUSTUS_BIN"),
        bootstrap_admin_username=_env("SUITE_WEB_BOOTSTRAP_ADMIN_USERNAME"),
        bootstrap_admin_password=_env("SUITE_WEB_BOOTSTRAP_ADMIN_PASSWORD"),
        allow_admin_probe_code=_env_bool("SUITE_WEB_ALLOW_ADMIN_PROBE_CODE", False),
    )

