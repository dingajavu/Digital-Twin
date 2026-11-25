"""Builds the system from the config details and knowledge sources"""

from __future__ import annotations

import re

from .config import KnowledgeSource, TwinConfig


# Return the text of one knowledge source, extracting it page by page for PDFs.
def read_source(source: KnowledgeSource, cfg: TwinConfig) -> str:
    path = cfg.resolve(source.path)
    if source.type == "pdf":
        # Imported here so that text-only setups don't load the PDF library.
        from pypdf import PdfReader


        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return path.read_text(encoding="utf-8")


# Replace every match of each pattern, so private details never reach the model.
def redact(text: str, patterns: list[str], replacement: str) -> str:
    for pattern in patterns:
        text = re.sub(pattern, replacement, text)
    return text


# Put the persona's name into config text. Every {name} substitution goes through here.
def fill_name(text: str, name: str) -> str:
    return text.replace("{name}", name)


def build_system_prompt(cfg: TwinConfig, source_texts: dict[str, str] | None = None) -> str:
    """Assemble the system prompt.
    
    `source_texts` lets callers (tests, evals)
    inject document text insted of reading files, keyed by source title. 
    """
    # The prompt is built in order: role, then documents, then rules.
    persona = cfg.persona
    sections = [
        "# Your role",
        fill_name(persona.role, persona.name).strip(),
        f"Your scope is: {persona.scope}",
        "# Context",
    ]
    for source in cfg.knowledge_sources:
        # Injected text takes priority; otherwise read the real file.
        text = (source_texts or {}).get(source.title)
        if text is None:
            text = read_source(source, cfg)
        text = redact(text, cfg.privacy.redact_patterns, cfg.privacy.redaction_text)
        sections.append(f"## {source.title}\n\n{text.strip()}")

    # Rules come last so they sit closest to the conversation.
    sections.append("# Rules")
    sections.append("\n".join(f"- {fill_name(rule, persona.name)}" for rule in persona.rules))
    return "\n\n".join(sections)


def format_approved_answers(matches, cfg: TwinConfig) -> str:
    """Per-turn context block for curated answers from the knowledge base."""
    persona = cfg.persona
    # The introduction comes from the config, so it can name a person or a business.
    lines = [
        "# Approved answers",
        fill_name(persona.approved_answers_intro, persona.name).strip(),
    ]
    # Each match becomes its own Q and A block.
    for m in matches:
        lines.append(f"Q: {m.question}\nA: {m.answer}")
    return "\n\n".join(lines)
