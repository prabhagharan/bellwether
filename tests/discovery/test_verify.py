from bellwether.discovery.verify import score_binding


def test_single_signal_below_bar():
    conf, verified = score_binding({"wikidata": True}, 0.7)
    assert conf == 0.6 and verified is False


def test_two_signals_clear_bar():
    conf, verified = score_binding({"wikidata": True, "domain_match": True}, 0.7)
    assert abs(conf - 0.9) < 1e-9 and verified is True


def test_llm_proposal_weak_stays_pending():
    # no wikidata: domain_match + reachable = 0.5 -> pending
    conf, verified = score_binding({"domain_match": True, "reachable": True}, 0.7)
    assert abs(conf - 0.5) < 1e-9 and verified is False


def test_llm_proposal_strong_x_clears():
    conf, verified = score_binding({"domain_match": True, "x_verified": True, "reachable": True}, 0.7)
    assert abs(conf - 0.7) < 1e-9 and verified is True


def test_falsy_and_unknown_signals_ignored():
    conf, verified = score_binding({"wikidata": True, "x_verified": None, "bogus": True}, 0.7)
    assert conf == 0.6 and verified is False
