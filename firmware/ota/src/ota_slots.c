/* Slot state machine and boot / model-selection decisions. */
#include <string.h>

#include "ota/ota.h"

typedef struct {
    uint8_t *state;      /* [2] */
    uint8_t *active;
    const uint32_t *addr; /* [2] */
    uint32_t size;
    uint8_t kind;
} group_t;

static group_t group_of(ota_meta_t *m, const ota_layout_t *l, uint8_t kind) {
    group_t g;
    if (kind == OTA_KIND_MODEL) {
        g.state = m->model_state;
        g.active = &m->model_active;
        g.addr = l->model_addr;
        g.size = l->model_size;
    } else {
        g.state = m->fw_state;
        g.active = &m->fw_active;
        g.addr = l->fw_addr;
        g.size = l->fw_size;
    }
    g.kind = kind;
    return g;
}

static int meta_equal(const ota_meta_t *a, const ota_meta_t *b) {
    if (a->seq != b->seq || a->fw_active != b->fw_active || a->model_active != b->model_active) return 0;
    for (int i = 0; i < 2; i++) {
        if (a->fw_state[i] != b->fw_state[i] || a->model_state[i] != b->model_state[i]) return 0;
    }
    for (int i = 0; i < OTA_KIND_COUNT; i++) {
        if (a->sec_counter[i] != b->sec_counter[i]) return 0;
    }
    return 1;
}

static int commit(ota_ctx_t *ctx, ota_meta_t *m) {
    if (meta_equal(m, &ctx->meta)) return OTA_OK;
    int copy = ctx->meta_copy;
    int rc = ota_meta_store(ctx->flash, ctx->layout, m, &copy);
    if (rc) return rc;
    ctx->meta = *m;
    ctx->meta_copy = copy;
    return OTA_OK;
}

static void policy_for(const ota_ctx_t *ctx, const ota_meta_t *m, uint8_t kind, ota_policy_t *p) {
    memset(p, 0, sizeof *p);
    p->expect_kind = kind;
    p->min_security_counter = m->sec_counter[kind - 1];
    p->required_op_set_hash = (kind == OTA_KIND_MODEL) ? ctx->fw_manifest.op_set_hash : NULL;
}

int ota_init(ota_ctx_t *ctx, const ota_flash_t *flash, const ota_layout_t *layout, const uint8_t *pubkey) {
    if (!ctx || !flash || !layout || !pubkey) return OTA_ERR_PARAM;
    memset(ctx, 0, sizeof *ctx);
    int rc = ota_layout_check(layout, flash);
    if (rc) return rc;
    rc = ota_p256_pubkey_check(pubkey);
    if (rc) return rc;
    ctx->flash = flash;
    ctx->layout = layout;
    ctx->pubkey = pubkey;
    return ota_meta_load(flash, layout, &ctx->meta, &ctx->meta_copy);
}

/* Shared decision for fw_n6 (bootloader) and model (app) slot groups. */
static int decide(ota_ctx_t *ctx, uint8_t kind, ota_boot_result_t *out) {
    ota_meta_t m = ctx->meta;
    group_t g = group_of(&m, ctx->layout, kind);
    ota_policy_t pol;
    policy_for(ctx, &m, kind, &pol);

    /* Rule 1: a TRIAL slot at decision time never confirmed -> BAD. */
    for (int s = 0; s < 2; s++) {
        if (g.state[s] == OTA_SLOT_TRIAL) g.state[s] = OTA_SLOT_BAD;
    }

    int chosen = -1, trial = 0;
    ota_manifest_t man;
    memset(&man, 0, sizeof man);

    /* Rule 2: a verified PENDING image gets its trial. */
    for (int i = 0; i < 2 && chosen < 0; i++) {
        int s = i == 0 ? (*g.active ^ 1) : *g.active;
        if (g.state[s] != OTA_SLOT_PENDING) continue;
        int rc = ota_slot_verify(ctx->flash, g.addr[s], g.size, ctx->pubkey, &pol, &man);
        if (rc == OTA_OK) {
            g.state[s] = OTA_SLOT_TRIAL;
            *g.active = (uint8_t)s;
            chosen = s;
            trial = 1;
        } else if (rc == OTA_ERR_FLASH) {
            return rc; /* cannot read flash (power loss): change nothing */
        } else if (rc != OTA_ERR_OPSET) {
            g.state[s] = OTA_SLOT_BAD;
        }
    }
    /* Rule 3: fall back to a verified CONFIRMED image, active slot first. */
    for (int i = 0; i < 2 && chosen < 0; i++) {
        int s = i == 0 ? *g.active : (*g.active ^ 1);
        if (g.state[s] != OTA_SLOT_CONFIRMED) continue;
        int rc = ota_slot_verify(ctx->flash, g.addr[s], g.size, ctx->pubkey, &pol, &man);
        if (rc == OTA_OK) {
            *g.active = (uint8_t)s;
            chosen = s;
        } else if (rc == OTA_ERR_FLASH) {
            return rc;
        } else if (rc != OTA_ERR_OPSET) {
            g.state[s] = OTA_SLOT_BAD;
        }
    }

    int rc = commit(ctx, &m);
    if (rc) return rc;
    if (chosen < 0) return OTA_ERR_NO_IMAGE;
    if (out) {
        out->slot = chosen;
        out->trial = trial;
        out->addr = g.addr[chosen];
        out->manifest = man;
    }
    if (kind == OTA_KIND_MODEL) {
        ctx->model_running = 1;
        ctx->model_manifest = man;
    } else {
        ctx->fw_running = 1;
        ctx->fw_manifest = man;
    }
    return OTA_OK;
}

