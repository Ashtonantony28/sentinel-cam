# firmware/ota: A/B OTA, anti-rollback, signed images (WS3)

Portable C11 library (no malloc, no OS) that the STM32N6 bootloader and app
use to accept, verify, boot, confirm and roll back firmware and model images
stored in external octal NOR. Python signing tool: `tools/sentinel_sign.py`.

> **Contract notes (INTERFACES.md §4).** The image and manifest format is
> implemented exactly as written. Nothing in §4 was changed. Where §4 left
> room, this is what was chosen (none of it changes a byte on the wire):
> 1. **One security counter per kind** (fw_n6, fw_c6, model). §4 says
>    "stored counter"; a single shared counter would let a model release block
>    firmware updates and the other way round.
> 2. **Models use the same slot state machine** (EMPTY/PENDING/TRIAL/CONFIRMED/BAD)
>    with `ota_model_select()` / `ota_model_confirm()`, so a model is hot-swapped
>    without a reboot and rolled back if the app resets before confirming it.
> 3. **Op-set compatibility = exact equality** of the 32-byte `model_op_set_hash`
>    and the running firmware's hash. The hash is defined (new, proposed for
>    §4) as `sha256("sentinel-opset-v1\n" + sorted unique lower-case op names, one per line)`;
>    see `op_set_hash()` in `tools/sentinel_sign.py`. WS1 should emit op lists
>    in that form.
> 4. **Strict manifest validation**: reserved bytes must be zero, unknown flag
>    bits are rejected, `load_addr` must be non-zero for firmware and zero for
>    models, `payload_size` must be non-zero and fit the slot.
> 5. **fw_c6 images** are verified by `ota_image_verify_buffer()` (same manifest,
>    same key) but are not stored in N6 slots; the C6 applies them with ESP-IDF's
>    own A/B OTA. The N6 slot writer rejects kind=fw_c6 with `ERR_KIND`.

## Layout

```
include/ota/       public headers (ota.h is the umbrella)
src/               library: sha256, crc32, manifest, metadata, slots + writer, crypto backend
vendor/micro-ecc/  ECDSA P-256 verify (BSD-2-Clause, see VENDORED.md)
sim/               host NOR simulator with power-cut injection
tests/             unit tests (ctest ota.unit.*) and fault injection (ota.fault_injection)
host/              ota_host_verify: CLI used by the Python cross tests
```

Build and test standalone: `cmake -S firmware/ota -B firmware/ota/build && cmake --build firmware/ota/build && ctest --test-dir firmware/ota/build`
(sanitizers are on by default in a standalone host build). As
`add_subdirectory(ota)` from `firmware/`, the parent's flags apply (firmware/
runs a plain and an ASan+UBSan pass). Python side: `make -C tools test`
(`OTA_CTEST=1` also runs this ctest suite). Long soak: `make -C tools fault-soak RUNS=20000 SEED=7`.

## How it works

### Flash abstraction
`ota_flash_t` has `read`, `program` (one page at most, can only clear bits)
and `erase` (one block to 0xFF). `ota_layout_t` places two metadata blocks,
two fw slots and two model slots; `ota_layout_check()` enforces alignment and
no overlap. Proposed layout for the 128 MB MX66UW-class NOR on XSPI2
(**to-verify** against the FSBL location the N6 boot ROM expects and the final
image sizes):

| Region | Offset | Size |
|---|---|---|
| FSBL (signed by ST tooling, loaded by boot ROM) | 0x0000000 | 1 MB (to-verify) |
| OTA metadata copy 0 / copy 1 | 0x0100000 / 0x0101000 | 4 KB each |
| fw_n6 slot A / B | 0x0200000 / 0x0A00000 | 8 MB each |
| model slot A / B | 0x1200000 / 0x2200000 | 16 MB each |
| gallery, logs, spare | 0x3200000 | rest |

### Redundant metadata
A 40-byte record (`ota_meta.h`): slot states, active slot per group, the three
security counters, a sequence number and a CRC-32. Two copies live in two
erase blocks. Load picks the valid copy with the newest sequence number
(serial-number comparison, wrap-safe). Store always erases and programs the
copy that is *not* the newest, then reads it back. A power cut at any point
of a store therefore leaves the previous record intact, and the next boot
sees either the old or the new record, never neither. (Exhaustively tested
per operation in `ota.unit.meta_power_cut`.)

### Slot state machine and boot decision
`ota_boot_decide()` (bootloader, fw_n6) and `ota_model_select()` (app, models):

1. Any slot in TRIAL did not confirm before the last reset: mark it BAD.
2. A PENDING slot (inactive slot first) is fully re-verified (signature,
   sha256, kind, size, `security_counter >= stored`, op-set for models). If it
   passes it becomes TRIAL and active and is booted; if not it becomes BAD.
