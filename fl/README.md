# Sentinel federated learning of the false-alert head (WS4)

Federated training of the on-device false-alert head of INTERFACES.md §5 (logistic
regression on 64-d features, `w[64] + b`), on **simulated** non-IID homes: local-only
baseline, FedAvg, FedProx, client-side L2 clipping, server-side Gaussian noise with an RDP
accountant, pairwise-mask secure aggregation with dropout recovery, and optional per-home
fine-tuning.

> **Contract notes.** Nothing in INTERFACES.md was changed. Two points to know:
> (1) in **non-DP** FedAvg each client contributes 66 numbers to aggregation, `n·Δθ` and its
> label count `n`, so the server can form the sample-weighted mean from a secure sum; in DP
> mode a client sends exactly the 65 clipped floats of §5. (2) §5's "add Gaussian noise σ·C at
> aggregation" is implemented as noise on the **sum** of clipped updates, divided by the
> expected cohort size `qN` (standard DP-FedAvg, McMahan et al. 2018). See [Deviations](#deviations-and-interpretations).

## Results (SIMULATED, synthetic data, not from devices)

300 synthetic homes, 100 rounds, Poisson client sampling with q = 0.1 (~30 homes per round),
5 local epochs (SGD, lr 0.1, batch 32), mean ± std over 3 seeds. Metrics are per home on a
held-out, balanced test set from that home's own distribution (200 nuisance + 200 person
events), at the device's threshold of 0.5, then averaged over homes. ε is home-level
(user-level) DP for adding or removing one whole home, at δ = 1e-4 (below 1/N), from our RDP
accountant / Google `dp-accounting`'s PLD accountant. Reproduce with `make experiment`
(1–2.5 min on 4 CPUs). Per-home rows: `results/per_home.csv`. Raw output: `results/results.json`.

| Method | ε (δ=1e-4) RDP / PLD | False-alert rate % | Person recall % | AUC | FAR %, homes <20 labels | Recall %, homes <20 labels | FAR %, homes with quirk | FAR %, worst-decile home |
|---|---|---|---|---|---|---|---|---|
| Local only | ∞ (no DP) | 9.6 ± 0.9 | 57.8 ± 1.8 | 0.876 ± 0.014 | 12.7 ± 1.5 | 45.2 ± 2.8 | 13.3 ± 1.0 | 22.7 ± 2.5 |
| FedAvg | ∞ (no DP) | 11.3 ± 0.8 | 82.0 ± 0.9 | 0.951 ± 0.005 | 12.2 ± 1.3 | 82.0 ± 2.5 | 19.3 ± 0.6 | 27.9 ± 1.9 |
| FedProx(mu=0.5) | ∞ (no DP) | 11.5 ± 0.7 | 82.4 ± 1.2 | 0.951 ± 0.005 | 12.4 ± 1.2 | 82.4 ± 2.6 | 19.6 ± 0.4 | 27.9 ± 1.0 |
| FedAvg+FT5 | ∞ (no DP) | **7.5 ± 0.6** | 79.2 ± 1.1 | **0.953 ± 0.005** | **8.7 ± 0.4** | 79.9 ± 2.8 | **12.0 ± 0.4** | **17.7 ± 1.3** |
| FedAvg+clip1 | ∞ (no DP) | 11.2 ± 0.4 | 82.0 ± 1.3 | 0.952 ± 0.005 | 12.0 ± 0.8 | 81.9 ± 2.5 | 19.2 ± 0.8 | 27.9 ± 0.6 |
| FedAvg+DP(sigma=0.5) | 50.74 / 27.15 | 10.5 ± 0.8 | 79.8 ± 2.3 | 0.950 ± 0.005 | 11.3 ± 1.2 | 79.9 ± 3.9 | 18.2 ± 0.4 | 26.2 ± 1.9 |
| FedAvg+DP(sigma=1) | 6.82 / 5.97 | 10.9 ± 1.3 | 78.4 ± 3.8 | 0.946 ± 0.005 | 11.6 ± 1.8 | 78.6 ± 5.2 | 18.4 ± 1.1 | 26.8 ± 2.9 |
| FedAvg+DP(sigma=2) | 2.21 / 1.97 | 12.1 ± 2.1 | 75.2 ± 6.1 | 0.933 ± 0.008 | 12.9 ± 2.5 | 75.6 ± 7.2 | 19.4 ± 2.3 | 28.9 ± 4.7 |
| FedAvg+DP(sigma=1)+SecAgg+drop10% | 6.82 / 5.97 | 12.4 ± 2.2 | 80.0 ± 7.3 | 0.944 ± 0.006 | 13.4 ± 2.2 | 80.5 ± 7.6 | 20.3 ± 3.6 | 30.5 ± 3.8 |
| FedAvg+DP(sigma=1)+FT5 | 6.82 / 5.97 | 7.6 ± 0.5 | 77.0 ± 2.2 | 0.948 ± 0.005 | 8.8 ± 0.7 | 76.9 ± 3.7 | 12.1 ± 0.3 | 17.9 ± 2.0 |

