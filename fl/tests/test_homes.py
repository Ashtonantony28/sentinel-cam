import numpy as np

from sentinel_fl.homes import NUISANCE, make_world


def test_deterministic_by_seed():
    a, b, c = make_world(10, 3), make_world(10, 3), make_world(10, 4)
    assert all(np.array_equal(x.X, y.X) for x, y in zip(a.homes, b.homes))
    assert not np.array_equal(a.homes[0].X, c.homes[0].X)


def test_shapes_and_labels():
    w = make_world(20, 0)
    for h in w.homes:
        assert h.X.shape == (h.n, 64) and h.X_test.shape == (400, 64)
        assert set(np.unique(h.y)) <= {0.0, 1.0}
        assert h.y_test.mean() == 0.5  # balanced test set: FAR and recall each get 200 events
        assert set(h.test_kind) <= set(NUISANCE) | {"person", "quirk"}


def test_non_iid_properties():
    w = make_world(200, 0)
    n = np.array([h.n for h in w.homes])
    rates = np.array([h.person_rate for h in w.homes])
    mixes = np.array([h.mix for h in w.homes])
    assert (n < 20).mean() > 0.15, "a real share of homes must be data-poor"
    assert n.max() >= 200
    assert rates.min() < 0.1 and rates.max() > 0.4
    assert mixes.max(axis=1).mean() > 0.6, "nuisance mix should be skewed per home"
    assert 0.3 < np.mean([h.has_quirk for h in w.homes]) < 0.7
    # Some tiny homes have seen only one class: local-only learning cannot work there.
    assert any(len(np.unique(h.y)) < 2 for h in w.homes)


def test_quirk_is_home_specific_and_person_like():
    w = make_world(50, 1)
    q = [h for h in w.homes if h.has_quirk][0]
    Xq = q.X_test[q.test_kind == "quirk"]
    Xp = q.X_test[q.test_kind == "person"]
    Xc = q.X_test[np.isin(q.test_kind, ["car", "tree"])]
    axis = w.mu_person / np.linalg.norm(w.mu_person)
    # Along the person axis the quirk sits between ordinary nuisances and people.
    if len(Xc):
        assert (Xc @ axis).mean() < (Xq @ axis).mean() < (Xp @ axis).mean()
