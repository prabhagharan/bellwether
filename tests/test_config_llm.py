from bellwether.config import Settings


def test_llm_and_worker_defaults():
    s = Settings(
        database_url="postgresql+psycopg://x/y",
        jwt_secret="s",
        admin_username="a",
        admin_password="b",
    )
    assert s.detect_model == "anthropic/claude-haiku-4-5"
    assert s.extract_model == "anthropic/claude-sonnet-5"
    assert s.relevance_threshold == 0.5
    assert s.worker_poll_interval_seconds == 5.0
    assert s.worker_stale_reclaim_seconds == 300.0