`FT5` = after federated training, each home fine-tunes the global head for 5 local epochs
(PLAN §5.4 layer 2). `clip1` = clip each update to L2 ≤ 1 without noise. `drop10%` = 10% of
each round's sampled homes drop out after the ShareKeys round, which the secure-aggregation
protocol recovers from.

**What the simulation says** (the research question of PLAN §5.4, on synthetic data):
- **Federation helps the most where local learning fails.** The global FedAvg head lifts mean
  person recall from 58% to 82% and AUC from 0.876 to 0.951. On homes with fewer than 20 labels,
  recall goes from 45% to 82%. Many of those homes have seen only one class, so they cannot learn alone.
- **A single global head is not enough for false alerts.** At the fixed 0.5 threshold, global
  FedAvg has a *higher* mean FAR than local-only (11.3% vs 9.6%), concentrated in homes with a
  home-specific recurring nuisance ("quirk", e.g. a neighbour's flag): 19% vs 13%.
  **Federate, then fine-tune locally** gives the best of both: FAR 7.5% (−22% relative to
  local-only, −34% relative to FedAvg), recall 79%, the best AUC, and a worst-decile FAR of
  17.7% (local-only: 22.7%).
- **FedProx ≈ FedAvg here.** With a convex 65-parameter model and 5 local epochs, client drift is
  small, so the proximal term has nothing to fix. That is a measured null result, not a bug:
  `test_fedprox_limits_client_drift` shows the term does shrink drift on a long local run.
- **Cost of DP.** σ = 1 (ε ≈ 6.8 RDP / 6.0 PLD) costs ~0.005 AUC and ~3.5 points of recall. σ = 2
  (ε ≈ 2.2 / 2.0) costs ~0.018 AUC and ~7 points of recall, with visibly larger variance across
  seeds. Fine-tuning after DP training (post-processing on the home's own data, no further privacy
  cost to other homes) recovers the FAR: **7.6% at ε ≈ 6**. With only ~30 homes per round the
  noise is large relative to the signal; a real fleet of 3–5 devices plus simulated clients would
  not give a meaningful ε on its own (PLAN §8).
- **SecAgg** produces the same model as a plain sum (tested to 1e-5). The row above differs from
  plain DP σ=1 only because 10% of each cohort drops out (fewer contributions, same noise).

### The simulated homes (`sentinel_fl/homes.py`)

A shared world has a person prototype and four nuisance prototypes (car, tree, pet,
headlights; pets and headlights lie closer to people), plus shared low-rank within-class
variation. Each home has: its own nuisance mix (Dirichlet α = 0.4, so a street-facing door sees
mostly cars and headlights); a camera/lighting offset on all its features; with probability 0.5 a
home-specific "quirk" nuisance placed 70% of the way towards the person prototype; its own person
rate (5–50%); and a log-normal number of labels (median ~37, clipped to 4–400; ~28% of homes have
<20 labels, some only one class). These knobs (`WorldConfig`) were set so that a centrally trained
head is good but not perfect. The *absolute* numbers depend on them; the *comparisons* between
methods are the point.

## Design

| Module | What |
|---|---|
| `head.py` | Logistic head, loss/gradient (L2, FedProx proximal term, class weights), minibatch SGD, **65 × LE fp32 serialization** (`w` then `b`) |
| `homes.py` | Non-IID home simulator |
| `fed.py` | `client_update` (local training → Δθ, clipped on the client), `client_payload`, `ServerState.sample` (Poisson) / `aggregate` (FedAvg weighting, or DP sum + noise / qN, optional SecAgg), local-only baseline, fine-tuning, evaluation (FAR, recall, AUC per home) |
| `dp.py` | Clipping; RDP of the Poisson-subsampled Gaussian at integer orders (Mironov–Talwar–Zhang 2019), (ε, δ) conversion (Balle et al. 2020); reference values from `dp-accounting` (RDP and PLD) |
| `secagg.py` | Bonawitz et al. 2017 secure aggregation, semi-honest server (below) |
| `flower_app.py` | Flower `ServerApp`/`ClientApp` that run the same `client_update`/`aggregate` code |
| `experiment.py` | Experiment grid → `results/` |

**Flower.** `flwr` 1.43 with `flwr[simulation]` (Ray 2.55) installs and runs on Python 3.13, so
Flower is the orchestration layer for deployment: `flower_app.py` drives rounds through Flower's
`Grid` API (one SuperNode per home), and the ClientApp exchanges exactly the payload above.
`test_flower.py` runs a 6-home, 3-round DP-FedAvg simulation through Flower+Ray and checks that the
resulting model is **bit-identical** to the in-process loop. The experiment grid uses the in-process
loop because each Ray-backed simulation costs ~8 s to start plus per-message overhead, and the grid is
30 runs of 100 rounds. The algorithm code is the same. Two caveats: `run_simulation` is deprecated in favour
of `flwr run` (it still works in 1.43; moving to a `pyproject.toml` Flower app is a Phase 2 task), and
secure aggregation's four message rounds are not carried over Flower messages yet, so the adapter
**refuses** `secagg=True` rather than silently sending plaintext. (Flower's own SecAgg+ mod is the
likely route for Phase 2.)

**Secure aggregation** (`secagg.py`). Fixed-point encoding into Z_2^64 (24 fractional bits).
Round 0: each client advertises two X25519 public keys. Round 1: each client t-of-n
Shamir-shares (over GF(2^521−1)) its masking secret key and a fresh self-mask seed, encrypting each
share end-to-end to the recipient with AES-GCM under an X25519+HKDF key, so the server relays only
ciphertext. Round 2: `y_u = enc(x_u) + PRG(b_u) + Σ_{v>u} PRG(s_uv) − Σ_{v<u} PRG(s_uv)`, with PRG =
AES-256-CTR and pairwise seeds from X25519 agreement. Round 3: for each peer, a survivor reveals the
share of `b_v` if `v` survived, or of `s_sk_v` if `v` dropped, **never both** (the client enforces
this). The server rebuilds the survivors' self-masks and the dropped clients' pairwise masks and
recovers exactly the survivors' sum. The threshold `t ≥ 2` is enforced; the experiments use
`t = ⌈n/2⌉`. Cost: ~0.4 s per round for 50 clients in pure Python.
Out of scope (documented, not implemented): an actively malicious server (needs the paper's
consistency-check and signature rounds), and authenticating AdvertiseKeys with the device identity key
of PLAN §6. With central DP, the server adds the noise after unmasking, so the server is trusted to add
noise honestly. Distributed DP (each client adds a share of the noise) would remove that trust and is a
natural next step.

**DP accounting.** Per round: Poisson sampling with probability q, sensitivity C = 1 (each client's
Δθ is clipped on the device), noise N(0, σ²C²) on the sum, fixed denominator qN. Composition over T
rounds in RDP at orders 2–64 plus a few larger ones; conversion to (ε, δ). A round that aborts, or has
no clients, still releases a noised update, so the released sequence is exactly the analysed mechanism.
With SecAgg on, a cohort of fewer than 3 or with too many dropouts aborts; client inputs are never summed
in plaintext.

## Tests (77, all passing; `make test` ≈ 30 s, `make test-fast` skips the Flower/Ray test)

| File | Covers |
|---|---|
| `test_head.py` | 65-fp32 LE layout (`w[0]` at byte 0, `b` at byte 256, `1.0f` = `00 00 80 3f`), round-trip, matches an independent `struct` encoder, wrong length/NaN/Inf rejected; gradient vs finite differences; stable sigmoid; learns separable data |
| `test_homes.py` | determinism, shapes, balanced test sets, the non-IID properties (data-poor homes, one-class homes, skewed mixes, class-rate spread, quirk placement) |
| `test_dp.py` | closed form at q = 1 (α/2σ²), a hand-computed subsampled value, **agreement with Google `dp-accounting` RdpAccountant to 1e-6** on 5 regimes, RDP ≥ PLD, **known exact value**: for full participation, ε is bounded below by the exact analytic-Gaussian ε (Balle & Wang 2018) and within 1.3× of it; monotonicity in σ, T, q, δ |
| `test_secagg.py` | fixed-point and ring wraparound; Shamir threshold; exact sum with n = 2…12; dropout recovery (1–3 dropped, including before sharing); abort below threshold and t = 1 rejected; **server learns only the sum**: what the server can strip from a client's masked vector is uniform (χ² on 7,800 samples) and has the same distribution for two different input sets with equal sums (KS), the input cannot be recovered from it, an all-zero input looks random on the wire, the server never holds both secrets of one client, and a lying server's request for both is refused; tampered or misrouted ciphertexts are rejected |
| `test_fed.py` | weighted FedAvg mean, dropouts excluded, clipping binds on device, DP noise std = σC/(qN), DP requires clipping, FedProx limits drift, determinism, SecAgg == plain sum (non-DP and DP), SecAgg with dropouts, a SecAgg cohort below 3 is aborted (never summed in plaintext), DP rounds with no clients still release noise, reported ε, AUC, **federation beats local-only** (AUC, recall, bigger gain on data-poor homes), fine-tuning lowers FAR, trained head survives fp32 serialization with unchanged metrics |
| `test_flower.py` | Flower+Ray simulation is bit-identical to the in-process loop; adapter refuses plaintext SecAgg |
| `test_experiment.py` | quick grid end to end: every method row present, per-home CSV complete |

## Deviations and interpretations

1. **Non-DP aggregation payload is 66 numbers** (`n·Δθ`, `n`), needed for sample-weighted FedAvg under
   secure aggregation. Under SecAgg the server learns only the total `n` of the cohort. DP mode sends exactly
   the 65 clipped floats of §5 and weights clients uniformly (needed for bounded sensitivity).
2. **Noise placement.** §5 says "add Gaussian noise σ·C at aggregation": noise N(0, σ²C²) is added once to
   the sum of clipped updates, and the sum is divided by the expected cohort qN, not the realized count.
3. **Privacy unit.** ε is per home (all of a device's data), not per example.
4. **Head on device.** The exchanged and serialized parameters follow §5 exactly; training here is fp64 and
   the shipped head is fp32 (tested to give the same FAR).
