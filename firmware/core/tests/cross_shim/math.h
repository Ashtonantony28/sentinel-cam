/* Minimal math.h for the freestanding cross-compile check. */
#ifndef SN_SHIM_MATH_H
#define SN_SHIM_MATH_H
double sqrt(double x);
double floor(double x);
double fabs(double x);
float  roundf(float x);
#define isnan(x)    __builtin_isnan(x)
#define isfinite(x) __builtin_isfinite(x)
#endif
