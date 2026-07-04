from datetime import datetime

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
    connector_type: str
    config: dict
    provenance: str = "primary"


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
