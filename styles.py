"""Styling constants for the digital twin Gradio app.

The palette and typography follow dingajavu.github.io. app.py swaps the primary,
secondary and accent hex values below for the colours in the config file.
"""

# Global stylesheet injected when the app launches. The selectors target Gradio's own class
# names, so some may need updating after a Gradio upgrade. Keep the primary, secondary and
# accent hex values in sync with DEFAULT_COLOURS in app.py, which swaps them for config colours.
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;500;700;800&display=swap');

:root {
  --twin-primary: #4fc3cf;
  --twin-primary-ink: #0f1a1f;
  --twin-secondary: #0b6f79;
  --twin-bg: #0f1a1f;
  --twin-surface: #15242a;
  --twin-surface-2: #1b2c33;
  --twin-border: #26363d;
  --twin-border-strong: #35494f;
  --twin-text: #e6eef1;
  --twin-muted: #9db0b8;
  --twin-radius: 4px;
  --twin-font: 'Schibsted Grotesk', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
}

/* Light mode: Gradio adds `.dark` to <body> when dark; absence = light.
   Same values as the website's light theme, where the accent darkens for contrast. */
body:not(.dark) {
  --twin-primary: #0b6f79;
  --twin-primary-ink: #ffffff;
  --twin-bg: #f3f6f7;
  --twin-surface: #ffffff;
  --twin-surface-2: #e9eff1;
  --twin-border: #d3dce0;
  --twin-border-strong: #b4c2c8;
  --twin-text: #14232b;
  --twin-muted: #54666f;
}

footer, .built-with, .show-api, .api-docs { display: none !important; }

html, body, gradio-app { background: var(--twin-bg) !important; }

/*   Stable layout   */
.gradio-container {
  background: var(--twin-bg) !important;
  color: var(--twin-text) !important;
  font-family: var(--twin-font) !important;
  --border-color-primary: var(--twin-border) !important;
  width: 100% !important;
  max-width: 880px !important;
  min-width: 0 !important;
  margin: 0 auto !important;
  padding: 32px 24px 48px !important;
}
.gradio-container .main, .gradio-container .contain, .gradio-container .wrap {
  width: 100% !important;
  max-width: 100% !important;
  min-width: 0 !important;
}
.gradio-container * { min-width: 0; }

/*   Title   */
.gradio-container h1 {
  color: var(--twin-text) !important;
  font-size: 34px !important;
  font-weight: 800 !important;
  letter-spacing: -0.02em !important;
  margin: 4px 0 8px !important;
  text-align: left !important;
}

/*   Corners on structural pieces   */
.chatbot, .chatbot *, .block, .form,
button, input, textarea,
.examples button {
  border-radius: var(--twin-radius) !important;
}

/*   Block surfaces   */
.block, .form, .styler { background: transparent !important; box-shadow: none !important; }

/*   Hide the Chatbot label / header strip   */
.chatbot > .block-label,
.chatbot > label,
.chatbot .label-wrap,
.chatbot .block-label,
.chatbot > .label-container {
  display: none !important;
}

/*   Chatbot frame   */
.chatbot, .chatbot.block {
  background: var(--twin-surface) !important;
  border: 1px solid var(--twin-border) !important;
  min-height: 460px !important;
  box-shadow: none !important;
}
.chatbot .placeholder, .chatbot .placeholder * { color: var(--twin-muted) !important; }

/*   Message rows: strip parent backgrounds   */
.message-row,
.message-row > div,
.message-row .role,
.message-wrap, .bubble-wrap {
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
}

/*   Reset borders on every bubble variant first   */
.message-row .message,
.message-row .message-bubble,
.message-row .bubble {
  border: 0 !important;
  box-shadow: none !important;
  padding: 6px 10px !important;
}

/*   Bubble backgrounds (broad to cover Gradio variants)   */
.message-row.user-row .message,
.message-row.user-row .message-bubble,
.message-row.user-row .bubble,
.message-row[data-role="user"] .message,
.message-row[data-role="user"] .message-bubble {
  background: var(--twin-secondary) !important;
  color: #ffffff !important;
}

