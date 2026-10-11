from app.services.connectors.base import ConnectorError, FeedbackSourceConnector
from app.services.connectors.demo import DemoAPIConnector, DemoCSVConnector

# Registered sync sources for POST /api/v1/ingestion/sync/{source}. A real
# deployment would register additional connectors here (each implementing
# FeedbackSourceConnector) rather than replacing this dict's shape.
CONNECTOR_REGISTRY: dict[str, FeedbackSourceConnector] = {
    "demo": DemoAPIConnector(),
    "csv_drop": DemoCSVConnector(),
}

__all__ = ["ConnectorError", "FeedbackSourceConnector", "CONNECTOR_REGISTRY"]
