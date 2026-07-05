import bellwether.api.optimize_api as optimize_api
from bellwether.eval.optimize import OptimizeResult
from bellwether.programs import save_program


def test_programs_list_and_promote(client, auth_headers, db_session):
    save_program(db_session, "detect", 1, {"a": 1}, holdout_score=0.7, is_champion=False)
    p2 = save_program(db_session, "detect", 2, {"a": 2}, holdout_score=0.9, is_champion=False)
    db_session.flush()
    listed = client.get("/programs?module=detect", headers=auth_headers).json()
    assert [p["version"] for p in listed] == [2, 1]
    r = client.post(f"/programs/{p2.id}/promote", headers=auth_headers)
    assert r.status_code == 200 and r.json()["is_champion"] is True
    db_session.refresh(p2)
    assert p2.is_champion is True


def test_optimize_endpoint_uses_run_optimize(client, auth_headers, monkeypatch):
    # patch run_optimize so the endpoint runs no GEPA/LLM
    def fake_run_optimize(session, module):
        return OptimizeResult(module, 3, 0.95, 0.80, True)
    monkeypatch.setattr(optimize_api, "run_optimize", fake_run_optimize)
    r = client.post("/optimize/detect", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 3 and body["promoted"] is True and abs(body["challenger_holdout"] - 0.95) < 1e-9


def test_optimize_rejects_unknown_module(client, auth_headers):
    assert client.post("/optimize/bogus", headers=auth_headers).status_code == 422
