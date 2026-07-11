import os
from bellwether.connectors.base import SourceConnector
from bellwether.connectors.rss import RssConnector
from bellwether.connectors.x import XConnector
from bellwether.connectors.news import NewsConnector
from bellwether.config import get_settings
from bellwether.models.source import Source


class UnknownConnectorType(Exception):
    pass


# The connector types that have a real implementation below and can actually ingest.
# Discovery uses this to reject LLM-proposed source types it could never fetch.
KNOWN_CONNECTOR_TYPES = frozenset({"rss", "x", "news"})


def build_connector(source: Source) -> SourceConnector:
    if source.connector_type == "rss":
        return RssConnector(source.config["feed_url"])
    if source.connector_type == "x":
        return XConnector(source.config["handle"], os.environ.get("X_API_KEY"))
    if source.connector_type == "news":
        return NewsConnector(source.config["query"], recency_days=get_settings().news_recency_days)
    raise UnknownConnectorType(source.connector_type)
