"""Gradio UI front end. Everything deployment-specific comes from the config file."""

import logging

import gradio as gr
from dotenv import load_dotenv

from styles import CSS, JS
from twin import Twin, load_config

# Load API keys from .env, letting the file win over existing environment variables.
load_dotenv(override=True)
# Show timestamped logs for tool calls and failures in the terminal.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# The style variable each config colour sets.
COLOUR_VARIABLES = {
    "primary": "--twin-primary",
    "primary_ink": "--twin-primary-ink",
    "secondary": "--twin-secondary",
    "secondary_ink": "--twin-secondary-ink",
    "accent": "--twin-muted",
}
# The same selectors styles.py uses for each theme. Gradio adds .dark to <body> in dark mode.
THEME_SELECTORS = {"dark": ":root", "light": "body:not(.dark)"}


# Add the config's colours after the stylesheet so they override its defaults, theme by theme.
def themed_css(colours: dict) -> str:
    overrides = []
    for mode, selector in THEME_SELECTORS.items():
        theme = colours.get(mode)
        if theme is None:
            continue
        lines = [
            f"  {variable}: {getattr(theme, key)};"
            for key, variable in COLOUR_VARIABLES.items()
            if getattr(theme, key)
        ]
        if lines:
            overrides.append(f"{selector} {{\n" + "\n".join(lines) + "\n}")
    return CSS + ("\n/* Brand colours from the config */\n" + "\n".join(overrides) + "\n" if overrides else "")


# Build the chat page. All visible text comes from the config so this file stays generic.
def build_app(twin: Twin) -> gr.ChatInterface:
    ui = twin.cfg.ui
    return gr.ChatInterface(
        # Gradio calls this with each message and the chat history.
        twin.chat,
        examples=ui.examples or None,
        title=ui.title,
        description=ui.description,
        chatbot=gr.Chatbot(show_label=False),
    )


if __name__ == "__main__":
    # Uses the TWIN_CONFIG environment variable if set, otherwise config/twin.yaml.
    cfg = load_config()
    # Connects the model, knowledge base, tools and system prompt.
    twin = Twin.from_config(cfg)
    # The stylesheet, script and theme are applied when the app launches.
    build_app(twin).launch(css=themed_css(cfg.ui.colours), js=JS, theme=gr.themes.Base())
