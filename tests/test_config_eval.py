from bellwether.config import Settings


def test_eval_defaults():
    s = Settings(database_url="postgresql+psycopg://x/y", jwt_secret="s",
                 admin_username="a", admin_password="b")
    assert s.reflection_model == "anthropic/claude-sonnet-5"
    assert s.gepa_auto == "light"
    assert s.holdout_modulus == 5
