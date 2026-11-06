/* Minimal string.h for the freestanding cross-compile check: only what the
 * core is allowed to use. A call to anything else fails the build. */
#ifndef SN_SHIM_STRING_H
#define SN_SHIM_STRING_H
#include <stddef.h>
void *memcpy(void *dst, const void *src, size_t n);
void *memset(void *dst, int c, size_t n);
int   memcmp(const void *a, const void *b, size_t n);
#endif
