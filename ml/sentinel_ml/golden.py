"""Generate golden test vectors for the C gallery matcher (INTERFACES.md §1).

    python -m sentinel_ml.golden --out golden/

Files (all little-endian):
  gallery_v1.sgal        valid SGAL blob (8 people, mixed proto counts / thresholds)
  queries_f32.bin        N x 128 float32 query embeddings (L2-normalised, except the zero case)
  queries_i8.bin         N x 128 int8, quantize_embedding(queries_f32) — check your quantiser
  expected.csv           idx,case,match_id(-1 = unknown),best_id,best_score,second_score
  scores.csv             idx,person_id,score  (max int8 cosine per person)
  quant_vectors.csv      x_f32_hex,x_f32,expected_int8   (rounding / clamp edge cases)
  bad/*.sgal + bad/expected_errors.csv   blobs that must be rejected, with the error code
  MANIFEST.json          constants, tolerances, file hashes
Deterministic: re-running produces byte-identical files (tests/test_golden.py checks it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import zlib

import numpy as np

from . import gallery as G

SEED = 20261106
SCORE_TOL = 1e-5        # |score_c - score_ref| allowed
BOUNDARY_GAP = 1e-3     # golden decisions are at least this far from any threshold / margin edge


def _unit(rng, n):
    return G.l2_normalize(rng.normal(size=(n, 128)))


def _at_score(rng, g, pid, u, target):
    """Unit vector along the (u, random-orthogonal) arc whose int8 score for ``pid`` ≈ target."""
    v = rng.normal(size=128)
    v -= (v @ u) * u
    v /= np.linalg.norm(v)
    lo, hi = -1.0, 1.0
    person = next(p for p in g.people if p.id == pid)
    for _ in range(40):
        c = (lo + hi) / 2
        q = (c * u + np.sqrt(1 - c * c) * v).astype(np.float32)
        sc = max(G.cosine_int8(G.quantize_embedding(q), pr) for pr in person.protos)
        lo, hi = (c, hi) if sc < target else (lo, c)
    return q


def build_gallery(rng):
    g = G.Gallery()
    centres = _unit(rng, 8)
    spec = [  # id, name, n_protos, threshold
        (1, "Ashton", 5, 0.55), (2, "Alex", 3, 0.55), (3, "Mom", 20, 0.55), (4, "Dad", 1, 0.55),
        (7, "Strict-Sam", 4, 0.70), (9, "Lenient-Lee", 4, 0.40), (12, "Twin-A", 2, 0.55),
        (13, "Twin-B", 2, 0.55),
    ]
    for (pid, name, k, thr), c in zip(spec, centres):
        protos = G.l2_normalize(c[None] + 0.06 * rng.normal(size=(k, 128)))
        g.add(pid, name, protos, threshold=thr)
    # twins share an identical prototype → exact score tie for a query equal to it
    g.people[7].protos[0] = g.people[6].protos[0]
    return g, centres


def build_queries(rng, g, centres):
    q, cases = [], []

    def add(v, case):
        q.append(np.asarray(v, np.float32))
        cases.append(case)

    idx = {p.id: i for i, p in enumerate(g.people)}
    add(_at_score(rng, g, 1, centres[idx[1]], 0.80), "known_close")
    add(G.l2_normalize(g.people[idx[3]].protos[7].astype(np.float32)), "exact_prototype")
    add(_unit(rng, 1)[0], "stranger_random")
    add(-centres[idx[2]], "negated_person")
    add(np.zeros(128, np.float32), "zero_vector")
    add(G.l2_normalize(centres[idx[1]] + centres[idx[2]]), "margin_fail_mix")
    add(_at_score(rng, g, 7, centres[idx[7]], 0.62), "strict_threshold_reject")   # thr 0.70
    add(_at_score(rng, g, 9, centres[idx[9]], 0.47), "lenient_threshold_accept")  # thr 0.40
    add(_at_score(rng, g, 4, centres[idx[4]], 0.50), "default_threshold_reject")  # thr 0.55
    add(G.l2_normalize(g.people[idx[12]].protos[0].astype(np.float32)), "exact_tie_twins")
    add(_at_score(rng, g, 4, centres[idx[4]], 0.90), "single_proto_person")
    # sweep: interpolate between a person and a stranger / another person
    while len(q) < 64:
        a = int(rng.integers(0, len(g.people)))
        if rng.random() < 0.5:
            other = _unit(rng, 1)[0]
        else:
            other = centres[int(rng.integers(0, len(centres)))]
        t = rng.uniform(0.0, 1.0)
        add(G.l2_normalize(t * centres[a] + (1 - t) * other), "sweep")
    return q, cases


def _near_boundary(g, r) -> bool:
    if r.best_id is None:
        return False
    thr = next(p.threshold for p in g.people if p.id == r.best_id)
    d_thr = abs(r.best_score - thr)
    d_margin = abs((r.best_score - r.second_score) - G.MARGIN)
    exact_tie = r.best_score == r.second_score
    return (d_thr < BOUNDARY_GAP or d_margin < BOUNDARY_GAP) and not exact_tie


def quant_edge_cases():
    xs = [1.0, -1.0, 1.5, -2.0, 0.0, -0.0, 1e-9, -1e-9, 127.0, float("inf"), float("-inf")]
    # values whose float32 product with 127 is exactly k + 0.5 (round-half-away test)
    for k in [0, 1, 2, 10, 63, 100, 126]:
        x = np.float32((k + 0.5) / 127.0)
        if np.float32(x) * np.float32(127.0) == np.float32(k + 0.5):
            xs += [float(x), -float(x)]
    xs = np.array(xs, np.float32)
    return xs, G.quantize_embedding(xs)


def bad_blobs(good: bytes):
    def recrc(body):
        return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)

    b = good[:-4]
    return {
        "truncated_short.sgal": (good[:9], "TRUNCATED"),
        "bad_crc.sgal": (good[:-1] + bytes([good[-1] ^ 0x5A]), "BAD_CRC"),
        "bad_magic.sgal": (recrc(b"SGAX" + b[4:]), "BAD_MAGIC"),
        "bad_version.sgal": (recrc(b[:4] + struct.pack("<H", 2) + b[6:]), "BAD_VERSION"),
        "too_many_people.sgal": (recrc(b[:6] + struct.pack("<H", 17) + b[8:]), "TOO_MANY_PEOPLE"),
        "n_people_overruns.sgal": (recrc(b[:6] + struct.pack("<H", 9) + b[8:]), "TRUNCATED"),
        "zero_protos.sgal": (recrc(b[:10] + bytes([0]) + b[11:]), "BAD_PROTO_COUNT"),
        "too_many_protos.sgal": (recrc(b[:10] + bytes([21]) + b[11:]), "BAD_PROTO_COUNT"),
        "trailing_bytes.sgal": (recrc(b + b"\x00\x00"), "TRAILING_BYTES"),
        "duplicate_id.sgal": (recrc(b[:8 + 30 + 5 * 128] + struct.pack("<H", 1) + b[8 + 30 + 5 * 128 + 2:]),
                              "DUPLICATE_ID"),
    }


def generate(out: str) -> dict:
    os.makedirs(os.path.join(out, "bad"), exist_ok=True)
    rng = np.random.default_rng(SEED)
    g, centres = build_gallery(rng)
    blob = G.write_blob(g)
    queries, cases = build_queries(rng, g, centres)
    rows, score_rows, keep_q = [], [], []
    for v, case in zip(queries, cases):
        r = g.match(v)
        if case == "sweep" and _near_boundary(g, r):
            continue
        assert not _near_boundary(g, r), case
        i = len(keep_q)
        keep_q.append(v)
        rows.append(f"{i},{case},{-1 if r.person_id is None else r.person_id},"
                    f"{-1 if r.best_id is None else r.best_id},{r.best_score:.9f},{r.second_score:.9f}")
        for p in g.people:
            score_rows.append(f"{i},{p.id},{r.scores[p.id]:.9f}")
    qf = np.stack(keep_q).astype("<f4")
    qi = G.quantize_embedding(qf)
    files = {
        "gallery_v1.sgal": blob,
        "queries_f32.bin": qf.tobytes(),
        "queries_i8.bin": qi.astype(np.int8).tobytes(),
        "expected.csv": ("idx,case,match_id,best_id,best_score,second_score\n" + "\n".join(rows) + "\n").encode(),
        "scores.csv": ("idx,person_id,score\n" + "\n".join(score_rows) + "\n").encode(),
    }
    xs, qs = quant_edge_cases()
    files["quant_vectors.csv"] = ("x_f32_hex,x_f32,expected_int8\n" + "\n".join(
        f"{struct.unpack('<I', struct.pack('<f', x))[0]:08x},{x!r},{int(q)}" for x, q in zip(xs.tolist(), qs)
    ) + "\n").encode()
    bad = bad_blobs(blob)
    for name, (data, code) in bad.items():
        files[f"bad/{name}"] = data
    files["bad/expected_errors.csv"] = ("file,error\n" + "\n".join(f"{n},{c}" for n, (_, c) in bad.items())
                                        + "\n").encode()
    for name, data in files.items():
        with open(os.path.join(out, name), "wb") as f:
            f.write(data)
    manifest = {
        "contract": "docs/INTERFACES.md §1",
        "generator": "python -m sentinel_ml.golden (ml/sentinel_ml/golden.py), seed %d" % SEED,
        "n_queries": int(len(qf)),
        "n_people": len(g.people),
        "people": [{"id": p.id, "name": p.name, "n_protos": int(len(p.protos)), "threshold_q15": p.threshold_q15}
                   for p in g.people],
        "constants": {"emb_dim": 128, "quant_scale": "1/127", "margin": G.MARGIN,
                      "default_threshold": G.DEFAULT_THRESHOLD, "max_people": G.MAX_PEOPLE,
                      "max_protos": G.MAX_PROTOS},
        "rules": [
            "q = round_half_away_from_zero(clamp(x * 127.0f, -127, 127)) in float32 (C roundf); NaN -> 0",
            "cos(a,b) = dot_i32(a,b) / (sqrt(dot_i32(a,a)) * sqrt(dot_i32(b,b))); 0 if either norm is 0",
            "score(person) = max over its prototypes",
            "match iff best >= threshold_q15(best)/32768 AND best - second >= 0.05; second = -1.0 if only one person",
            "ties for best: first in gallery order (a tie always fails the margin)",
            "match_id = -1 means unknown; best_id = -1 only for an empty gallery",
        ],
        "tolerances": {"score_abs": SCORE_TOL, "decision_boundary_gap": BOUNDARY_GAP,
                       "note": "decisions must match exactly; scores within score_abs"},
        "blob_validation_order": ["TRUNCATED(len<12)", "BAD_CRC", "BAD_MAGIC", "BAD_VERSION", "TOO_MANY_PEOPLE",
                                  "TRUNCATED|BAD_PROTO_COUNT (per person)", "TRAILING_BYTES", "DUPLICATE_ID"],
        "sha256": {n: hashlib.sha256(d).hexdigest() for n, d in sorted(files.items())},
    }
    with open(os.path.join(out, "MANIFEST.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "golden"))
    a = ap.parse_args(argv)
    m = generate(a.out)
    print(f"wrote {len(m['sha256'])} files, {m['n_queries']} queries, {m['n_people']} people → {a.out}")


if __name__ == "__main__":
    main()
