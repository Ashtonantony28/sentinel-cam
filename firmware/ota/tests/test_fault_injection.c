/* Randomized fault injection for the OTA slot manager.
 *
 * One simulated device lives through --runs update attempts. Each run picks a
 * scenario (good fw, good model, bad signature, wrong sha, rollback, firmware
 * that never confirms, model with a mismatched op-set hash, model that never
 * confirms, truncated stream, image corrupted in flash after verification)
 * and, most of the time, cuts power at a random program/erase operation
 * anywhere in the session (streaming, metadata updates in the writer, the
 * boot decision, and confirm). After EVERY power-on the boot decision is
 * checked against an independent oracle:
 *   - it returns an image (zero bricks)
 *   - the slot is TRIAL or CONFIRMED
 *   - the image in flash verifies (ECDSA via micro-ecc directly + sha256)
 *   - image security_counter >= stored counter, and stored counters never
 *     decrease across boots
 *   - the selected model verifies, matches the firmware's op-set hash, and
 *     respects the model counter
 * Without a cut, the run's expected outcome is also checked exactly.
 *
 * Usage: ota_test_fault [--runs N] [--seed S] [--verbose]
 * Env:   OTA_FI_RUNS, OTA_FI_SEED override the defaults. */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ota/ota.h"
#include "test_support.h"
#include "uECC.h"

enum {
    SC_FW_GOOD,
    SC_MODEL_GOOD,
    SC_FW_BADSIG,
    SC_FW_BADSHA,
    SC_FW_ROLLBACK,
    SC_FW_NEVER_CONFIRMS,
    SC_MODEL_OPSET,
    SC_MODEL_NEVER_CONFIRMS,
    SC_MODEL_BADSIG,
    SC_MODEL_ROLLBACK,
    SC_FW_TRUNCATED,
    SC_FW_CORRUPT_IN_FLASH,
    SC_COUNT
};
static const char *SC_NAME[SC_COUNT] = {"fw_good",        "model_good",       "fw_badsig",
                                        "fw_badsha",      "fw_rollback",      "fw_never_confirms",
                                        "model_opset",    "model_never_conf", "model_badsig",
                                        "model_rollback", "fw_truncated",     "fw_corrupt_in_flash"};
static const unsigned SC_WEIGHT[SC_COUNT] = {22, 14, 7, 7, 7, 10, 7, 6, 4, 4, 5, 7};

/* Patch number 255 marks an image whose app never passes its health check. */
#define BROKEN_PATCH 0xFFu
static int is_broken(uint32_t version) { return (version & 0xFFu) == BROKEN_PATCH; }

static tb_dev_t g_dev;
static tb_key_t g_key;
static uint8_t g_img[TB_FW_SIZE];
static uint64_t g_master_seed;
static uint64_t g_run_seed;
static long g_run = -1;
static int g_verbose;
static uint8_t g_snap_mem[TB_FLASH_SIZE];

/* oracle state */
static uint32_t g_hw_ctr[OTA_KIND_COUNT];
static uint32_t g_next_minor = 1;

/* stats */
typedef struct {
    uint64_t boots, checks, cut_meta, cut_erase, cut_prog, cut_runs, bricks, violations, sc[SC_COUNT],
        reached_new, expect_ok;
} stats_t;
static stats_t g_st;
static uint64_t g_dry_runs; /* uncut rehearsals of cut runs (each fully checked) */
#define st_boots g_st.boots
#define st_checks g_st.checks
#define st_cut_meta g_st.cut_meta
#define st_cut_erase g_st.cut_erase
#define st_cut_prog g_st.cut_prog
#define st_cut_runs g_st.cut_runs
#define st_bricks g_st.bricks
#define st_violations g_st.violations
#define st_sc g_st.sc
#define st_reached_new g_st.reached_new
#define st_expect_ok g_st.expect_ok

static void fail(const char *what) {
    st_violations++;
    fprintf(stderr,
            "FAULT-INJECTION FAILURE run=%ld: %s\n  reproduce: ota_test_fault --seed %" PRIu64
            " --runs %ld   (run seed %" PRIu64 ")\n",
            g_run, what, g_master_seed, g_run + 1, g_run_seed);
}

