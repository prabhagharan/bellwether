from bellwether.repositories.watchlist import (
    create_figure, list_figures, get_figure, delete_figure,
    add_source, list_sources, get_source, set_source_enabled, delete_source,
)
from bellwether.repositories.users import create_user

def test_figure_crud_is_owner_scoped(db_session):
    # Create users for the test
    u1 = create_user(db_session, "user1", "pw1")
    u2 = create_user(db_session, "user2", "pw2")
    db_session.flush()

    f = create_figure(db_session, "Jerome Powell", "central_bank", ["Powell"], owner_id=u1.id)
    db_session.flush()
    assert f.id is not None
    assert [x.id for x in list_figures(db_session, owner_id=u1.id)] == [f.id]
    assert list_figures(db_session, owner_id=u2.id) == []          # other owner sees nothing
    assert get_figure(db_session, f.id, owner_id=u2.id) is None     # cannot read across owners
    assert get_figure(db_session, f.id, owner_id=u1.id).name == "Jerome Powell"
    assert delete_figure(db_session, f.id, owner_id=u2.id) is False # cannot delete across owners
    assert delete_figure(db_session, f.id, owner_id=u1.id) is True

def test_source_crud_is_owner_scoped(db_session):
    # Create users for the test
    u1 = create_user(db_session, "user3", "pw3")
    u2 = create_user(db_session, "user4", "pw4")
    db_session.flush()

    f = create_figure(db_session, "ECB", "central_bank", [], owner_id=u1.id)
    db_session.flush()
    s = add_source(db_session, f.id, "rss", {"feed_url": "https://x/feed"}, "primary", "manual", owner_id=u1.id)
    db_session.flush()
    assert s is not None and s.figure_id == f.id and s.enabled is True
    # cannot add a source to a figure you don't own
    assert add_source(db_session, f.id, "rss", {"feed_url": "https://x"}, "primary", "manual", owner_id=u2.id) is None
    assert [x.id for x in list_sources(db_session, f.id, owner_id=u1.id)] == [s.id]
    assert get_source(db_session, s.id, owner_id=u2.id) is None
    updated = set_source_enabled(db_session, s.id, False, owner_id=u1.id)
    assert updated.enabled is False
    assert delete_source(db_session, s.id, owner_id=u2.id) is False
    assert delete_source(db_session, s.id, owner_id=u1.id) is True
