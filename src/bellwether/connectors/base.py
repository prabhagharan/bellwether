from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RawItem:
    external_id: str
    text: str
    url: str | None
    published_at: datetime


@runtime_checkable
class SourceConnector(Protocol):
    def fetch(self) -> list[RawItem]:
        ...
