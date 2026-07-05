from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class FigureCreate(BaseModel):
    name: str
    type: str
    aliases: list[str] = []


class FigureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    type: str
    aliases: list[str]


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


class ProgramRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    module: str
    version: int
    holdout_score: float | None
    is_champion: bool


class EvalRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    module: str
    split: str
    metric: str
    score: float
    n: int


class OptimizeRead(BaseModel):
    module: str
    version: int
    challenger_holdout: float
    champion_holdout: float
    promoted: bool
