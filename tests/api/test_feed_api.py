def test_feed_requires_auth(client):
    assert client.get("/signals").status_code == 401
    assert client.get("/impacts").status_code == 401
    assert client.get("/leaderboard").status_code == 401
