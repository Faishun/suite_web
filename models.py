from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class ProviderKind(str, Enum):
    openai_compat = "openai_compat"
    openai = "openai"
    ollama = "ollama"
    anthropic = "anthropic"
    google = "google"
    huggingface = "huggingface"


class ToolKind(str, Enum):
    garak = "garak"
    augustus = "augustus"
    agentdojo = "agentdojo"
    localguard = "localguard"


class RunStatus(str, Enum):
    pending = "PENDING"
    running = "RUNNING"
    succeeded = "SUCCEEDED"
    failed = "FAILED"
    cancelled = "CANCELLED"


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    is_admin: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class ModelProfile(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int = Field(index=True)

    name: str = Field(index=True)
    provider_kind: ProviderKind = Field(index=True)
    model: str = Field(default="")

    # For OpenAI-compatible or self-hosted providers.
    base_url: str = Field(default="")

    # Encrypted at rest.
    api_key_enc: str = Field(default="")

    # JSON string for tool-specific config (headers, timeouts, etc).
    extra_json: str = Field(default="{}")

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int = Field(index=True)
    model_profile_id: int | None = Field(default=None, index=True)

    tool_kind: ToolKind = Field(index=True)
    status: RunStatus = Field(default=RunStatus.pending, index=True)

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    started_at: datetime | None = Field(default=None, index=True)
    finished_at: datetime | None = Field(default=None, index=True)

    params_json: str = Field(default="{}")

    exit_code: int | None = Field(default=None)
    log_path: str = Field(default="")
    pid: int | None = Field(default=None, index=True)


class Artifact(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)

    kind: str = Field(index=True)  # log,jsonl,html,pdf,dir,...
    path: str
    mime: str = Field(default="application/octet-stream")

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class CustomGarakProbe(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int = Field(index=True)

    # module filename (without .py) and class name inside it
    module_name: str = Field(index=True)
    class_name: str = Field(default="CustomProbe")

    title: str = Field(index=True)
    doc_uri: str = Field(default="")
    goal: str = Field(default="")
    tags_csv: str = Field(default="")
    primary_detector: str = Field(default="always.Fail")
    active: bool = Field(default=False, index=True)
    prompts_text: str = Field(default="")  # one prompt per line

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class CustomAugustusTemplate(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: int = Field(index=True)

    # Probe ID must be fully qualified, e.g. "custom.MyProbe"
    template_id: str = Field(index=True)

    name: str = Field(index=True)
    author: str = Field(default="suite_web")
    description: str = Field(default="")
    goal: str = Field(default="")
    detector: str = Field(default="always.Always")
    severity: str = Field(default="info")
    tags_csv: str = Field(default="")
    prompts_text: str = Field(default="")

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)

