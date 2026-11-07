/* head_eval: integration hook for fl/ (tests/integration).
 *   head_eval HEAD.bin FEATURES.bin
 * HEAD.bin: 65 LE fp32 (INTERFACES 5). FEATURES.bin: n x 64 LE fp32.
 * Prints one probability per row ("%.17g"). Exit 1 if the head is rejected. */
#include <stdio.h>
#include <string.h>
#include "sn_head.h"

static long read_all(const char *path, unsigned char *buf, size_t cap)
{
    FILE *f = fopen(path, "rb");
    size_t n;
    if (f == NULL) {
        return -1;
    }
    n = fread(buf, 1u, cap, f);
    (void)fclose(f);
    return (long)n;
}

int main(int argc, char **argv)
{
    static unsigned char head[SN_HEAD_BYTES + 1u];
    static unsigned char feat[4u * SN_HEAD_DIM * 4096u];
    sn_head_t h;
    long hn;
    long fn;
    long r;
    if (argc != 3) {
        fprintf(stderr, "usage: head_eval HEAD.bin FEATURES.bin\n");
        return 2;
    }
    hn = read_all(argv[1], head, sizeof(head));
    fn = read_all(argv[2], feat, sizeof(feat));
    if ((hn < 0) || (fn < 0) || ((fn % (long)(4u * SN_HEAD_DIM)) != 0)) {
        fprintf(stderr, "bad input files\n");
        return 2;
    }
    if (!sn_head_load(&h, head, (size_t)hn)) {
        printf("REJECT\n");
        return 1;
    }
    for (r = 0; r < fn / (long)(4u * SN_HEAD_DIM); r++) {
        float x[SN_HEAD_DIM];
        uint32_t i;
        for (i = 0u; i < SN_HEAD_DIM; i++) {
            const unsigned char *p = &feat[((size_t)r * SN_HEAD_DIM + i) * 4u];
            uint32_t u = (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
                         ((uint32_t)p[3] << 24);
            memcpy(&x[i], &u, sizeof(u));
        }
        printf("%.17g\n", sn_head_prob(&h, x));
    }
    return 0;
}