3. Otherwise boot the CONFIRMED slot, active first, after the same full
   re-verification. A CONFIRMED image that no longer verifies becomes BAD.
4. All state changes of one decision are committed in a single metadata
   write, before the jump.

A model whose op-set does not match the running firmware is *skipped, not
marked BAD*: it is still needed if a firmware trial rolls back.

`ota_fw_confirm()` / `ota_model_confirm()`: TRIAL becomes CONFIRMED and the
stored counter becomes `max(stored, image counter)`, in the same metadata
write. The 60 s confirm deadline is the app's job: arm the IWDG at boot and
only confirm after camera, NPU self-test and network checks pass.

### Streaming writer
`ota_writer_begin(kind)` picks the inactive slot and refuses unless the
running image of that group is CONFIRMED (no update from a TRIAL image, so a
CONFIRMED fallback always exists while the other slot is rewritten). It sets
the target slot to EMPTY in metadata *before* erasing anything. `write()`
takes any chunk size; it validates the manifest (structure, signature, kind,
counter, size, op-set) as soon as its 256 bytes are in, so a bad image is
rejected before any payload is programmed, then hashes and programs the
payload page by page, erasing blocks lazily. `finish()` checks the length and
sha256, re-reads and re-verifies the whole slot from flash, and only then
marks it PENDING. Errors are sticky.

### Known behaviour to be aware of
- A power loss during a firmware's trial boot (before it confirms) counts as
  a failed trial, as §4 specifies: the update is rolled back and the backend
  must re-offer it. On a battery device this is rare but possible. A
  boot-attempt counter would avoid it but would change the §4 rule, so it is
  not implemented.
- The library has no clock or watchdog; the 60 s deadline is enforced by the
  app plus the hardware watchdog.

## Threat model

| Threat | Defended? | How / why not |
|---|---|---|
| Network attacker or malicious backend pushes a modified or unsigned image | Yes | ECDSA P-256 over the manifest, manifest binds payload sha256; verified while streaming, again from flash before PENDING, and again on every boot. |
| Image from another product or another kind (model into fw slot, C6 fw into N6 slot) | Yes | `kind` is signed and checked against the slot group. |
| Downgrade to an older, validly signed, vulnerable image via OTA | Yes | `security_counter < stored` rejected in the writer and at boot. Releases that fix vulnerabilities must bump the counter; same counter with a new version is allowed. |
| Model incompatible with the running firmware's NPU op set | Yes (functional, not security) | op-set hash equality checked in the writer and at selection. |
| Power loss at any point of an update, of a metadata write, of the boot decision, or of confirm | Yes | Redundant metadata plus ordering rules; ≥ 1000-run randomized fault injection with zero bricks. |
| New firmware that hangs, crashes or fails health checks | Yes | Trial boot plus confirm; the next boot marks it BAD and boots the CONFIRMED fallback. |
| Bit rot or tampering of a stored image (PENDING or CONFIRMED) | Detected | Full re-verify on every boot; a failing slot is marked BAD. If *both* fw slots are bad the device has nothing to boot (recovery mode is out of scope for this library). |
| Physical attacker rewrites NOR: old signed image and forged metadata with lower counter | **Only on hardware** | In this library the counters live in NOR metadata, which an attacker with a flash programmer can rewrite. On the N6 the counter must be mirrored in OTP fuses / secure storage and checked by the bootloader (see below). Covered in tests only for the case where the forged metadata keeps the counter (`ota.unit.physical_downgrade`). |
| Physical attacker replaces the bootloader or the public key in NOR | **Only on hardware** | Requires N6 secure boot: the ROM authenticates the FSBL (which contains the OTA public key) against a key hash in OTP. Without secure boot enabled nothing here holds. |
| Signing key compromise | No | One root key, no revocation list. Mitigation: key kept offline (HSM or offline machine), `sentinel_sign.py keygen` writes the private key 0600 and unencrypted. Future work: key ID in a reserved manifest byte plus OTP key-revocation bits (would change §4). |
| Fault injection on the CPU (glitching the verify branch), side channels | No | micro-ecc verify is not hardened against glitching. ST's secure boot ROM and the N6 product-state lifecycle are the line of defence (to-verify). |
| Confidentiality of firmware/model | No | Images are signed, not encrypted. |
| Denial of service (attacker floods bogus images) | Partly | Bad manifests are rejected after 256 bytes, before any slot erase beyond the first block; flash wear from repeated attempts is not rate-limited here. |

## Mapping onto STM32N6 secure boot (real hardware)

Everything in this section is the intended design. Items marked **to-verify**
must be checked against ST's RM0486 reference manual, the STM32N6 security
docs and the STM32CubeN6 examples during M5.

