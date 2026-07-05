from bellwether.programs import next_version, save_program, load_champion, set_champion, list_programs


def test_versioning_and_champion(db_session):
    assert next_version(db_session, "detect") == 1
    p1 = save_program(db_session, "detect", 1, {"a": 1}); db_session.flush()
    assert next_version(db_session, "detect") == 2
    p2 = save_program(db_session, "detect", 2, {"a": 2}); db_session.flush()
    assert load_champion(db_session, "detect") is None  # nothing promoted yet
    set_champion(db_session, p1.id); db_session.flush()
    assert load_champion(db_session, "detect") == ({"a": 1}, 1)
    set_champion(db_session, p2.id); db_session.flush()  # promotes p2, demotes p1
    assert load_champion(db_session, "detect") == ({"a": 2}, 2)
    db_session.refresh(p1)
    assert p1.is_champion is False
    assert [p.version for p in list_programs(db_session, "detect")] == [2, 1]
