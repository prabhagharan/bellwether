from bellwether.connectors.base import SourceConnector
from bellwether.connectors.rss import RssConnector
from bellwether.models.source import Source


class UnknownConnectorType(Exception):
    pass


def build_connector(source: Source) -> SourceConnector:
    if source.connector_type == "rss":
        return RssConnector(source.config["feed_url"])
    raise UnknownConnectorType(source.connector_type)
