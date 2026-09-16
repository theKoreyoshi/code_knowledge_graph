#ifndef CKG_SHIM_STDDEF_H
#define CKG_SHIM_STDDEF_H

typedef unsigned int size_t;
typedef int ptrdiff_t;
typedef int wchar_t;

#ifndef NULL
#define NULL ((void *)0)
#endif

#define offsetof(type, member) __builtin_offsetof(type, member)

#endif /* CKG_SHIM_STDDEF_H */
