from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class FigureCreate(BaseModel):
    name: str
    type: str
    aliases: list[str] = []
    discover: bool = True


class FigureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    type: str
    aliases: list[str]
    discovery_status: str
    wikidata_id: str | None


class SourceCreate(BaseModel):
    connector_type: Literal["rss", "x", "youtube", "news"]
    config: dict
    provenance: Literal["primary", "reported"] = "primary"


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    figure_id: int
    connector_type: str
    config: dict
    provenance: str
    origin: str
    enabled: bool
    status: str
    verified: bool
    discovery_confidence: float | None


class SourceUpdate(BaseModel):
    enabled: bool


class StatementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    figure_id: int
    source_id: int
    text: str
    url: str | None
    provenance: str
    published_at: datetime
    status: str


class ExtractionCorrect(BaseModel):
    direction: str
    magnitude: str
    entities: list[str]
    evidence_quote: str


class ReviewSubmit(BaseModel):
    is_relevant: bool
    extraction: ExtractionCorrect | None = None


class ReviewQueueItem(BaseModel):
    statement_id: int
    text: str
    figure_name: str
    current_extraction: dict | None


class DiscoveryQueueItem(BaseModel):
    source_id: int
    figure_id: int
    figure_name: str
    connector_type: str
    config: dict
    discovery_confidence: float | None
    discovery_meta: dict | None


class DiscoveryDecision(BaseModel):
    decision: str
