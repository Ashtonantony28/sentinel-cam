# Vendored third-party code

## micro-ecc (ECDSA P-256 verify)

| | |
|---|---|
| Path | `vendor/micro-ecc/` |
| Upstream | https://github.com/kmackay/micro-ecc |
| Version | `master` at commit `541b3a78026420a3e369c4c9281c396b5e531113` (2024-11-13, "Merge pull request #230 from kmackay/hcca"). This is the commit the `v1.2` tag points to and includes the HCCA side-channel mitigation that `v1.1` (2020) lacks. |
| License | BSD-2-Clause, Copyright (c) 2014 Kenneth MacKay. Full text in `vendor/micro-ecc/LICENSE.txt` (must ship with any binary distribution). |
| Files taken | `uECC.c`, `uECC.h`, `uECC_vli.h`, `types.h`, `platform-specific.inc`, `curve-specific.inc`, `asm_arm.inc`, `asm_arm_mult_square.inc`, `asm_arm_mult_square_umaal.inc`, `LICENSE.txt`. AVR assembly, tests, examples and build scripts were not copied. |
| Local modifications | None. Files are byte-identical to upstream (`sha256(uECC.c) = e9db3217c2e8aacb2e1930004a9236b7502d4f0d44fd5796f45e3923f61eaeba`, `sha256(uECC.h) = 9d480aa6920155f68503064bebefb815a82aab318ab305efdcf04674d2a9cba7`). |
| Configuration | Compile definitions in `CMakeLists.txt`: only `secp256r1` enabled, compressed points off. Built with `-Wall -Wextra -Werror -O2` (no `-Wpedantic`: it uses `unsigned __int128` on 64-bit hosts). |
| How it is used | Only through `src/ota_crypto_uecc.c` (`uECC_verify`, `uECC_valid_public_key`). The firmware never signs, so it never needs an RNG. Host tests additionally use `uECC_make_key`/`uECC_sign` with a seeded deterministic RNG to build test images. |

To update: copy the same file list from a newer upstream commit, update the
commit hash and checksums above, and run `make -C tools test OTA_CTEST=1`.

Everything else in `firmware/ota` (SHA-256, CRC-32, manifest, metadata, slot
state machine, writer, simulator) is original code written for Sentinel.
