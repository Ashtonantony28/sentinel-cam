/* Minimal test harness for the OTA host tests. */
#ifndef OTA_TEST_H
#define OTA_TEST_H
#include <stdio.h>
#include <string.h>

extern int tf_failures;
extern int tf_checks;

#define CHECK(c)                                                                     \
    do {                                                                             \
        tf_checks++;                                                                 \
        if (!(c)) {                                                                  \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #c);    \
            tf_failures++;                                                           \
        }                                                                            \
    } while (0)

#define CHECK_RC(expr, want)                                                                         \
    do {                                                                                             \
        tf_checks++;                                                                                 \
        int rc_ = (expr);                                                                            \
        if (rc_ != (want)) {                                                                         \
            fprintf(stderr, "%s:%d: %s returned %s (%d), expected %s\n", __FILE__, __LINE__, #expr, \
                    ota_err_str(rc_), rc_, ota_err_str(want));                                       \
            tf_failures++;                                                                           \
        }                                                                                            \
    } while (0)

typedef struct {
    const char *name;
    void (*fn)(void);
} tf_case_t;

/* Runs the case named argv[1] (or all). Returns process exit code. */
int tf_main(int argc, char **argv, const tf_case_t *cases, int n);
#endif
