/* glibc < 2.38 compatibility shim for pre-built ONNX Runtime.
 * ort-sys ships binaries linked against glibc 2.38+ which reference
 * __isoc23_strtol / __isoc23_strtoll / __isoc23_strtoull.
 * On older glibc (e.g. Ubuntu 22.04 with glibc 2.35), these symbols
 * are missing. We provide thin wrappers that delegate to the standard
 * C99 functions.
 */
#include <stdlib.h>

long __isoc23_strtol(const char *nptr, char **endptr, int base) {
    return strtol(nptr, endptr, base);
}

long long __isoc23_strtoll(const char *nptr, char **endptr, int base) {
    return strtoll(nptr, endptr, base);
}

unsigned long long __isoc23_strtoull(const char *nptr, char **endptr, int base) {
    return strtoull(nptr, endptr, base);
}
