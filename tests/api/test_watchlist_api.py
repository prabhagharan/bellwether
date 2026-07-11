def test_requires_auth(client):
    assert client.get("/figures").status_code == 401


def test_figure_and_source_lifecycle(client, auth_headers):
    # create figure
    r = client.post("/figures", json={"name": "Powell", "type": "central_bank", "aliases": ["Jerome Powell"]},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    fig = r.json()
    assert fig["name"] == "Powell" and fig["aliases"] == ["Jerome Powell"]
    fid = fig["id"]
    # list figures
    assert [f["id"] for f in client.get("/figures", headers=auth_headers).json()] == [fid]
    # figure creation auto-creates an enabled "news" source
    auto_sources = client.get(f"/figures/{fid}/sources", headers=auth_headers).json()
    assert [s["connector_type"] for s in auto_sources] == ["news"]
    news_sid = auto_sources[0]["id"]
    # add rss source
    r = client.post(f"/figures/{fid}/sources",
                    json={"connector_type": "rss", "config": {"feed_url": "https://x/feed"}},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["connector_type"] == "rss" and src["enabled"] is True and src["origin"] == "manual"
    sid = src["id"]
    # rss without feed_url -> 422
    assert client.post(f"/figures/{fid}/sources", json={"connector_type": "rss", "config": {}},
                       headers=auth_headers).status_code == 422
    # disable the source
    r = client.patch(f"/sources/{sid}", json={"enabled": False}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["enabled"] is False
    # list sources (auto-created news source + manually-added rss source)
    assert [s["id"] for s in client.get(f"/figures/{fid}/sources", headers=auth_headers).json()] == [news_sid, sid]
    # delete source + figure
    assert client.delete(f"/sources/{sid}", headers=auth_headers).status_code == 204
    assert client.delete(f"/figures/{fid}", headers=auth_headers).status_code == 204
    assert client.delete(f"/figures/{fid}", headers=auth_headers).status_code == 404  # already gone