int ota_boot_decide(ota_ctx_t *ctx, ota_boot_result_t *out) {
    if (!ctx || !ctx->flash) return OTA_ERR_PARAM;
    ctx->fw_running = 0;
    ctx->model_running = 0;
    return decide(ctx, OTA_KIND_FW_N6, out);
}

int ota_model_select(ota_ctx_t *ctx, ota_boot_result_t *out) {
    if (!ctx || !ctx->flash) return OTA_ERR_PARAM;
    if (!ctx->fw_running) return OTA_ERR_STATE;
    ctx->model_running = 0;
    return decide(ctx, OTA_KIND_MODEL, out);
}

int ota_app_attach(ota_ctx_t *ctx) {
    if (!ctx || !ctx->flash) return OTA_ERR_PARAM;
    ota_meta_t m = ctx->meta;
    group_t g = group_of(&m, ctx->layout, OTA_KIND_FW_N6);
    uint8_t st = g.state[*g.active];
    if (st != OTA_SLOT_TRIAL && st != OTA_SLOT_CONFIRMED) return OTA_ERR_STATE;
    ota_policy_t pol;
    policy_for(ctx, &m, OTA_KIND_FW_N6, &pol);
    int rc = ota_slot_verify(ctx->flash, g.addr[*g.active], g.size, ctx->pubkey, &pol, &ctx->fw_manifest);
    if (rc) return rc;
    ctx->fw_running = 1;
    return OTA_OK;
}

static int confirm(ota_ctx_t *ctx, uint8_t kind) {
    int running = kind == OTA_KIND_MODEL ? ctx->model_running : ctx->fw_running;
    if (!running) return OTA_ERR_STATE;
    const ota_manifest_t *man = kind == OTA_KIND_MODEL ? &ctx->model_manifest : &ctx->fw_manifest;
    ota_meta_t m = ctx->meta;
    group_t g = group_of(&m, ctx->layout, kind);
    uint8_t *st = &g.state[*g.active];
    if (*st == OTA_SLOT_CONFIRMED) return OTA_OK;
    if (*st != OTA_SLOT_TRIAL) return OTA_ERR_STATE;
    *st = OTA_SLOT_CONFIRMED;
    if (man->security_counter > m.sec_counter[kind - 1]) m.sec_counter[kind - 1] = man->security_counter;
    return commit(ctx, &m);
}

int ota_fw_confirm(ota_ctx_t *ctx) {
    if (!ctx || !ctx->flash) return OTA_ERR_PARAM;
    return confirm(ctx, OTA_KIND_FW_N6);
}

int ota_model_confirm(ota_ctx_t *ctx) {
    if (!ctx || !ctx->flash) return OTA_ERR_PARAM;
    return confirm(ctx, OTA_KIND_MODEL);
}

/* ---------------------------------------------------------------- writer */

static int wfail(ota_writer_t *w, int rc) {
    if (w->state != OTA_W_FAILED) {
        w->err = rc;
        w->state = OTA_W_FAILED;
    }
    return w->err;
}

int ota_writer_begin(ota_ctx_t *ctx, ota_writer_t *w, uint8_t kind) {
    if (!ctx || !w || !ctx->flash) return OTA_ERR_PARAM;
    memset(w, 0, sizeof *w);
    w->state = OTA_W_FAILED;
    w->err = OTA_ERR_PARAM;
    if (kind != OTA_KIND_FW_N6 && kind != OTA_KIND_MODEL) return w->err = OTA_ERR_KIND;
    if (kind == OTA_KIND_MODEL && !ctx->fw_running) return w->err = OTA_ERR_STATE;

    ota_meta_t m = ctx->meta;
    group_t g = group_of(&m, ctx->layout, kind);
    int act = *g.active, tgt = act ^ 1;
    int live0 = g.state[0] == OTA_SLOT_CONFIRMED || g.state[0] == OTA_SLOT_TRIAL;
    int live1 = g.state[1] == OTA_SLOT_CONFIRMED || g.state[1] == OTA_SLOT_TRIAL;
    /* Only update from a confirmed running image (or a blank factory group),
     * so the fallback slot is always CONFIRMED while the target is rewritten. */
    if (!(g.state[act] == OTA_SLOT_CONFIRMED || (!live0 && !live1))) return w->err = OTA_ERR_STATE;

    /* Invalidate the target before touching its blocks. */
    if (g.state[tgt] != OTA_SLOT_EMPTY) {
        g.state[tgt] = OTA_SLOT_EMPTY;
        int rc = commit(ctx, &m);
        if (rc) return w->err = rc;
    }
    w->ctx = ctx;
    w->kind = kind;
    w->slot = tgt;
    w->base = g.addr[tgt];
    w->cap = g.size;
    w->page_addr = w->base;
    w->erased_end = w->base;
    ota_sha256_init(&w->sha);
    w->err = OTA_OK;
    w->state = OTA_W_ACTIVE;
    return OTA_OK;
}

