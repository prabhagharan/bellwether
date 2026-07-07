from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class NotifyOutcome:
    ok: bool


@runtime_checkable
class Notifier(Protocol):
    def notify(self, webhook_url: str, payload: dict) -> NotifyOutcome: ...
