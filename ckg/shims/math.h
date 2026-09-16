#ifndef CKG_SHIM_MATH_H
#define CKG_SHIM_MATH_H

/* Declaration-only stand-ins for the small math surface the FOC code uses. */
double sin(double x);
double cos(double x);
double tan(double x);
double asin(double x);
double acos(double x);
double atan(double x);
double atan2(double y, double x);
double sqrt(double x);
double fabs(double x);
double floor(double x);
double ceil(double x);
double fmod(double x, double y);
double pow(double x, double y);
double exp(double x);
double log(double x);

float sinf(float x);
float cosf(float x);
float tanf(float x);
float asinf(float x);
float acosf(float x);
float atanf(float x);
float atan2f(float y, float x);
float sqrtf(float x);
float fabsf(float x);
float floorf(float x);
float ceilf(float x);
float fmodf(float x, float y);
float powf(float x, float y);
float expf(float x);
float logf(float x);

#define M_PI 3.14159265358979323846
#define M_PI_2 1.57079632679489661923
#define M_2_PI 0.63661977236758134308
#define M_SQRT2 1.41421356237309504880

#endif /* CKG_SHIM_MATH_H */