static uint64_t mix(uint64_t x) {
    x += 0x9E3779B97F4A7C15ull;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ull;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBull;
    return x ^ (x >> 31);
}

/* Independent check of an image in flash: decode the few fields we need by
 * hand, verify with micro-ecc directly, hash the payload. */
static int oracle_verify(uint32_t addr, uint32_t cap, uint8_t kind, uint32_t *ctr, uint8_t ophash[32]) {
    const uint8_t *m = g_dev.mem + addr;
    if (memcmp(m, "SOTA", 4) != 0 || m[6] != kind) return 0;
    uint32_t psize = ota_get_le32(m + 16);
    if (psize == 0 || psize > cap - 256) return 0;
    uint8_t d[32];
    ota_sha256(m, 192, d);
    if (!uECC_verify(g_key.pub, d, 32, m + 192, uECC_secp256r1())) return 0;
    ota_sha256(m + 256, psize, d);
    if (memcmp(d, m + 20, 32) != 0) return 0;
    *ctr = ota_get_le32(m + 12);
    if (ophash) memcpy(ophash, m + 60, 32);
    return 1;
}

static void check_counters(const ota_meta_t *mt) {
    for (int k = 0; k < OTA_KIND_COUNT; k++) {
        if (mt->sec_counter[k] < g_hw_ctr[k]) fail("stored security counter decreased");
        g_hw_ctr[k] = mt->sec_counter[k];
    }
}

/* One power-on: fresh RAM context, bootloader decision, app model selection,
 * health check + confirm. Returns 1 if the cycle completed (power stayed on). */
typedef struct {
    ota_ctx_t ctx;
    ota_boot_result_t fw, model;
    int fw_ok, model_ok;
} dev_state_t;

static int power_cycle(dev_state_t *d, int do_confirm) {
    d->fw_ok = d->model_ok = 0;
    if (!g_dev.sim.powered) return 0;
    st_boots++;
    int rc = ota_init(&d->ctx, &g_dev.flash, &g_dev.layout, g_key.pub);
    if (rc == OTA_ERR_FLASH && !g_dev.sim.powered) return 0;
    if (rc) {
        st_bricks++;
        fail("ota_init failed on a powered device (metadata lost)");
        return 0;
    }
    check_counters(&d->ctx.meta);
    rc = ota_boot_decide(&d->ctx, &d->fw);
    if (rc == OTA_ERR_FLASH && !g_dev.sim.powered) return 0;
    st_checks++;
    if (rc) {
        st_bricks++;
        fail("BRICK: boot decision found no bootable firmware");
        return 0;
    }
    uint8_t st = d->ctx.meta.fw_state[d->fw.slot];
    uint32_t ctr;
    uint8_t fw_op[32];
    if (st != OTA_SLOT_TRIAL && st != OTA_SLOT_CONFIRMED) fail("booted fw slot not TRIAL/CONFIRMED");
    if (!oracle_verify(d->fw.addr, TB_FW_SIZE, OTA_KIND_FW_N6, &ctr, fw_op)) fail("booted fw does not verify");
    if (ctr != d->fw.manifest.security_counter) fail("boot result manifest mismatch");
    if (ctr < d->ctx.meta.sec_counter[0] || ctr < g_hw_ctr[0]) fail("booted fw below security counter");
    check_counters(&d->ctx.meta);
    d->fw_ok = 1;

    rc = ota_model_select(&d->ctx, &d->model);
    if (rc == OTA_ERR_FLASH && !g_dev.sim.powered) return 0;
    st_checks++;
    if (rc) {
        st_bricks++;
        fail("no compatible model selectable");
        return 0;
    }
    uint8_t mop[32];
    st = d->ctx.meta.model_state[d->model.slot];
    if (st != OTA_SLOT_TRIAL && st != OTA_SLOT_CONFIRMED) fail("model slot not TRIAL/CONFIRMED");
    if (!oracle_verify(d->model.addr, TB_MODEL_SIZE, OTA_KIND_MODEL, &ctr, mop)) fail("model does not verify");
    if (memcmp(mop, fw_op, 32) != 0) fail("model op-set hash != firmware op-set hash");
    if (ctr < d->ctx.meta.sec_counter[2] || ctr < g_hw_ctr[2]) fail("model below security counter");
    check_counters(&d->ctx.meta);
    d->model_ok = 1;

    if (do_confirm) {
        if (!is_broken(d->fw.manifest.version)) {
            rc = ota_fw_confirm(&d->ctx);
            if (rc == OTA_ERR_FLASH && !g_dev.sim.powered) return 0;
            if (rc) fail("fw confirm failed");
            /* model health check only runs on healthy firmware */
            if (!is_broken(d->model.manifest.version)) {
                rc = ota_model_confirm(&d->ctx);
                if (rc == OTA_ERR_FLASH && !g_dev.sim.powered) return 0;
                if (rc) fail("model confirm failed");
            }
        }
    }
    return g_dev.sim.powered;
}

