# Sentinel ML (WS1)

Models, training, int8 quantisation, NPU-fit checking, evaluation and the face-gallery
reference implementation for Sentinel (STM32N6 Neural-ART NPU + ESP32-C6). CPU only.

```
make -C ml deps      # pip install -r requirements.txt (CPU torch: see requirements.txt)
make -C ml test      # pytest: unit + quick end-to-end pipeline (~30 s on 4 idle CPUs)
make -C ml full      # reproduce the tables below (~1-1.5 h on 4 CPUs, synthetic data)
make -C ml golden    # regenerate ml/golden/ (byte-identical; firmware/core consumes it)
```

**Every accuracy number in this README is on procedurally generated SYNTHETIC data.** It shows
the train → quantise → export → evaluate loop works and how big the int8 gap is. It says
nothing about real-world accuracy (PLAN.md G4/G5), which needs the real-dataset hooks below
and the doorstep domain set.

## Contract notes (INTERFACES.md)

No contract was changed. Points the contract leaves open are pinned by `sentinel_ml/gallery.py`
and the golden vectors:

- §1 rounding: round-half-away-from-zero on the float32 product `x * 127.0f` (C `roundf`), NaN → 0.
  This is now also written into INTERFACES.md §1. Only `gallery.quantize_embedding` produces device
  embeddings; a test checks no other module rounds embeddings. (The ONNX QDQ weight folding in
  `quant.py` uses ONNX's half-to-even, which is correct for model weights.)
- §1 cosine with an all-zero int8 vector = 0. With fewer than 2 people, second-best = −1.0.
  Best-person ties go to gallery order and always fail the margin.
- §1 `threshold_q15 = round_half_away(t * 32768)`, effective threshold `q15 / 32768`
  (0.55 → 18022 → 0.549988).
- §1 blob: `n_protos` must be 1..20 (0 is rejected), ids unique, name UTF-8 NUL-padded and
  ≤ 23 bytes so it is always NUL-terminated. Validation order: TRUNCATED (len < 12) → BAD_CRC →
  BAD_MAGIC → BAD_VERSION → TOO_MANY_PEOPLE → per person TRUNCATED / BAD_PROTO_COUNT →
  TRAILING_BYTES → DUPLICATE_ID.
- §4 `model_op_set_hash` = `sha256("sentinel-opset-v1\n" + sorted unique lower-case op names, each + "\n")`,
  emitted by `npu_fit` and written as `<model>.opset.txt` next to each export. `Constant` and
  `Identity` are left out of the op set (graph plumbing, not runtime requirements). A test checks
  the value against `tools/sentinel_sign.py opset`.
- §5 the person detector's `feat` output is the 64-d input of the false-alert head: global average
  pool of the stride-16 neck → 1×1 conv projection (linear), fp32 after dequantisation. During
  training it is shaped by an auxiliary "image contains a person" logit (not exported).
- §6 energy: `npu.npu_pj_per_mac` (3.6 pJ/MAC, low 0.67, high 16) and
  `npu.npu_overhead_per_inference` (0.153 mJ) are read from `hardware/power_model/constants.yaml`;
  if the file or key is missing the default is 1.0 pJ/MAC and no overhead.

## Layout

| Path | What |
|---|---|
| `sentinel_ml/models/` | `person_det.py` (256², anchor-free, 3 levels + 64-d feature), `face_det.py` (160², box + 5 landmarks), `embedder.py` (MobileFaceNet, 112² → 128-d), shared `detector.py` / `blocks.py` |
| `sentinel_ml/data/` | synthetic renderers (`synthetic_person.py`, `synthetic_face.py`) and real-dataset hooks (`real.py`) |
| `sentinel_ml/detection.py` | target assignment, focal + GIoU + landmark loss, numpy decode + NMS (reference for the M55 post-processing) |
| `sentinel_ml/train.py`, `train_detector.py`, `train_embedder.py` | training loops and CLIs |
| `sentinel_ml/quant.py`, `export.py` | BN folding, torch.ao QAT / PTQ, int8 QDQ ONNX export, onnxruntime PTQ |
| `sentinel_ml/npu_fit.py` | NPU-fit checker (CLI) |
| `sentinel_ml/eval/` | mAP@0.5, TAR@FAR, energy model, latency |
| `sentinel_ml/gallery.py`, `golden.py`, `golden/` | INTERFACES §1 reference + golden vectors for the C matcher |
| `sentinel_ml/pipeline.py` | end-to-end runner that produces the tables below |

## Models

All three use only Conv (regular, depthwise, pointwise), ReLU, Add, nearest Resize and global
average pooling. There is no SiLU, Swish, hard-swish or PReLU: ReLU fuses into the conv epilogue for
free on an int8 NPU, while the others need lookup tables or fall back to the CPU. Sigmoid, exp, NMS and
the embedding L2-norm are kept out of the graph. They run on the M55, see `detection.decode`.

- **Person detector**: MobileNetV2-style backbone with ReLU and expansion 3, then an FPN + PAN-lite
  neck with 48 channels. It has one head per level at strides 8, 16 and 32. Each head outputs
  `[obj, log(l/s), log(t/s), log(r/s), log(b/s)]` (FCOS/YOLOX-style anchor-free), with center
  sampling and a focal + GIoU loss. It has 218k parameters (the budget is < 1M).
- **Face detector**: the same design, smaller, with 160×160 input. It adds 5 landmarks per cell
  (eyes, nose, mouth corners) for alignment to the 112×112 ArcFace template.
- **Embedder**: MobileFaceNet (paper Table 1) with ReLU instead of PReLU, a global depthwise 7×7
  conv, and a linear 128-d output. Training uses a CosFace loss (s=30, m=0.35), and the CosFace
  head is saved in the checkpoint so QAT continues the same loss.

## Quantisation (int8 QDQ ONNX)

The exported model uses int8 per-channel symmetric weights, int8 per-tensor activations and int32
biases (scale = s_in·s_w). The **input has fixed qparams of scale 1/255 and zero-point −128**, so the
firmware feeds `uint8_pixel − 128` with no float pre-processing. BN is folded before QAT/PTQ.
Conv+ReLU pairs are fused, residual adds and the FPN merges are observed through `FloatFunctional`,
and a `QuantStub` after each Resize and pool keeps every tensor int8. The export uses the TorchScript
ONNX exporter (fake-quant becomes Q/DQ). `finalize_qdq` then folds the weight Q nodes into int8
initialisers, quantises the biases to int32 and turns 1-element qparams into scalars.

There are three paths, all compared in the tables:

- torch.ao PTQ: MinMax observers on 64 calibration images.
- torch.ao QAT: observers warmed on calibration data, then 200–300 fine-tuning steps at lr 3e-4.
- onnxruntime `quantize_static` (QDQ, per-channel): a cross-check.

## NPU fit

`python -m sentinel_ml.npu_fit model.onnx [--require-int8] [--json out.json] [--opset-out ops.txt]`
reports:

- the op histogram
- ops outside the allowlist, plus attribute rules (Resize must be nearest, Clip min must be 0, no dilation)
- parameters and weight bytes
- MACs (Conv/Gemm)
- a peak activation-memory estimate (graph collapsed over Q/DQ, Conv+ReLU treated as fused, int8 = 1 byte)
- the op-set hash

It exits 1 on any violation. With `--require-int8` it also exits 1 if any compute op reads a
non-quantised tensor.

**The allowlist is an approximation** of Neural-ART hardware support, written from public ST
documentation and deliberately conservative: Conv, Relu, Clip, Add, Concat, Resize (nearest), Max/Avg/
GlobalAveragePool, Q/DQ, plus free layout ops. It must be replaced by the ST Edge AI Core
`analyze` report once hardware is available (PLAN.md §5.2).

## Real datasets (hooks, not run here)

Nothing is downloaded. Pass local paths:

```
python -m sentinel_ml.train_detector --task person --coco-root /data/coco --crowdhuman-root /data/crowdhuman
python -m sentinel_ml.train_detector --task face --wider-root /data/widerface --wider-landmarks /data/retinaface/train/label.txt
python -m sentinel_ml.train_embedder --faceid-root /data/casia_aligned_112 --faceid-val-root /data/heldout_ids
python -m sentinel_ml.export --ckpt artifacts/person/person_float.pt --mode qat --coco-root /data/coco
```

The expected layouts are in `sentinel_ml/data/real.py`, and each loader is tested on tiny fake files
in that format. Check dataset licences: several face-ID sets are research-only.

## Golden vectors for the firmware (`ml/golden/`)

| File | Contents |
|---|---|
| `gallery_v1.sgal` | 8 people with 1–20 prototypes; thresholds of 0.55, 0.70 and 0.40; two "twins" sharing a prototype |
| `queries_f32.bin`, `queries_i8.bin` | 64 queries |
| `expected.csv` | idx, case, match_id (−1 = unknown), best_id, best_score, second_score |
| `scores.csv` | per-person scores |
| `quant_vectors.csv` | rounding and clamp edge cases, including exact x.5 ties, ±inf and −0 |
| `bad/*.sgal` + `bad/expected_errors.csv` | 10 blobs the reader must reject, each with its error code |
| `MANIFEST.json` | rules, tolerances and sha256 of every file |

Decisions must match exactly. Scores must agree to 1e-5. Every golden decision is ≥ 1e-3 away
from its threshold and margin edges, except the exact-tie case. `make test` checks that the files
regenerate byte-identically.

## Results (synthetic data, `make full`)

Profile `full` — **synthetic data** — x86_64 (4 threads), torch 2.15.0+cu132, onnxruntime 1.29.0, total 5305 s.

| Model | Input | Params | MACs | Weights (int8) | Peak act. (int8, est.) | NPU-fit | Energy/inf.: MACs x pJ/MAC (+ fixed) [MAC-term range] | CPU latency float / int8 (1 thread) |
|---|---|---|---|---|---|---|---|---|
| person | 256x256 | 218,159 | 111.7 M | 226 KiB | 1024 KiB | PASS | 0.402 + 0.153 = **0.555 mJ** [0.075–1.788] | 2.3 / 4.5 ms |
| face | 160x160 | 82,717 | 19.1 M | 87 KiB | 300 KiB | PASS | 0.069 + 0.153 = **0.222 mJ** [0.013–0.306] | 0.5 / 1.2 ms |
| emb | 112x112 | 985,792 | 221.0 M | 991 KiB | 588 KiB | PASS | 0.795 + 0.153 = **0.948 mJ** [0.148–3.535] | 3.3 / 5.8 ms |

| Model | Metric | float | int8 PTQ (torch.ao) | int8 QAT (torch.ao) | int8 PTQ (onnxruntime) |
|---|---|---|---|---|---|
| person | mAP@0.5 (%) | 89.6 | 89.1 | 89.6 | 89.6 |
| face | mAP@0.5 (%) | 96.2 | 95.9 | 96.0 | 96.4 |
| emb | TAR@FAR=0.01 (%) | 97.5 | 97.5 | 97.1 | 97.4 |
| emb | TAR@FAR=0.001 (%) | 84.0 | 84.4 | 83.4 | 83.8 |
| emb | TAR@FAR=0.01 (int8 emb) (%) | 97.5 | 97.5 | 97.1 | 97.3 |
| emb | TAR@FAR=0.001 (int8 emb) (%) | 83.9 | 84.4 | 83.6 | 84.0 |

Parity, float ONNX vs int8 QDQ ONNX on 32 validation inputs. The tolerances apply to PTQ (same weights, so the gap is pure quantisation error) and are asserted by `check_parity`. QAT also fine-tunes the weights, so its distance from the float model is shown for information; the QAT criterion is the task-metric gap (mAP / TAR) in the table above.

| Model | Variant | Objectness prob max abs diff | mean abs diff | Box rel-L2 | Feature/embedding cosine (min) | Within tolerance |
|---|---|---|---|---|---|---|
| person | ptq | 0.129 | 0.0033 | 0.045 | 0.9872 | yes |
| person | ort_ptq | 0.130 | 0.0036 | 0.049 | 0.9475 | no |
| person | qat | 0.127 | 0.0077 | 0.097 | 0.9743 | n/a (info) |
| face | ptq | 0.064 | 0.0029 | 0.072 | – | yes |
| face | ort_ptq | 0.094 | 0.0034 | 0.080 | – | yes |
| face | qat | 0.138 | 0.0071 | 0.170 | – | n/a (info) |
| emb | ptq | – | – | – | 0.9904 (mean 0.9974) | yes |
| emb | ort_ptq | – | – | – | 0.9924 (mean 0.9975) | yes |
| emb | qat | – | – | – | 0.9650 (mean 0.9842) | n/a (info) |

Tolerances (PTQ): det_prob_max_abs 0.15, det_prob_mean_abs 0.01, det_box_rel_l2 0.1, feat_cos_min 0.97, emb_cos_mean 0.98, emb_cos_min 0.95, map_gap 0.03, tar_gap 0.03.

Energy = MACs x 3.6 pJ/MAC (range from the file's low/high) from `hardware/power_model/constants.yaml`.
With the file's fixed per-inference overhead (0.153 mJ) added: person 0.555 mJ, face 0.222 mJ, emb 0.948 mJ.

The onnxruntime-PTQ cross-check misses one tolerance: the person 64-d feature cosine minimum is 0.9475, below the 0.97 tolerance. Its MinMax calibration of the pooled feature is coarser than torch.ao's. The shipping paths are torch.ao PTQ and QAT, and both pass.


What the tables show:

- **float → int8**: the int8 gap is small on every model, with PTQ and QAT both within the
  `map_gap`/`tar_gap` tolerance of 0.03. On this data PTQ is already enough. PLAN.md §5.2 says to
  use QAT when the gap exceeds 2 mAP, so re-check this on real data.
- **"int8 emb" rows**: the full device path is int8 model → L2 norm → int8 embedding (§1) → int8
  cosine. It costs nothing measurable compared with float cosine.
- **CPU latency**: measured with onnxruntime on a single x86 thread, and only meant for relative
  comparison. int8 QDQ is *slower* than float on x86 onnxruntime. The NPU numbers come in M1.
- **Energy**: an estimate from MACs × constants, not a measurement. The PPK2 measurement is in M1.
  The person detector is **0.112 GMAC per frame**; `hardware/power_model/constants.yaml`
  `npu.person_det_gmac` (currently a 0.7 estimate) can be updated to this value by WS5.

## Tests

`make test` runs the files below.

| Test file | What it covers |
|---|---|
| `test_models.py` | op whitelist at module level, shapes, parameter budget, exact BN folding |
| `test_data.py` | renderer determinism and validity |
| `test_detection.py` | assignment, encode/decode round trip incl. landmarks, NMS, loss decreases |
| `test_metrics.py` | AP and TAR@FAR on hand-made cases |
| `test_quant.py` | QDQ structure (int8 weights, int32 bias, fixed input qparams); NPU-fit of the PTQ, QAT and ORT exports; float-vs-int8 parity within `pipeline.TOL`; torch fake-quant vs onnxruntime int8 (≥ 97 % of outputs within 1 LSB) |
| `test_npu_fit.py` | MAC and parameter counts, disallowed ops → exit 1, op-set hash equal to `tools/sentinel_sign.py` |
| `test_gallery.py`, `test_golden.py` | §1 reference, blob errors, golden reproducibility and float32-vs-float64 agreement |
| `test_energy.py` | energy model |
| `test_real_loaders.py` | real-dataset hooks |
| `test_pipeline.py` | quick end-to-end run of all 3 models, npu_fit CLI on the exports, train/export CLIs, the device-embedding rounding rule |
