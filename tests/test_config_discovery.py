from bellwether.config import Settings


def test_discovery_defaults():
    s = Settings(database_url="postgresql+psycopg://x/y", jwt_secret="s",
                 admin_username="a", admin_password="b")
    assert s.discovery_model == "anthropic/claude-sonnet-5"
    assert s.discovery_confidence_threshold == 0.7