static int steady(const dev_state_t *d) {
    return d->fw_ok && d->model_ok && d->ctx.meta.fw_state[d->fw.slot] == OTA_SLOT_CONFIRMED &&
           d->ctx.meta.model_state[d->model.slot] == OTA_SLOT_CONFIRMED;
}

static unsigned pick_scenario(void) {
    unsigned total = 0;
    for (int i = 0; i < SC_COUNT; i++) total += SC_WEIGHT[i];
    unsigned r = tb_rand_below(total);
    for (unsigned i = 0; i < SC_COUNT; i++) {
        if (r < SC_WEIGHT[i]) return i;
        r -= SC_WEIGHT[i];
    }
    return 0;
}

static int stream_image(ota_ctx_t *ctx, uint8_t kind, uint32_t send) {
    ota_writer_t w;
    int rc = ota_writer_begin(ctx, &w, kind);
    if (rc) return rc;
    uint32_t max_chunk = 1u + tb_rand_below(tb_rand_below(4) == 0 ? 17 : 3000);
    for (uint32_t off = 0; off < send;) {
        uint32_t k = 1u + tb_rand_below(max_chunk);
        if (k > send - off) k = send - off;
        rc = ota_writer_write(&w, g_img + off, k);
        if (rc) return rc;
        off += k;
    }
    return ota_writer_finish(&w);
}

enum { CUT_NONE, CUT_ANY, CUT_META };

/* One update session. With CUT_ANY the cut_index-th flash op of the session
 * loses power; with CUT_META the cut_index-th op touching the metadata
 * blocks. *ops / *meta_ops receive the session's op counts. */