.message-row.bot-row .message,
.message-row.bot-row .message-bubble,
.message-row.bot-row .bubble,
.message-row[data-role="assistant"] .message,
.message-row[data-role="assistant"] .message-bubble {
  background: var(--twin-surface-2) !important;
  color: var(--twin-text) !important;
}

/*   Accent stripe  
   Apply to every common bubble class for assistant rows (we don't know which
   one the running Gradio uses), then suppress on any *nested* instance so the
   stripe lands on the outermost matching element only — exactly one stripe. */
.message-row.bot-row .message,
.message-row.bot-row .bubble,
.message-row.bot-row .message-bubble,
.message-row[data-role="assistant"] .message,
.message-row[data-role="assistant"] .bubble,
.message-row[data-role="assistant"] .message-bubble {
  border-left: 2px solid var(--twin-primary) !important;
}

.message-row.bot-row .message .message,
.message-row.bot-row .message .bubble,
.message-row.bot-row .message .message-bubble,
.message-row.bot-row .bubble .message,
.message-row.bot-row .bubble .bubble,
.message-row.bot-row .bubble .message-bubble,
.message-row.bot-row .message-bubble .message,
.message-row.bot-row .message-bubble .bubble,
.message-row.bot-row .message-bubble .message-bubble,
.message-row[data-role="assistant"] .message .message,
.message-row[data-role="assistant"] .message .bubble,
.message-row[data-role="assistant"] .message .message-bubble,
.message-row[data-role="assistant"] .bubble .message,
.message-row[data-role="assistant"] .bubble .bubble,
.message-row[data-role="assistant"] .bubble .message-bubble,
.message-row[data-role="assistant"] .message-bubble .message,
.message-row[data-role="assistant"] .message-bubble .bubble,
.message-row[data-role="assistant"] .message-bubble .message-bubble {
  border-left: 0 !important;
}

/*   Uniform font size in bubbles  
   The "first paragraph different size" was caused by a leaky `.prose p:first-of-type`
   selector. Force every paragraph in a bubble to the same size. */
.message-row .message,
.message-row .message-bubble,
.message-row .bubble {
  font-size: 15px !important;
  line-height: 1.55 !important;
}
.message-row .message p,
.message-row .message-bubble p,
.message-row .bubble p,
.message-row .prose p {
  font-size: 15px !important;
  line-height: 1.55 !important;
  margin: 0 0 8px !important;
  color: inherit !important;
}
.message-row .message p:last-child,
.message-row .message-bubble p:last-child,
.message-row .bubble p:last-child,
.message-row .prose p:last-child { margin-bottom: 0 !important; }

/* Strip stray internal borders/backgrounds from anything inside a bubble */
.message-row .message *,
.message-row .message-bubble *,
.message-row .bubble * {
  background: transparent !important;
  border-color: transparent !important;
  box-shadow: none !important;
  color: inherit !important;
}
.message-row .message a,
.message-row .message-bubble a {
  color: var(--twin-primary) !important;
  text-decoration: underline;
}
.message-row.user-row .message a,
.message-row[data-role="user"] .message a {
  color: #ffffff !important;
}

/*   Input row alignment   */
.input-row,
.gr-input-row,
.chat-input-row,
form[class*="input"] { align-items: stretch !important; }

textarea, input[type="text"] {
  background: var(--twin-surface) !important;
  border: 1px solid var(--twin-border) !important;
  color: var(--twin-text) !important;
  font-family: var(--twin-font) !important;
  font-size: 15px !important;
  padding: 12px 14px !important;
  line-height: 1.4 !important;
  min-height: 48px !important;
}
textarea:focus, input[type="text"]:focus {
  border-color: var(--twin-primary) !important;
  outline: none !important;
  box-shadow: 0 0 0 1px var(--twin-primary) !important;
}
textarea::placeholder, input::placeholder { color: var(--twin-muted) !important; }

/*   Buttons   */
button {
  font-family: var(--twin-font) !important;
  font-size: 14px !important;
  font-weight: 700 !important;
  border: 1px solid var(--twin-border) !important;
  background: transparent !important;
  color: var(--twin-text) !important;
  padding: 0 16px !important;
  min-height: 48px !important;
  align-self: stretch !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease, filter 0.12s ease;
}
button:hover { border-color: var(--twin-primary) !important; color: var(--twin-primary) !important; }

