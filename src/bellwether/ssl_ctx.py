"""A default SSL context backed by certifi's CA bundle, so outbound HTTPS (Wikidata,
Tavily, X, feed fetches) verifies even in environments whose system Python lacks a CA
store — e.g. macOS python.org installs that never ran "Install Certificates.command".
Falls back to the system default store if certifi is unavailable."""
import ssl

try:
    import certifi
    _CAFILE = certifi.where()
except Exception:  # pragma: no cover - certifi is a dependency
    _CAFILE = None

SSL_CONTEXT = ssl.create_default_context(cafile=_CAFILE)
