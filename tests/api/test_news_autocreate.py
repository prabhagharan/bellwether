from sqlalchemy import select
from bellwether.models.source import Source


def test_create_figure_auto_creates_enabled_news_source(client, auth_headers, db_session):
    r = client.post("/figures", json={"name": "Jerome Powell", "type": "individual"},
                    headers=auth_headers)
    assert r.status_code == 201
    fid = r.json()["id"]
    news = db_session.execute(
        select(Source).where(Source.figure_id == fid, Source.connector_type == "news")
    ).scalars().all()
    assert len(news) == 1
    n = news[0]
    assert n.config["query"] == "Jerome Powell"
    assert n.enabled is True
    assert n.provenance == "news"
    assert n.origin == "auto"
    assert n.poll_interval_seconds == 1800
