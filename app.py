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

# styles.py ships with these default brand colours; config can override them.
DEFAULT_COLOURS = {"primary": "#4fc3cf", "secondary": "#0b6f79", "accent": "#9db0b8"}


# Swap the default brand hex values in the stylesheet for any colours set in the config.
def themed_css(colours: dict) -> str:
    css = CSS
    for key, default in DEFAULT_COLOURS.items():
        if colours.get(key):
            css = css.replace(default, colours[key])
    return css


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
