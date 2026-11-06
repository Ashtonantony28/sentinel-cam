"""Secure aggregation: correctness, dropout recovery, and that the server learns only the sum."""
import numpy as np
import pytest
from cryptography.exceptions import InvalidTag
from scipy.stats import chisquare, ks_2samp

from sentinel_fl import secagg
from sentinel_fl.secagg import (Client, SecAggError, ServerView, decode, encode, prg, run_protocol, shamir_combine,
                                shamir_split)

RNG = np.random.default_rng(0)


def inputs(n, dim=65, scale=1.0, rng=RNG):
    return {i: rng.normal(scale=scale, size=dim) for i in range(n)}


def test_fixed_point_roundtrip_and_wraparound():
    x = np.array([0.0, 1.5, -2.25, 1e-7, -1e6])
    assert np.allclose(decode(encode(x)), x, atol=2 ** -24)
    a, b = encode(np.array([-3.0])), encode(np.array([5.0]))
    assert decode(a + b)[0] == 2.0  # negative values wrap correctly in Z_2^64
    with pytest.raises(SecAggError):
        encode(np.array([1e13]))


def test_shamir_threshold():
    s = 123456789 << 200
    sh = shamir_split(s, list(range(7)), t=4)
    assert shamir_combine({i: sh[i] for i in [0, 3, 5, 6]}, 4) == s
    assert shamir_combine({i: sh[i] for i in [1, 2, 4, 6]}, 4) == s
    with pytest.raises(SecAggError):
        shamir_combine({i: sh[i] for i in [0, 1, 2]}, 4)
    # t-1 shares are consistent with any secret: combining them as if t=3 gives garbage.
    assert shamir_combine({i: sh[i] for i in [0, 1, 2]}, 3) != s


@pytest.mark.parametrize("n,t", [(2, 2), (3, 2), (5, 3), (12, 7)])
def test_sum_correct_no_dropout(n, t):
    x = inputs(n)
    total, view, surv = run_protocol(x, t=t)
    assert surv == set(range(n))
    assert np.allclose(total, sum(x.values()), atol=n * 2 ** -23)


@pytest.mark.parametrize("n,t,drop", [(5, 3, {1}), (8, 4, {0, 7}), (10, 6, {2, 3, 9, 5}), (6, 3, {0, 1, 2})])
def test_dropout_recovery(n, t, drop):
    x = inputs(n)
    total, view, surv = run_protocol(x, t=t, drop_after_share=frozenset(drop))
    assert surv == set(range(n)) - drop
    assert np.allclose(total, sum(x[i] for i in surv), atol=n * 2 ** -23)
    assert set(view.s_keys) == drop          # server learned dropped clients' mask keys ...
    assert set(view.b_seeds) == surv         # ... and survivors' self-mask seeds ...
    assert not (set(view.s_keys) & set(view.b_seeds))  # ... never both for one client


def test_dropout_before_sharing():
    x = inputs(6)
    total, _, surv = run_protocol(x, t=3, drop_before_share=frozenset({4}))
    assert surv == {0, 1, 2, 3, 5}
    assert np.allclose(total, sum(x[i] for i in surv), atol=1e-5)


def test_abort_below_threshold():
    with pytest.raises(SecAggError):
        run_protocol(inputs(5), t=4, drop_after_share=frozenset({0, 1}))
    with pytest.raises(SecAggError):
        run_protocol(inputs(3), t=1)


def _server_partial_unmask(view: ServerView, u: int, dim: int, round_id: int = 0) -> np.ndarray:
    """Strip everything the server legitimately can from client u's masked vector."""
    return view.masked[u] - prg(view.b_seeds[u], dim, round_id)


def test_server_view_of_individual_input_is_uniform_and_independent():
    """With >= 2 survivors, each partially-unmasked vector z_u = x_u + (pairwise masks) is
    uniform on Z_2^64 whatever x_u is. Two input sets with the same sum produce views
    whose distributions cannot be told apart, so the server learns nothing beyond the sum."""
    dim, runs = 65, 120
    rng = np.random.default_rng(1)
    xa = {0: np.zeros(dim), 1: np.full(dim, 5.0), 2: np.full(dim, -2.0)}
    xb = {0: np.full(dim, 3.0), 1: np.full(dim, -1.0), 2: np.full(dim, 1.0)}   # same sum
    assert np.allclose(sum(xa.values()), sum(xb.values()))
    tops = {"a": [], "b": []}
    for _ in range(runs):
        for k, x in (("a", xa), ("b", xb)):
            total, view, _ = run_protocol(x, t=2)
            assert np.allclose(total, sum(x.values()), atol=1e-5)
            z = _server_partial_unmask(view, 0, dim)
            assert not np.allclose(decode(z), x[0], atol=1.0)    # input not recoverable
            tops[k].append((z >> np.uint64(56)).astype(np.int64))  # top byte of each coordinate
    for k in tops:
        counts = np.bincount(np.concatenate(tops[k]), minlength=256)
        assert chisquare(counts).pvalue > 1e-4, f"server view not uniform for inputs {k}"
    assert ks_2samp(np.concatenate(tops["a"]), np.concatenate(tops["b"])).pvalue > 1e-4


def test_masked_vector_alone_reveals_nothing():
    x = {0: np.zeros(65), 1: np.zeros(65), 2: np.zeros(65)}
    _, view, _ = run_protocol(x, t=2)
    for u in view.masked:  # all-zero inputs still look random on the wire
        assert np.count_nonzero(view.masked[u]) > 60


def test_client_refuses_to_reveal_both_secrets_of_a_peer():
    """A server that lies (claims u dropped after receiving y_u) would get s_sk_u and then
    b_u and could unmask y_u. Honest clients refuse the second request."""
    n, dim = 4, 8
    clients = {i: Client(i, dim, 3) for i in range(n)}
    pks = {i: c.advertise() for i, c in clients.items()}
    msgs = [m for i in range(n) for m in clients[i].share_keys(pks)]
    for i in range(n):
        clients[i].masked_input(np.ones(dim), [m for m in msgs if m.dst == i])
    clients[1].unmask({0, 1, 2}, {3})               # first asks: 3 dropped -> share of s_sk_3
    with pytest.raises(SecAggError):
        clients[1].unmask({0, 1, 2, 3}, set())      # then asks for b_3 too
    with pytest.raises(SecAggError):
        clients[2].unmask({0, 2, 3}, {3})           # overlapping sets
    with pytest.raises(SecAggError):
        clients[0].unmask({0, 1, 2, 9}, set())      # unknown client


def test_shares_are_end_to_end_encrypted():
    n, dim = 3, 4
    clients = {i: Client(i, dim, 2) for i in range(n)}
    pks = {i: c.advertise() for i, c in clients.items()}
    msgs = [m for i in range(n) for m in clients[i].share_keys(pks)]
    m = next(m for m in msgs if m.dst == 1)
    tampered = secagg.ShareMsg(m.src, m.dst, m.nonce, m.ct[:-1] + bytes([m.ct[-1] ^ 1]))
    with pytest.raises(InvalidTag):
        clients[1].masked_input(np.zeros(dim), [tampered])
    # A share re-addressed to another client is rejected.
    with pytest.raises(SecAggError):
        clients[2].masked_input(np.zeros(dim), [m])


def test_round_id_separates_masks():
    assert not np.array_equal(prg(b"k" * 32, 4, 1), prg(b"k" * 32, 4, 2))
