"""Typed configuration loaded from YAML.

All deployment-specific choices (persona, knowledge, model, tools, branding)
come from here, so the same code can power a personal twin or a business
assistant by swapping the config file.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Used when neither a path nor the TWIN_CONFIG environment variable is given.
DEFAULT_CONFIG_PATH = "config/twin.yaml"


# Raised for a missing file, missing section or invalid value, so bad config fails at startup.
class ConfigError(ValueError):
    pass


# Neutral default for the block that introduces approved answers. {name} is substituted.
DEFAULT_APPROVED_ANSWERS_INTRO = (
    "The following answers were written and approved by {name}. "
    "They are authoritative: prefer them over anything else, keep their facts unchanged, "
    "and adapt only the wording to fit the conversation."
)


# Who the twin represents. {name} in the role and rules is replaced with the name.
@dataclass
class Persona:
    name: str
    role: str
    scope: str
    rules: list[str] = field(default_factory=list)
    # Introduces approved answers from the knowledge base in each turn's context.
    approved_answers_intro: str = DEFAULT_APPROVED_ANSWERS_INTRO


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


# A hex colour such as #1565c0. Short forms like #fff are allowed.
HEX_COLOUR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


# Brand colours for one theme. Any colour left out keeps the stylesheet's default.
@dataclass
class ThemeColours:
    primary: str | None = None       # Buttons, links and highlights
    primary_ink: str | None = None   # Text on top of the primary colour
    secondary: str | None = None     # Visitor message bubbles
    secondary_ink: str | None = None # Text on visitor message bubbles
    accent: str | None = None        # Muted text such as placeholders

    def __post_init__(self):
        for key, value in vars(self).items():
            if value is not None and not HEX_COLOUR.match(str(value)):
                raise ConfigError(f"ui.colours: {key} must be a hex colour like #1565c0, got {value!r}")


# Text and brand colours for the chat page. Light and dark mode each get their own colours,
# because a colour that reads well on a dark background often doesn't on a light one.
@dataclass
class UIConfig:
    title: str = "Digital Twin"
    description: str = ""
    examples: list[str] = field(default_factory=list)
    colours: dict[str, ThemeColours] = field(default_factory=dict)

    def __post_init__(self):
        unknown = set(self.colours) - {"dark", "light"}
        if unknown:
            raise ConfigError(
                f"ui.colours must be split into 'dark' and 'light' themes, found: {sorted(unknown)}"
            )
        # Unknown colour names fail here as a TypeError, which _section reports.
        self.colours = {
            mode: c if isinstance(c, ThemeColours) else ThemeColours(**(c or {}))
            for mode, c in self.colours.items()
        }


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
    # The file this config was loaded from. Its name keeps each deployment's eval baseline separate.
    source_path: Path | None = None

    def resolve(self, path: str) -> Path:
        """Resolve a config-relative path against the project root."""
        p = Path(path)
        return p if p.is_absolute() else self.base_dir / p


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
        source_path=path.resolve(),
    )
    if cfg.agent.max_tool_rounds < 1:
        raise ConfigError("agent.max_tool_rounds must be at least 1")
    return cfg
