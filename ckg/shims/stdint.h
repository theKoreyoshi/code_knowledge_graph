#ifndef CKG_SHIM_STDINT_H
#define CKG_SHIM_STDINT_H

/*
 * Minimal freestanding stand-in for <stdint.h>.
 *
 * The analysed project is an embedded (IAR) build; the host toolchain used for
 * knowledge-graph extraction has no matching system headers.  These shim
 * headers only need to be accurate enough for clang to build a correct AST:
 * the sizes below match the IAR ARM (32-bit, ILP32) and x86-64 LP64 models for
 * every type the FOC sources actually use.
 */

typedef signed char int8_t;
typedef unsigned char uint8_t;
typedef short int16_t;
typedef unsigned short uint16_t;
typedef int int32_t;
typedef unsigned int uint32_t;
typedef long long int64_t;
typedef unsigned long long uint64_t;

typedef signed char int_least8_t;
typedef unsigned char uint_least8_t;
typedef short int_least16_t;
typedef unsigned short uint_least16_t;
typedef int int_least32_t;
typedef unsigned int uint_least32_t;
typedef long long int_least64_t;
typedef unsigned long long uint_least64_t;

typedef signed char int_fast8_t;
typedef unsigned char uint_fast8_t;
typedef int int_fast16_t;
typedef unsigned int uint_fast16_t;
typedef int int_fast32_t;
typedef unsigned int uint_fast32_t;
typedef long long int_fast64_t;
typedef unsigned long long uint_fast64_t;

typedef int intptr_t;
typedef unsigned int uintptr_t;
typedef long long intmax_t;
typedef unsigned long long uintmax_t;

#define INT8_MIN (-128)
#define INT8_MAX 127
#define UINT8_MAX 255U
#define INT16_MIN (-32767 - 1)
#define INT16_MAX 32767
#define UINT16_MAX 65535U
#define INT32_MIN (-2147483647 - 1)
#define INT32_MAX 2147483647
#define UINT32_MAX 4294967295U
#define INT64_MIN (-9223372036854775807LL - 1)
#define INT64_MAX 9223372036854775807LL
#define UINT64_MAX 18446744073709551615ULL

#define INT8_C(v) v
#define UINT8_C(v) v##U
#define INT16_C(v) v
#define UINT16_C(v) v##U
#define INT32_C(v) v
#define UINT32_C(v) v##U
#define INT64_C(v) v##LL
#define UINT64_C(v) v##ULL

#endif /* CKG_SHIM_STDINT_H */
