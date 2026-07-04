from bellwether.config import Settings


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    monkeypatch.setenv("JWT_SECRET", "s3cr3t")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    s = Settings()
    assert s.database_url == "postgresql+psycopg://u:p@h:5432/d"
    assert s.jwt_secret == "s3cr3t"
    assert s.jwt_algorithm == "HS256"        # default
    assert s.jwt_expire_minutes == 60         # default
    assert s.admin_username == "admin"
