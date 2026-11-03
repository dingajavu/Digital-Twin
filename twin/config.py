"""Typed configuration loaded from YAML.

All deployment-specific choices (persona, knowledge, model, tools, branding)
come from here, so the same code can power a personal twin or a business
assistant by swapping the config file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Used when neither a path nor the TWIN_CONFIG environment variable is given.
DEFAULT_CONFIG_PATH = "config/twin.yaml"


# Who the twin represents. {name} in the role and rules is replaced with the name.
@dataclass
class Persona:
    name: str
    role: str
    scope: str
    rules: list[str] = field(default_factory=list)


# One document the twin can draw on. The title becomes its heading in the prompt.
@dataclass
class KnowledgeSource:
    type: str  # "pdf" | "text"
    path: str
    title: str


# Regex patterns scrubbed from documents before they reach the model.
@dataclass
class Privacy:
    redact_patterns: list[str] = field(default_factory=list)
    redaction_text: str = "[redacted]"


# Which model to call and where. extra_params are passed straight to the API.
@dataclass
class LLMConfig:
    model: str
    provider: str = "openai"
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    extra_params: dict = field(default_factory=dict)


# Limits on the agent loop, which control cost and stop runaway tool calls.
@dataclass
class AgentConfig:
    max_tool_rounds: int = 5
    max_history_messages: int = 10


# Settings for the approved answers database and its matching.
@dataclass
class KnowledgeBaseConfig:
    enabled: bool = True
    db_path: str = "data/twin.db"
    # Environment variables for a hosted Turso database. If the URL is set, it replaces db_path.
    db_url_env: str | None = None
    db_token_env: str | None = None
    match_threshold: float = 0.55
    max_matches: int = 3
    log_interactions: bool = True
    seed_file: str | None = None


# How the owner is alerted. Credentials are read from these environment variables.
@dataclass
class NotificationsConfig:
    provider: str = "none"
    user_env: str = "PUSHOVER_USER"
    token_env: str = "PUSHOVER_TOKEN"


# Which registered tools the model is allowed to call.
@dataclass
class ToolsConfig:
    enabled: list[str] = field(default_factory=list)


# Text and brand colours for the chat page.
@dataclass
class UIConfig:
    title: str = "Digital Twin"
    description: str = ""
    examples: list[str] = field(default_factory=list)
    colours: dict = field(default_factory=dict)


# Settings for the eval harness. A case must score pass_threshold or above with the judge.
@dataclass
class EvaluationConfig:
    judge_model: str | None = None
    dataset: str = "evals/dataset.yaml"
    pass_threshold: int = 4


# The complete configuration, with one attribute per section of the YAML file.
@dataclass
class TwinConfig:
    persona: Persona
    knowledge_sources: list[KnowledgeSource]
    llm: LLMConfig
    privacy: Privacy = field(default_factory=Privacy)
    agent: AgentConfig = field(default_factory=AgentConfig)
    knowledge_base: KnowledgeBaseConfig = field(default_factory=KnowledgeBaseConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    base_dir: Path = Path(".")

    def resolve(self, path: str) -> Path:
        """Resolve a config-relative path against the project root."""
        p = Path(path)
        return p if p.is_absolute() else self.base_dir / p


# Raised for a missing file, missing section or invalid value, so bad config fails at startup.
class ConfigError(ValueError):
    pass


# Build one dataclass from a YAML section, using its defaults if the section is absent.
def _section(raw: dict, key: str, cls, required: bool = False):
    data = raw.get(key)
    if data is None:
        if required:
            raise ConfigError(f"Missing required config section: '{key}'")
        return cls()
    try:
        return cls(**data)
    except TypeError as exc:  # unknown or missing keys
        raise ConfigError(f"Invalid '{key}' section: {exc}") from exc


def load_config(path: str | os.PathLike | None = None) -> TwinConfig:
    """Load config from `path`, the TWIN_CONFIG env var, or the default."""
    path = Path(path or os.getenv("TWIN_CONFIG", DEFAULT_CONFIG_PATH))
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Knowledge sources are a list, so they are built here instead of through _section.
    sources = [KnowledgeSource(**s) for s in raw.get("knowledge_sources", [])]
    for s in sources:
        if s.type not in {"pdf", "text"}:
            raise ConfigError(f"Unsupported knowledge source type: {s.type}")

    cfg = TwinConfig(
        persona=_section(raw, "persona", Persona, required=True),
        knowledge_sources=sources,
        llm=_section(raw, "llm", LLMConfig, required=True),
        privacy=_section(raw, "privacy", Privacy),
        agent=_section(raw, "agent", AgentConfig),
        knowledge_base=_section(raw, "knowledge_base", KnowledgeBaseConfig),
        notifications=_section(raw, "notifications", NotificationsConfig),
        tools=_section(raw, "tools", ToolsConfig),
        ui=_section(raw, "ui", UIConfig),
        evaluation=_section(raw, "evaluation", EvaluationConfig),
        # Config lives in <root>/config/, so paths resolve from <root>.
        base_dir=path.resolve().parent.parent,
    )
    if cfg.agent.max_tool_rounds < 1:
        raise ConfigError("agent.max_tool_rounds must be at least 1")
    return cfg