static void run_one(dev_state_t *d, int cut_mode, uint64_t cut_index, uint64_t *ops, uint64_t *meta_ops) {
    tb_rng_seed(g_run_seed);
    ota_sim_seed(&g_dev.sim, mix(g_run_seed));
    unsigned sc = pick_scenario();
    st_sc[sc]++;
    const uint32_t prev_fw = d->fw.manifest.version;
    const uint32_t prev_model = d->model.manifest.version;
    const int prev_fw_slot = d->fw.slot;

    /* ---- build the image for this scenario */
    int is_model = sc == SC_MODEL_GOOD || sc == SC_MODEL_OPSET || sc == SC_MODEL_NEVER_CONFIRMS ||
                   sc == SC_MODEL_BADSIG || sc == SC_MODEL_ROLLBACK;
    uint8_t kind = is_model ? OTA_KIND_MODEL : OTA_KIND_FW_N6;
    uint32_t cap = is_model ? TB_MODEL_SIZE : TB_FW_SIZE;
    tb_spec_t s;
    tb_spec_default(&s, kind);
    uint32_t stored = d->ctx.meta.sec_counter[kind - 1];
    s.version = (g_next_minor % 0xFFFFu) << 8; /* unique major.minor, patch 0 */
    g_next_minor++;
    s.counter = stored + tb_rand_below(3);
    s.payload_size = tb_rand_below(10) == 0 ? cap - 256 - tb_rand_below(3) : 1 + tb_rand_below(cap / 3);
    s.payload_seed = (uint32_t)g_run_seed;
    if (sc == SC_FW_NEVER_CONFIRMS || sc == SC_MODEL_NEVER_CONFIRMS) s.version |= BROKEN_PATCH;
    if (sc == SC_FW_ROLLBACK || sc == SC_MODEL_ROLLBACK) {
        if (stored == 0) {
            sc = is_model ? SC_MODEL_GOOD : SC_FW_GOOD;
        } else {
            s.counter = stored - 1u - tb_rand_below(stored);
        }
    }
    if (sc == SC_MODEL_OPSET) memcpy(s.op_hash, TB_OPHASH_B, 32);
    uint32_t n = tb_build(&s, &g_key, g_img, sizeof g_img);
    uint32_t send = n;
    int expect_rc = OTA_OK;
    switch (sc) {
    case SC_FW_BADSIG:
    case SC_MODEL_BADSIG:
        g_img[192 + tb_rand_below(64)] ^= (uint8_t)(1u << tb_rand_below(8));
        expect_rc = OTA_ERR_SIGNATURE;
        break;
    case SC_FW_BADSHA:
        g_img[256 + tb_rand_below(s.payload_size)] ^= (uint8_t)(1u << tb_rand_below(8));
        expect_rc = OTA_ERR_HASH;
        break;
    case SC_FW_ROLLBACK:
    case SC_MODEL_ROLLBACK:
        expect_rc = OTA_ERR_ROLLBACK;
        break;
    case SC_MODEL_OPSET:
        expect_rc = OTA_ERR_OPSET;
        break;
    case SC_FW_TRUNCATED:
        send = tb_rand_below(n);
        expect_rc = OTA_ERR_TRUNCATED;
        break;
    default:
        break;
    }

    /* ---- arm the power cut */
    uint64_t cuts_before = g_dev.sim.n_cuts;
    uint64_t ops_before = ota_sim_ops(&g_dev.sim), meta_before = g_dev.sim.n_watch_ops;
    if (cut_mode == CUT_ANY) {
        ota_sim_arm_cut(&g_dev.sim, cut_index);
    } else if (cut_mode == CUT_META) {
        ota_sim_arm_cut_range(&g_dev.sim, cut_index, g_dev.layout.meta_addr[0], g_dev.layout.meta_addr[1] + TB_ERASE);
    }

    /* ---- the session */
    int rc = stream_image(&d->ctx, kind, send);
    if (g_dev.sim.powered && rc != expect_rc) {
        char msg[160];
        snprintf(msg, sizeof msg, "%s: writer returned %s, expected %s", SC_NAME[sc], ota_err_str(rc),
                 ota_err_str(expect_rc));
        fail(msg);
    }
    if (g_dev.sim.powered && rc == OTA_OK && sc == SC_FW_CORRUPT_IN_FLASH) {
        /* bit rot / tampering after the writer verified the slot */
        uint32_t base = g_dev.layout.fw_addr[d->ctx.meta.fw_active ^ 1];
        uint32_t off = tb_rand_below(2) ? 256 + tb_rand_below(s.payload_size) : tb_rand_below(256);
        g_dev.mem[base + off] ^= (uint8_t)(1u << tb_rand_below(8));
    }
    if (is_model) {
        /* hot swap: app re-selects the model without a reboot */
        if (g_dev.sim.powered && ota_model_select(&d->ctx, &d->model) == OTA_OK && !is_broken(d->model.manifest.version)) {
            (void)ota_model_confirm(&d->ctx);
        }
        power_cycle(d, 1);
    } else {
        power_cycle(d, 1); /* reboot into the update */
        power_cycle(d, 1); /* reboot again: unconfirmed trial images are rejected here */
    }
    *ops = ota_sim_ops(&g_dev.sim) - ops_before;
    *meta_ops = g_dev.sim.n_watch_ops - meta_before;
    int cut = g_dev.sim.n_cuts > cuts_before;
    if (cut_mode != CUT_NONE && !cut) fail("armed power cut did not fire inside the session");
    if (cut) {
        st_cut_runs++;
        uint32_t a = g_dev.sim.last_cut_addr;
        if (a >= g_dev.layout.meta_addr[0] && a < g_dev.layout.meta_addr[1] + TB_ERASE) st_cut_meta++;
        if (g_dev.sim.last_cut_was_erase) {
            st_cut_erase++;
        } else {
            st_cut_prog++;
        }
    }
    ota_sim_power_on(&g_dev.sim); /* also disarms an unused cut */

    /* ---- recovery: power on until steady (both running images CONFIRMED) */
    int cycles = 0;
    do {
        power_cycle(d, 1);
        cycles++;
    } while (!steady(d) && cycles < 4 && st_bricks == 0);
    if (!steady(d)) fail("device did not reach a steady confirmed state");

    /* ---- exact expectations when no cut happened */
    if (!cut) {
        st_expect_ok++;
        uint32_t fw_now = d->fw.manifest.version, model_now = d->model.manifest.version;
        int ok = 1;
        switch (sc) {
        case SC_FW_GOOD:
            ok = fw_now == s.version && model_now == prev_model;
            break;
        case SC_MODEL_GOOD:
            ok = model_now == s.version && fw_now == prev_fw;
            break;
        case SC_FW_NEVER_CONFIRMS:
            ok = fw_now == prev_fw && d->fw.slot == prev_fw_slot &&
                 d->ctx.meta.fw_state[prev_fw_slot ^ 1] == OTA_SLOT_BAD;
            break;
        case SC_MODEL_NEVER_CONFIRMS:
            ok = model_now == prev_model && d->ctx.meta.model_state[d->model.slot ^ 1] == OTA_SLOT_BAD;
            break;
        case SC_FW_CORRUPT_IN_FLASH:
            ok = fw_now == prev_fw && d->ctx.meta.fw_state[prev_fw_slot ^ 1] == OTA_SLOT_BAD;
            break;
        default: /* rejected by the writer: nothing changes */
            ok = fw_now == prev_fw && model_now == prev_model;
            break;
        }
        if (!ok) {
            char msg[200];
            snprintf(msg, sizeof msg, "%s (no cut): unexpected outcome fw %06" PRIx32 "->%06" PRIx32
                     " model %06" PRIx32 "->%06" PRIx32 " (image %06" PRIx32 ")",
                     SC_NAME[sc], prev_fw, fw_now, prev_model, model_now, s.version);
            fail(msg);
        }
    }
    if (d->fw.manifest.version != prev_fw || d->model.manifest.version != prev_model) st_reached_new++;
    if (g_verbose) {
        printf("run %ld %-18s cut=%d fw=%06" PRIx32 " model=%06" PRIx32 " ctr=%" PRIu32 "/%" PRIu32 "\n", g_run,
               SC_NAME[sc], cut, d->fw.manifest.version, d->model.manifest.version, d->ctx.meta.sec_counter[0],
               d->ctx.meta.sec_counter[2]);
    }
}