static int flush_page(ota_writer_t *w) {
    const ota_flash_t *f = w->ctx->flash;
    while (w->erased_end <= w->page_addr + w->page_fill - 1u) {
        int rc = f->erase(f->ctx, w->erased_end);
        if (rc) return rc;
        w->erased_end += f->erase_size;
    }
    int rc = f->program(f->ctx, w->page_addr, w->page, w->page_fill);
    if (rc) return rc;
    w->page_addr += f->page_size;
    w->page_fill = 0;
    return OTA_OK;
}

static int check_manifest(ota_writer_t *w) {
    ota_ctx_t *ctx = w->ctx;
    ota_policy_t pol;
    policy_for(ctx, &ctx->meta, w->kind, &pol);
    pol.max_payload_size = w->cap - OTA_MANIFEST_SIZE;
    int rc = ota_manifest_check(w->man_raw, ctx->pubkey, &pol, &w->manifest);
    if (rc) return rc;
    w->total = OTA_MANIFEST_SIZE + w->manifest.payload_size;
    return OTA_OK;
}

int ota_writer_write(ota_writer_t *w, const uint8_t *data, uint32_t len) {
    if (!w) return OTA_ERR_PARAM;
    if (w->state != OTA_W_ACTIVE) return w->state == OTA_W_FAILED ? w->err : OTA_ERR_STATE;
    if (!data && len) return wfail(w, OTA_ERR_PARAM);
    const uint32_t page = w->ctx->flash->page_size;
    while (len) {
        uint32_t lim;
        if (w->received < OTA_MANIFEST_SIZE) {
            lim = OTA_MANIFEST_SIZE - w->received;
        } else {
            if (w->received >= w->total) return wfail(w, OTA_ERR_OVERFLOW);
            lim = w->total - w->received;
        }
        uint32_t n = len;
        if (n > lim) n = lim;
        if (n > page - w->page_fill) n = page - w->page_fill;
        memcpy(w->page + w->page_fill, data, n);
        if (w->received < OTA_MANIFEST_SIZE) {
            memcpy(w->man_raw + w->received, data, n);
        } else {
            ota_sha256_update(&w->sha, data, n);
        }
        w->received += n;
        w->page_fill += n;
        data += n;
        len -= n;
        if (w->received == OTA_MANIFEST_SIZE && w->total == 0) {
            int rc = check_manifest(w);
            if (rc) return wfail(w, rc);
        }
        if (w->page_fill == page) {
            int rc = flush_page(w);
            if (rc) return wfail(w, rc);
        }
    }
    return OTA_OK;
}

int ota_writer_finish(ota_writer_t *w) {
    if (!w) return OTA_ERR_PARAM;
    if (w->state != OTA_W_ACTIVE) return w->state == OTA_W_FAILED ? w->err : OTA_ERR_STATE;
    if (w->total == 0 || w->received < w->total) return wfail(w, OTA_ERR_TRUNCATED);
    if (w->page_fill) {
        int rc = flush_page(w);
        if (rc) return wfail(w, rc);
    }
    uint8_t digest[32];
    ota_sha256_final(&w->sha, digest);
    if (!ota_ct_equal(digest, w->manifest.payload_sha256, 32)) return wfail(w, OTA_ERR_HASH);

    /* Read back and re-verify everything from flash before marking PENDING. */
    ota_ctx_t *ctx = w->ctx;
    ota_policy_t pol;
    policy_for(ctx, &ctx->meta, w->kind, &pol);
    int rc = ota_slot_verify(ctx->flash, w->base, w->cap, ctx->pubkey, &pol, NULL);
    if (rc) return wfail(w, rc);

    ota_meta_t m = ctx->meta;
    group_t g = group_of(&m, ctx->layout, w->kind);
    if (g.state[w->slot] != OTA_SLOT_EMPTY || *g.active == w->slot) return wfail(w, OTA_ERR_STATE);
    g.state[w->slot] = OTA_SLOT_PENDING;
    rc = commit(ctx, &m);
    if (rc) return wfail(w, rc);
    w->state = OTA_W_DONE;
    return OTA_OK;
}

void ota_writer_abort(ota_writer_t *w) {
    if (!w) return;
    if (w->state == OTA_W_ACTIVE) {
        w->state = OTA_W_FAILED;
        w->err = OTA_ERR_STATE;
    }
}
