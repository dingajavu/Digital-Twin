"""Owner notifications. Use config -> (pushover | none)"""

from __future__ import annotations

import logging
import os
from typing import Protocol

import requests

from .config import NotificationsConfig

log = logging.getLogger(__name__)

# Anything with a send() method can be used as a notifier.
class Notifier(Protocol):
    def send(self, text: str) -> None: ...


# Sends nothing and keeps messages in a list. Used by tests, evals and the `none` provider.
class NullNotifier:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


# Sends a push notification to the owner's phone through Pushover.
class PushoverNotifier:
    URL = "https://api.pushover.net/1/messages.json"

    def __init__(self, user: str | None, token: str | None):
        self.user, self.token = user, token

    def send(self, text: str) -> None:
        if not (self.user and self.token):
            log.warning("Pushover credentials missing; notification skipped")
            return
        try:
            requests.post(
                self.URL,
                # Pushover limits message length, so longer text is cut off.
                data={"token": self.token, "user": self.user, "message": text[:1024]},
                timeout=5,
            ).raise_for_status()
        except requests.RequestException:
            # A failed notification should never break the conversation;
            # the record is already safe in the database.
            log.exception("Pushover notification failed")

# Pick the notifier for the configured provider.
def build_notifier(cfg: NotificationsConfig) -> Notifier:
    if cfg.provider == "pushover":
        return PushoverNotifier(os.getenv(cfg.user_env), os.getenv(cfg.token_env))
    if cfg.provider == "none":
        return NullNotifier()
    raise ValueError(f"Unsupported notifications provider: {cfg.provider}")