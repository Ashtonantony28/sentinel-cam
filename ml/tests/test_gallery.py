import struct
import zlib

import numpy as np
import pytest

from sentinel_ml import gallery as G


def unit(rng, n=1):
    return G.l2_normalize(rng.normal(size=(n, 128)))


def test_quantize_formula_and_rounding():
    x = np.array([1.0, -1.0, 2.0, -3.0, 0.0, -0.0, 0.5 / 127, -0.5 / 127, 1.5 / 127, np.nan], np.float32)
    q = G.quantize_embedding(x)
    assert q.dtype == np.int8
    # half-away-from-zero (C roundf); 0.5/127*127 in float32 is exactly 0.5
    assert np.float32(0.5 / 127) * np.float32(127) == np.float32(0.5)
    assert list(q) == [127, -127, 127, -127, 0, 0, 1, -1, 2, 0]
    assert q.min() >= -127  # -128 never produced


def test_cosine_int8_matches_float_closely():
    rng = np.random.default_rng(0)
    a, b = unit(rng, 2)
    c_f = float(a @ b)
    c_q = G.cosine_int8(G.quantize_embedding(a), G.quantize_embedding(b))
    assert abs(c_f - c_q) < 0.02
    assert G.cosine_int8(G.quantize_embedding(a), G.quantize_embedding(a)) == pytest.approx(1.0)
    assert G.cosine_int8(np.zeros(128, np.int8), G.quantize_embedding(a)) == 0.0


def test_threshold_q15():
    assert G.threshold_to_q15(0.55) == 18022
    assert G.threshold_to_q15(1.0) == 32767
    assert G.threshold_to_q15(-1.0) == -32768
    assert G.q15_to_threshold(18022) == pytest.approx(0.55, abs=1 / 32768)


def make_gallery(rng):
    g = G.Gallery()
    centres = unit(rng, 3)
    for i, c in enumerate(centres):
        g.add(10 + i, f"p{i}", G.l2_normalize(c + 0.05 * rng.normal(size=(4, 128))))
    return g, centres


def test_match_rules():
    rng = np.random.default_rng(1)
    g, c = make_gallery(rng)
    r = g.match(c[1])
    assert r.person_id == 11 and r.best_score > 0.75
    # unknown: random direction
    assert g.match(unit(rng)[0]).person_id is None
    # margin failure: equal mix of two people
    r = g.match(G.l2_normalize(c[0] + c[2]))
    assert r.person_id is None and r.best_id in (10, 12) and r.best_score - r.second_score < G.MARGIN
    # per-person threshold override
    g.people[1].threshold_q15 = G.threshold_to_q15(0.999)
    assert g.match(c[1]).person_id is None
    # empty gallery and single person (second = -1)
    assert G.Gallery().match(c[0]).person_id is None
    g1 = G.Gallery()
    g1.add(1, "solo", c[0])
    r = g1.match(c[0])
    assert r.person_id == 1 and r.second_score == -1.0


def test_blob_roundtrip_and_layout():
    rng = np.random.default_rng(2)
    g, _ = make_gallery(rng)
    b = G.write_blob(g)
    assert b[:4] == b"SGAL" and struct.unpack_from("<HH", b, 4) == (1, 3)
    assert len(b) == 8 + 3 * (30 + 4 * 128) + 4
    assert struct.unpack("<I", b[-4:])[0] == zlib.crc32(b[:-4])
    g2 = G.read_blob(b)
    assert [p.id for p in g2.people] == [10, 11, 12]
    assert [p.name for p in g2.people] == ["p0", "p1", "p2"]
    for p, q in zip(g.people, g2.people):
        np.testing.assert_array_equal(p.protos, q.protos)
        assert p.threshold_q15 == q.threshold_q15
    assert G.write_blob(g2) == b


def _recrc(body: bytes) -> bytes:
    return body + struct.pack("<I", zlib.crc32(body))


@pytest.mark.parametrize("mutate,code", [
    (lambda b: b[:10], "TRUNCATED"),
    (lambda b: b[:-1] + bytes([b[-1] ^ 1]), "BAD_CRC"),
    (lambda b: _recrc(b"XGAL" + b[4:-4]), "BAD_MAGIC"),
    (lambda b: _recrc(b[:4] + struct.pack("<H", 2) + b[6:-4]), "BAD_VERSION"),
    (lambda b: _recrc(b[:6] + struct.pack("<H", 17) + b[8:-4]), "TOO_MANY_PEOPLE"),
    (lambda b: _recrc(b[:6] + struct.pack("<H", 4) + b[8:-4]), "TRUNCATED"),
    (lambda b: _recrc(b[:10] + bytes([21]) + b[11:-4]), "BAD_PROTO_COUNT"),
    (lambda b: _recrc(b[:10] + bytes([0]) + b[11:-4]), "BAD_PROTO_COUNT"),
    (lambda b: _recrc(b[:-4] + b"\0"), "TRAILING_BYTES"),
])
def test_blob_rejects(mutate, code):
    rng = np.random.default_rng(3)
    g, _ = make_gallery(rng)
    with pytest.raises(G.GalleryError) as e:
        G.read_blob(mutate(G.write_blob(g)))
    assert e.value.code == code


def test_writer_limits():
    rng = np.random.default_rng(4)
    g = G.Gallery()
    for i in range(16):
        g.add(i, f"n{i}", unit(rng, 20))
    with pytest.raises(G.GalleryError):
        g.add(99, "x", unit(rng))
    with pytest.raises(G.GalleryError):
        G.Gallery().add(1, "x", unit(rng, 21))
    with pytest.raises(G.GalleryError):
        G.Gallery().add(1, "a" * 24, unit(rng))
    g2 = G.Gallery()
    g2.add(1, "a", unit(rng))
    with pytest.raises(G.GalleryError) as e:
        g2.add(1, "b", unit(rng))
    assert e.value.code == "DUPLICATE_ID"
