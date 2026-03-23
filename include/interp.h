/*
 * General Chaos — M68K Interpreter Fallback
 *
 * Lightweight interpreter for code paths not discovered by static analysis.
 * Uses the same g_m68k context and bus functions as recompiled code.
 */
#ifndef GENCHAOS_INTERP_H
#define GENCHAOS_INTERP_H

#include <stdbool.h>
#include <stdint.h>

/* Execute M68K code at addr until RTS/RTE or instruction limit.
 * Returns true if the code returned normally (RTS/RTE). */
bool interp_execute(uint32_t addr);

#endif /* GENCHAOS_INTERP_H */