1. **Root of trust.** The N6 has no internal flash. The boot ROM loads the
   first-stage bootloader (FSBL) from external XSPI NOR into internal SRAM and,
   in the closed product state, authenticates it with an OEM key whose hash is
   fused in OTP (ST's signing tool / STM32CubeProgrammer `STM32_SigningTool`).
   **to-verify:** exact header format, key type (ECDSA P-256 vs P-384), and
   where in NOR the ROM looks for the FSBL.
2. **Our bootloader = the FSBL** (or runs inside it). It links this library,
   embeds the OTA public key (`sentinel_ota.pub.h` from `keygen`; or stores
   its hash in an OTP word, to-verify which), runs `ota_boot_decide()`, and
   jumps to the chosen slot. The app runs execute-in-place from the XSPI2
   memory-mapped window, or is copied to SRAM/PSRAM (`load_addr` says where).
   **to-verify:** XIP performance from octal NOR vs copy-to-RAM.
3. **Anti-rollback counter in OTP.** The NOR metadata counter is a cache. On
   confirm, the bootloader (or a secure service) also burns OTP so that the
   OTP value = max confirmed counter, and `ota_boot_decide()` should take
   `min_security_counter = max(meta counter, OTP counter)`. Because fuses are
   one-way, encode the counter as a thermometer code (one bit per increment),
   which bounds the number of security-relevant releases by the number of
   spare OTP bits; bump the counter only for security fixes.
   **to-verify:** number of free OTP (BSEC) words for OEM use and their
   programming constraints. The hook to add is a single extra argument to the
   policy, no format change.
4. **Crypto acceleration.** `ota_crypto.h` is the only crypto entry point; a
   backend using the N6 HASH and PKA peripherals (**to-verify** that PKA is
   present on the N657 and supports ECDSA verify) can replace
   `src/ota_crypto_uecc.c`. micro-ecc has Thumb-2 assembly that can be
   enabled for the Cortex-M55 meanwhile.
5. **TrustZone.** The bootloader and the metadata/OTP writes run in the
   secure world; the app (non-secure) gets a small secure-callable API:
   `confirm`, `writer_begin/write/finish`. The XSPI region holding the slots
   and metadata should be secure-only via the MPCBB/MPCWM watermark so a
   compromised non-secure app cannot forge metadata (**to-verify** N6
   memory-protection controller naming and granularity for XSPI).
6. **Debug.** Ship units in the closed/locked product state with debug
   authentication; an open SWD port makes everything above moot.
   **to-verify:** N6 lifecycle names and how regression to open works.
7. **Device identity.** Separate from OTA: per-device key for mTLS (secure
   element or N6 secure storage), see PLAN §6.

## API sketch

```c
ota_ctx_t ctx;
ota_init(&ctx, &flash, &layout, OTA_PUBKEY);      // loads redundant metadata
ota_boot_result_t br;
ota_boot_decide(&ctx, &br);                       // bootloader: pick + mark
/* app */ ota_model_select(&ctx, &mr);            // pick model compatible with running fw
/* health checks ok */ ota_fw_confirm(&ctx); ota_model_confirm(&ctx);

ota_writer_t w;                                   // update from the C6 link
ota_writer_begin(&ctx, &w, OTA_KIND_FW_N6);
while (chunk) ota_writer_write(&w, chunk, len);   // any chunk size
ota_writer_finish(&w);                            // -> PENDING, reboot to try it
```

All functions return `OTA_OK` (0) or a negative `ota_err_t`;
`ota_err_str()` gives the name (the Python tool uses the same names).

## Tests

- `ota.unit.*` (22 ctest cases): NIST SHA-256 vectors, CRC-32, ECDSA
  accept/reject (including r = n, zero signature, wrong key), every manifest
  rejection path, **every single-bit flip of the 256-byte manifest rejected**,
  simulator semantics, metadata redundancy, sequence wrap, metadata power
  cuts, writer with chunk sizes 1..whole, every writer rejection, state
  rules, never-confirming firmware and models, corrupted PENDING image,
  counter rules, physical downgrade, op-set mismatch with firmware rollback,
  and an exhaustive "cut power at every flash op of an update" sweep.
- `ota.fault_injection`: 2000 randomized runs by default
  (`--runs`, `--seed`, or `OTA_FI_RUNS` / `OTA_FI_SEED`; CMake cache
  `OTA_FI_RUNS` / `OTA_FI_SEED`). See the header of
  `tests/test_fault_injection.c` for the scenarios and the invariant. A
  failure prints the master seed, run index and the exact command to
  reproduce it.
- `tools/tests` (pytest): signing tool unit tests and Python-to-C cross tests
  (whole-buffer and streamed verification; every manifest field and byte
  tampered; payload, key, rollback, op-set, kind, truncation; C and Python
  must agree on the error name).
