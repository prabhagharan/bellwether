from bellwether.config import Settings


def test_alert_defaults():
    s = Settings(database_url="postgresql+psycopg://x/y", jwt_secret="s",
                 admin_username="a", admin_password="b")
    assert s.cors_origins == []
    assert s.sse_poll_interval_seconds == 2.0
    assert s.alert_webhook_timeout_seconds == 10.0
