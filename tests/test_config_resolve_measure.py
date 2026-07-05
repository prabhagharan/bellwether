from bellwether.config import Settings


def test_resolve_measure_defaults():
    s = Settings(database_url="postgresql+psycopg://x/y", jwt_secret="s",
                 admin_username="a", admin_password="b")
    assert s.resolve_model == "anthropic/claude-haiku-4-5"
    assert s.resolve_max_attempts == 3
    assert s.resolve_confidence_threshold == 0.5
    assert s.measure_windows == "5m,1h,1d"
    assert s.measure_baseline_bars == 20
