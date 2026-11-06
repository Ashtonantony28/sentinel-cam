/*
 * sn_test.h - minimal single-header unit test framework (host only).
 *   static void my_case(void) { CHECK(x); CHECK_EQ_INT(a, b); }
 *   int main(int argc, char **argv) { TEST_BEGIN(argc, argv);
 *       RUN_TEST(my_case); TEST_END(); }
 * With an argument, only the named case runs (ctest registers one test per
 * RUN_TEST line). Exit status is non-zero if any check failed.
 */
#ifndef SN_TEST_H
#define SN_TEST_H
#include <stdio.h>
#include <string.h>
#include <math.h>

static int         sn_t_failed_cases;
static int         sn_t_cases;
static int         sn_t_case_failed;
static long        sn_t_checks;
static const char *sn_t_filter;
static int         sn_t_matched;

#define SN_T_FAIL_HDR() (sn_t_case_failed = 1, printf("  %s:%d: ", __FILE__, __LINE__))

#define CHECK(cond) do { sn_t_checks++; if (!(cond)) { \
    SN_T_FAIL_HDR(); printf("CHECK(%s)\n", #cond); } } while (0)

#define CHECK_EQ_INT(a, b) do { long long sn_a_ = (long long)(a), sn_b_ = (long long)(b); \
    sn_t_checks++; if (sn_a_ != sn_b_) { SN_T_FAIL_HDR(); \
    printf("%s == %s (%lld vs %lld)\n", #a, #b, sn_a_, sn_b_); } } while (0)

#define CHECK_NEAR(a, b, tol) do { double sn_a_ = (double)(a), sn_b_ = (double)(b); \
    sn_t_checks++; if (!(fabs(sn_a_ - sn_b_) <= (double)(tol))) { SN_T_FAIL_HDR(); \
    printf("%s ~= %s (%.9g vs %.9g, tol %g)\n", #a, #b, sn_a_, sn_b_, (double)(tol)); } } while (0)

#define CHECK_STR(a, b) do { const char *sn_a_ = (a), *sn_b_ = (b); sn_t_checks++; \
    if ((sn_a_ == NULL) || (sn_b_ == NULL) || (strcmp(sn_a_, sn_b_) != 0)) { SN_T_FAIL_HDR(); \
    printf("%s == %s\n    got:  %s\n    want: %s\n", #a, #b, sn_a_ ? sn_a_ : "(null)", \
    sn_b_ ? sn_b_ : "(null)"); } } while (0)

#define TEST_BEGIN(argc, argv) do { sn_t_filter = ((argc) > 1) ? (argv)[1] : NULL; } while (0)

#define RUN_TEST(fn) do { if ((sn_t_filter == NULL) || (strcmp(sn_t_filter, #fn) == 0)) { \
    sn_t_matched++; sn_t_case_failed = 0; sn_t_cases++; fn(); \
    if (sn_t_case_failed) { sn_t_failed_cases++; printf("FAIL %s\n", #fn); } \
    else { printf("ok   %s\n", #fn); } } } while (0)

#define TEST_END() do { \
    if ((sn_t_filter != NULL) && (sn_t_matched == 0)) { \
        printf("no test named %s\n", sn_t_filter); return 2; } \
    printf("%d cases, %ld checks, %d failed\n", sn_t_cases, sn_t_checks, sn_t_failed_cases); \
    return (sn_t_failed_cases == 0) ? 0 : 1; } while (0)

#endif