int main(int argc, char **argv) {
    long runs = 2000;
    g_master_seed = 0x5E57C0DEull;
    const char *e = getenv("OTA_FI_RUNS");
    if (e && *e) runs = strtol(e, NULL, 0);
    e = getenv("OTA_FI_SEED");
    if (e && *e) g_master_seed = strtoull(e, NULL, 0);
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--runs") && i + 1 < argc) {
            runs = strtol(argv[++i], NULL, 0);
        } else if (!strcmp(argv[i], "--seed") && i + 1 < argc) {
            g_master_seed = strtoull(argv[++i], NULL, 0);
        } else if (!strcmp(argv[i], "--verbose")) {
            g_verbose = 1;
        } else {
            fprintf(stderr, "usage: %s [--runs N] [--seed S] [--verbose]\n", argv[0]);
            return 2;
        }
    }
    printf("ota fault injection: seed=%" PRIu64 " runs=%ld\n", g_master_seed, runs);

    tb_rng_seed(g_master_seed);
    tb_dev_init(&g_dev);
    tb_key_gen(&g_key);
    g_dev.sim.watch_lo = g_dev.layout.meta_addr[0];
    g_dev.sim.watch_hi = g_dev.layout.meta_addr[1] + TB_ERASE;
    dev_state_t d;
    memset(&d, 0, sizeof d);
    if (tb_factory(&g_dev, &d.ctx, &g_key, TB_OPHASH_A, g_img, sizeof g_img) != OTA_OK) {
        fprintf(stderr, "factory provisioning failed (seed %" PRIu64 ")\n", g_master_seed);
        return 1;
    }
    power_cycle(&d, 1);
    if (!steady(&d)) {
        fprintf(stderr, "factory device not steady (seed %" PRIu64 ")\n", g_master_seed);
        return 1;
    }
    for (g_run = 0; g_run < runs; g_run++) {
        g_run_seed = mix(g_master_seed ^ ((uint64_t)g_run * 0xD1B54A32D192ED03ull));
        /* 80% of runs lose power: rehearse the session without a cut (fully
         * checked) to learn its op count, roll the device back, then replay
         * it with the cut at a uniformly random op (55%) or metadata op (25%). */
        uint64_t cr = mix(g_run_seed ^ 0xC07C07ull);
        unsigned mode = (unsigned)(cr % 100u);
        uint64_t ops = 0, mops = 0;
        if (mode < 80) {
            memcpy(g_snap_mem, g_dev.mem, TB_FLASH_SIZE);
            ota_sim_flash_t sim_s = g_dev.sim;
            dev_state_t d_s = d;
            stats_t st_s = g_st;
            uint32_t hw_s[OTA_KIND_COUNT];
            memcpy(hw_s, g_hw_ctr, sizeof hw_s);
            uint32_t minor_s = g_next_minor;
            run_one(&d, CUT_NONE, 0, &ops, &mops);
            g_dry_runs++;
            uint64_t v = g_st.violations, b = g_st.bricks;
            memcpy(g_dev.mem, g_snap_mem, TB_FLASH_SIZE);
            g_dev.sim = sim_s;
            d = d_s;
            g_st = st_s;
            g_st.violations += v - st_s.violations;
            g_st.bricks += b - st_s.bricks;
            memcpy(g_hw_ctr, hw_s, sizeof hw_s);
            g_next_minor = minor_s;
            if (ops == 0) {
                run_one(&d, CUT_NONE, 0, &ops, &mops); /* session touched no flash: nothing to cut */
            } else if (mode < 55 || mops == 0) {
                run_one(&d, CUT_ANY, mix(cr) % ops, &ops, &mops);
            } else {
                run_one(&d, CUT_META, mix(cr) % mops, &ops, &mops);
            }
        } else {
            run_one(&d, CUT_NONE, 0, &ops, &mops);
        }
        if (st_violations && !g_verbose) break; /* first failure is the interesting one */
    }
    printf("runs=%ld boots=%" PRIu64 " boot/model checks=%" PRIu64 " (+%" PRIu64 " uncut rehearsals, also checked)\n",
           g_run, st_boots, st_checks, g_dry_runs);
    printf("power cuts=%" PRIu64 " (runs with a cut %" PRIu64 ", during erase %" PRIu64 ", during program %" PRIu64
           ", in metadata region %" PRIu64 ")\n",
           g_dev.sim.n_cuts, st_cut_runs, st_cut_erase, st_cut_prog, st_cut_meta);
    printf("runs with exact outcome checked (no cut)=%" PRIu64 ", runs that changed running image=%" PRIu64 "\n",
           st_expect_ok, st_reached_new);
    printf("scenarios:");
    for (int i = 0; i < SC_COUNT; i++) printf(" %s=%" PRIu64, SC_NAME[i], st_sc[i]);
    printf("\nfinal counters fw=%" PRIu32 " model=%" PRIu32 ", flash ops=%" PRIu64 "\n", d.ctx.meta.sec_counter[0],
           d.ctx.meta.sec_counter[2], ota_sim_ops(&g_dev.sim));
    uint64_t st_cuts = g_dev.sim.n_cuts;
    printf("bricks=%" PRIu64 " invariant violations=%" PRIu64 " -> %s\n", st_bricks, st_violations,
           (st_bricks || st_violations) ? "FAIL" : "PASS");
    if (st_bricks || st_violations) {
        fprintf(stderr, "seed=%" PRIu64 " (rerun with --seed %" PRIu64 " --verbose)\n", g_master_seed, g_master_seed);
        return 1;
    }
    if (st_cuts < (uint64_t)g_run / 2) {
        fprintf(stderr, "too few power cuts injected (%" PRIu64 ")\n", st_cuts);
        return 1;
    }
    return 0;
}