button.primary,
button[variant="primary"],
button.submit,
button.submit-button,
.submit-button,
button.lg.primary {
  background: var(--twin-primary) !important;
  border: 1px solid var(--twin-primary) !important;
  color: var(--twin-primary-ink) !important;
  min-height: 48px !important;
  align-self: stretch !important;
  padding: 0 14px !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
}
button.primary:hover,
button.submit:hover,
.submit-button:hover,
button.lg.primary:hover {
  background: var(--twin-primary) !important;
  border-color: var(--twin-primary) !important;
  color: var(--twin-primary-ink) !important;
  filter: brightness(1.1);
}

/*   Submit-button icon: center vertically and size correctly   */
button.submit svg,
button.submit-button svg,
.submit-button svg,
button.primary svg,
button[variant="primary"] svg {
  width: 18px !important;
  height: 18px !important;
  margin: 0 auto !important;
  display: block !important;
  align-self: center !important;
  color: var(--twin-primary-ink) !important;
  fill: currentColor !important;
  stroke: currentColor !important;
}

/*   Examples   */
.examples, .examples-holder, [data-testid="examples"] {
  background: transparent !important;
  padding: 0 !important;
  margin-top: 14px !important;
}
.examples table, .examples-table { background: transparent !important; border: 0 !important; }
.examples button, .example, .examples td button, [data-testid="examples"] button {
  background: var(--twin-surface) !important;
  border: 1px solid var(--twin-border) !important;
  color: var(--twin-text) !important;
  font-family: var(--twin-font) !important;
  font-size: 14px !important;
  font-weight: 400 !important;
  padding: 10px 14px !important;
  text-align: left !important;
  min-height: 0 !important;
  align-self: auto !important;
  display: inline-block !important;
}
.examples button:hover, .example:hover, [data-testid="examples"] button:hover {
  border-color: var(--twin-primary) !important;
  color: var(--twin-primary) !important;
  background: var(--twin-surface) !important;
}

/*   Icon buttons (clear, retry, copy)   */
.icon-button, .chatbot .icon-button {
  color: var(--twin-muted) !important;
  background: transparent !important;
  border: 0 !important;
  min-height: 0 !important;
  align-self: auto !important;
  padding: 4px !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
}
.icon-button:hover, .chatbot .icon-button:hover { color: var(--twin-primary) !important; }

/*   Scrollbar   */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: var(--twin-bg); }
::-webkit-scrollbar-thumb { background: var(--twin-border-strong); }
::-webkit-scrollbar-thumb:hover { background: var(--twin-primary); }

/*   Selection   */
::selection { background: var(--twin-primary); color: var(--twin-primary-ink); }

/*   Mobile   */
@media (max-width: 640px) {
  .gradio-container { padding: 22px 14px 36px !important; }
  .gradio-container h1 { font-size: 28px !important; }
}
"""

# Runs once on page load. Keeps the cursor in the message box so visitors can keep typing.
JS = """
() => {
  const focusInput = () => {
    const areas = document.querySelectorAll('textarea');
    if (areas.length) areas[areas.length - 1].focus();
  };
  setTimeout(focusInput, 300);

  // Re-focus the message field whenever Gradio re-enables it
  // (i.e. after the assistant finishes responding).
  const watchTextarea = (area) => {
    if (area.dataset.twinWatched) return;
    area.dataset.twinWatched = '1';
    let wasDisabled = area.disabled || area.readOnly;
    new MutationObserver(() => {
      const isDisabled = area.disabled || area.readOnly;
      if (wasDisabled && !isDisabled) area.focus();
      wasDisabled = isDisabled;
    }).observe(area, { attributes: true, attributeFilter: ['disabled', 'readonly'] });
  };

  const scan = () => document.querySelectorAll('textarea').forEach(watchTextarea);
  setTimeout(scan, 500);
  new MutationObserver(scan).observe(document.body, { childList: true, subtree: true });
}
"""
