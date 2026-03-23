/*
 * General Chaos — M68K Interpreter Fallback + Unified Call Dispatch
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

/*
 * Unified function dispatch: tries recompiled code first, then interpreter.
 *
 * ALL function calls in the recompiled code use this instead of
 * func_table_call() directly. This ensures indirect calls (JSR (An),
 * JMP via jump tables, etc.) always have a fallback path.
 */
void genchaos_call(uint32_t addr);

#endif /* GENCHAOS_INTERP_H */
