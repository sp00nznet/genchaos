/*
 * General Chaos — Lightweight M68K Interpreter Fallback
 * ======================================================
 * When the recompiled function table misses (indirect calls to addresses
 * we didn't statically discover), this interpreter executes the M68K
 * code at that address using the ROM data via bus_read.
 *
 * This is NOT a full M68K emulator — it handles the most common
 * instruction patterns found in Genesis games to survive short code
 * sequences until an RTS returns control to recompiled code.
 *
 * The interpreter uses the same g_m68k context and bus functions as
 * the recompiled code, so state is fully shared.
 */

#include <genrecomp/genrecomp.h>
#include "genchaos.h"
#include "interp.h"
#include <stdio.h>
#include <stdbool.h>

/* Maximum instructions to interpret before bailing out */
#define INTERP_MAX_INSNS 200000

/* Logging control */
static int s_interp_calls = 0;
static int s_interp_logged = 0;
#define INTERP_MAX_LOG 20

/* ====================================================================
 * Unified call dispatch — the backbone of runtime execution
 *
 * Every function call in the recompiled code goes through here.
 * Tries the recompiled function table first (fast path), and if the
 * address isn't found, falls back to the interpreter.
 * ==================================================================== */
void genchaos_call(uint32_t addr) {
    if (func_table_call(addr)) return;
    interp_execute(addr);
}

/* Sign extension helpers */
static int32_t sext8(uint8_t v) { return (int32_t)(int8_t)v; }
static int32_t sext16(uint16_t v) { return (int32_t)(int16_t)v; }

/* Read instruction words from the bus */
static uint16_t fetch16(uint32_t *pc) {
    uint16_t w = bus_read16(*pc);
    *pc += 2;
    return w;
}

static uint32_t fetch32(uint32_t *pc) {
    uint32_t hi = bus_read16(*pc); *pc += 2;
    uint32_t lo = bus_read16(*pc); *pc += 2;
    return (hi << 16) | lo;
}

/*
 * Resolve an effective address for reading.
 * Returns the value and advances PC past extension words.
 * For address-only modes (LEA), use resolve_ea_addr instead.
 */
static uint32_t read_ea(uint32_t *pc, int mode, int reg, int size) {
    int bytes = (size == 0) ? 1 : (size == 1) ? 2 : 4;

    switch (mode) {
    case 0: /* Dn */
        if (size == 0) return (uint8_t)g_m68k.d[reg];
        if (size == 1) return (uint16_t)g_m68k.d[reg];
        return g_m68k.d[reg];

    case 1: /* An */
        if (size == 1) return (uint16_t)g_m68k.a[reg];
        return g_m68k.a[reg];

    case 2: /* (An) */
        if (size == 0) return bus_read8(g_m68k.a[reg]);
        if (size == 1) return bus_read16(g_m68k.a[reg]);
        return bus_read32(g_m68k.a[reg]);

    case 3: /* (An)+ */ {
        uint32_t addr = g_m68k.a[reg];
        uint32_t val;
        if (size == 0) { val = bus_read8(addr); g_m68k.a[reg] += (reg == 7) ? 2 : 1; }
        else if (size == 1) { val = bus_read16(addr); g_m68k.a[reg] += 2; }
        else { val = bus_read32(addr); g_m68k.a[reg] += 4; }
        return val;
    }

    case 4: /* -(An) */ {
        int dec = (size == 0 && reg != 7) ? 1 : (size == 1) ? 2 : (size == 0 && reg == 7) ? 2 : 4;
        g_m68k.a[reg] -= dec;
        if (size == 0) return bus_read8(g_m68k.a[reg]);
        if (size == 1) return bus_read16(g_m68k.a[reg]);
        return bus_read32(g_m68k.a[reg]);
    }

    case 5: /* d16(An) */ {
        int16_t disp = (int16_t)fetch16(pc);
        uint32_t addr = g_m68k.a[reg] + disp;
        if (size == 0) return bus_read8(addr);
        if (size == 1) return bus_read16(addr);
        return bus_read32(addr);
    }

    case 6: /* d8(An,Xn) */ {
        uint16_t ext = fetch16(pc);
        int8_t disp = (int8_t)(ext & 0xFF);
        int xreg = (ext >> 12) & 7;
        int32_t xval = (ext & 0x8000) ? (int32_t)g_m68k.a[xreg] : (int32_t)g_m68k.d[xreg];
        if (!(ext & 0x0800)) xval = (int32_t)(int16_t)(uint16_t)xval;
        uint32_t addr = g_m68k.a[reg] + xval + disp;
        if (size == 0) return bus_read8(addr);
        if (size == 1) return bus_read16(addr);
        return bus_read32(addr);
    }

    case 7:
        switch (reg) {
        case 0: /* xxx.W */ {
            uint32_t addr = (uint32_t)(int32_t)(int16_t)fetch16(pc);
            if (size == 0) return bus_read8(addr);
            if (size == 1) return bus_read16(addr);
            return bus_read32(addr);
        }
        case 1: /* xxx.L */ {
            uint32_t addr = fetch32(pc);
            if (size == 0) return bus_read8(addr);
            if (size == 1) return bus_read16(addr);
            return bus_read32(addr);
        }
        case 2: /* d16(PC) */ {
            uint32_t base = *pc;
            int16_t disp = (int16_t)fetch16(pc);
            uint32_t addr = base + disp;
            if (size == 0) return bus_read8(addr);
            if (size == 1) return bus_read16(addr);
            return bus_read32(addr);
        }
        case 3: /* d8(PC,Xn) */ {
            uint32_t base = *pc;
            uint16_t ext = fetch16(pc);
            int8_t disp = (int8_t)(ext & 0xFF);
            int xreg = (ext >> 12) & 7;
            int32_t xval = (ext & 0x8000) ? (int32_t)g_m68k.a[xreg] : (int32_t)g_m68k.d[xreg];
            if (!(ext & 0x0800)) xval = (int32_t)(int16_t)(uint16_t)xval;
            uint32_t addr = base + xval + disp;
            if (size == 0) return bus_read8(addr);
            if (size == 1) return bus_read16(addr);
            return bus_read32(addr);
        }
        case 4: /* #imm */
            if (size == 0) return fetch16(pc) & 0xFF;
            if (size == 1) return fetch16(pc);
            return fetch32(pc);
        }
        break;
    }
    return 0;
}

/* Write a value to an effective address */
static void write_ea(uint32_t *pc, int mode, int reg, int size, uint32_t val) {
    switch (mode) {
    case 0: /* Dn */
        if (size == 0) g_m68k.d[reg] = (g_m68k.d[reg] & 0xFFFFFF00) | (val & 0xFF);
        else if (size == 1) g_m68k.d[reg] = (g_m68k.d[reg] & 0xFFFF0000) | (val & 0xFFFF);
        else g_m68k.d[reg] = val;
        return;
    case 1: /* An */
        g_m68k.a[reg] = val;
        return;
    case 2: /* (An) */
        if (size == 0) bus_write8(g_m68k.a[reg], (uint8_t)val);
        else if (size == 1) bus_write16(g_m68k.a[reg], (uint16_t)val);
        else bus_write32(g_m68k.a[reg], val);
        return;
    case 3: /* (An)+ */ {
        uint32_t addr = g_m68k.a[reg];
        if (size == 0) { bus_write8(addr, (uint8_t)val); g_m68k.a[reg] += (reg == 7) ? 2 : 1; }
        else if (size == 1) { bus_write16(addr, (uint16_t)val); g_m68k.a[reg] += 2; }
        else { bus_write32(addr, val); g_m68k.a[reg] += 4; }
        return;
    }
    case 4: /* -(An) */ {
        int dec = (size == 0 && reg != 7) ? 1 : (size == 1) ? 2 : (size == 0 && reg == 7) ? 2 : 4;
        g_m68k.a[reg] -= dec;
        if (size == 0) bus_write8(g_m68k.a[reg], (uint8_t)val);
        else if (size == 1) bus_write16(g_m68k.a[reg], (uint16_t)val);
        else bus_write32(g_m68k.a[reg], val);
        return;
    }
    case 5: /* d16(An) */ {
        int16_t disp = (int16_t)fetch16(pc);
        uint32_t addr = g_m68k.a[reg] + disp;
        if (size == 0) bus_write8(addr, (uint8_t)val);
        else if (size == 1) bus_write16(addr, (uint16_t)val);
        else bus_write32(addr, val);
        return;
    }
    case 6: /* d8(An,Xn) */ {
        uint16_t ext = fetch16(pc);
        int8_t disp = (int8_t)(ext & 0xFF);
        int xreg = (ext >> 12) & 7;
        int32_t xval = (ext & 0x8000) ? (int32_t)g_m68k.a[xreg] : (int32_t)g_m68k.d[xreg];
        if (!(ext & 0x0800)) xval = (int32_t)(int16_t)(uint16_t)xval;
        uint32_t addr = g_m68k.a[reg] + xval + disp;
        if (size == 0) bus_write8(addr, (uint8_t)val);
        else if (size == 1) bus_write16(addr, (uint16_t)val);
        else bus_write32(addr, val);
        return;
    }
    case 7:
        if (reg == 0) {
            uint32_t addr = (uint32_t)(int32_t)(int16_t)fetch16(pc);
            if (size == 0) bus_write8(addr, (uint8_t)val);
            else if (size == 1) bus_write16(addr, (uint16_t)val);
            else bus_write32(addr, val);
        } else if (reg == 1) {
            uint32_t addr = fetch32(pc);
            if (size == 0) bus_write8(addr, (uint8_t)val);
            else if (size == 1) bus_write16(addr, (uint16_t)val);
            else bus_write32(addr, val);
        }
        return;
    }
}

/* Resolve EA to an address (for LEA, PEA, JMP, JSR) */
static uint32_t resolve_ea_addr(uint32_t *pc, int mode, int reg) {
    switch (mode) {
    case 2: return g_m68k.a[reg];
    case 5: { int16_t d = (int16_t)fetch16(pc); return g_m68k.a[reg] + d; }
    case 6: {
        uint16_t ext = fetch16(pc);
        int8_t disp = (int8_t)(ext & 0xFF);
        int xreg = (ext >> 12) & 7;
        int32_t xval = (ext & 0x8000) ? (int32_t)g_m68k.a[xreg] : (int32_t)g_m68k.d[xreg];
        if (!(ext & 0x0800)) xval = (int32_t)(int16_t)(uint16_t)xval;
        return g_m68k.a[reg] + xval + disp;
    }
    case 7:
        if (reg == 0) return (uint32_t)(int32_t)(int16_t)fetch16(pc);
        if (reg == 1) return fetch32(pc);
        if (reg == 2) { uint32_t b = *pc; return b + (int16_t)fetch16(pc); }
        if (reg == 3) {
            uint32_t b = *pc;
            uint16_t ext = fetch16(pc);
            int8_t disp = (int8_t)(ext & 0xFF);
            int xreg = (ext >> 12) & 7;
            int32_t xval = (ext & 0x8000) ? (int32_t)g_m68k.a[xreg] : (int32_t)g_m68k.d[xreg];
            if (!(ext & 0x0800)) xval = (int32_t)(int16_t)(uint16_t)xval;
            return b + xval + disp;
        }
        break;
    }
    return 0;
}

/* Test a condition code */
static bool test_cc(int cc) {
    switch (cc) {
    case 0: return true;
    case 1: return false;
    case 2: return !g_m68k.flag_C && !g_m68k.flag_Z;
    case 3: return g_m68k.flag_C || g_m68k.flag_Z;
    case 4: return !g_m68k.flag_C;
    case 5: return g_m68k.flag_C;
    case 6: return !g_m68k.flag_Z;
    case 7: return g_m68k.flag_Z;
    case 8: return !g_m68k.flag_V;
    case 9: return g_m68k.flag_V;
    case 10: return !g_m68k.flag_N;
    case 11: return g_m68k.flag_N;
    case 12: return g_m68k.flag_N == g_m68k.flag_V;
    case 13: return g_m68k.flag_N != g_m68k.flag_V;
    case 14: return !g_m68k.flag_Z && (g_m68k.flag_N == g_m68k.flag_V);
    case 15: return g_m68k.flag_Z || (g_m68k.flag_N != g_m68k.flag_V);
    }
    return false;
}

/*
 * Interpret M68K code starting at 'addr' until RTS/RTE or max instructions.
 * Returns true if the function completed successfully.
 */
bool interp_execute(uint32_t addr) {
    uint32_t pc = addr & 0xFFFFFF;
    int insn_count = 0;

    s_interp_calls++;
    if (s_interp_logged < INTERP_MAX_LOG) {
        fprintf(stderr, "  INTERP: executing at $%06X (call #%d)\n",
                addr, s_interp_calls);
        s_interp_logged++;
    }

    while (insn_count < INTERP_MAX_INSNS) {
        uint16_t op = fetch16(&pc);
        int group = (op >> 12) & 0xF;
        insn_count++;

        g_m68k.pc = pc;


        /* ---- NOP ---- */
        if (op == 0x4E71) continue;

        /* ---- RTS ---- */
        if (op == 0x4E75) {
            g_m68k.pc = bus_read32(g_m68k.a[7]);
            g_m68k.a[7] += 4;
            return true;
        }

        /* ---- RTE ---- */
        if (op == 0x4E73) {
            /* Just return — the exception frame is popped by the caller
             * (recomp_m68k_exception handles the SR+PC pop). */
            return true;
        }

        /* ---- MOVEQ ---- */
        if (group == 7) {
            int dreg = (op >> 9) & 7;
            int8_t data = (int8_t)(op & 0xFF);
            g_m68k.d[dreg] = (uint32_t)(int32_t)data;
            M68K_TST32(g_m68k.d[dreg]);
            continue;
        }

        /* ---- BRA/BSR/Bcc ---- */
        if (group == 6) {
            int cc = (op >> 8) & 0xF;
            int disp8 = op & 0xFF;
            int32_t disp;
            if (disp8 == 0) disp = sext16(fetch16(&pc));
            else if (disp8 == 0xFF) disp = (int32_t)fetch32(&pc);
            else disp = sext8((uint8_t)disp8);

            uint32_t target = (pc - (disp8 == 0 ? 2 : disp8 == 0xFF ? 4 : 0) + disp) & 0xFFFFFF;

            if (cc == 0) { /* BRA */
                if (target == pc - (disp8 == 0 ? 4 : disp8 == (int)0xFF ? 6 : 2)) {
                    /* BRA $self — spin-wait for interrupt */
                    /* Tick cycles until VBlank fires via bus callback */
                    while (1) { bus_read16(0xC00004); }
                }
                pc = target;
            } else if (cc == 1) { /* BSR */
                g_m68k.a[7] -= 4;
                bus_write32(g_m68k.a[7], pc);
                /* Try recompiled first, fall back to interpret */
                if (!func_table_call(target)) {
                    if (!interp_execute(target)) return false;
                }
            } else {
                if (test_cc(cc)) {
                    /* Detect tight backward loops (like VDP status polling).
                     * If the same Bcc branches back >1000 times, force exit.
                     * The VDP backend doesn't update status register bits on
                     * cycle advancement, so these loops never terminate. */
                    static uint32_t loop_target = 0;
                    static int loop_count = 0;
                    if (target < pc && (pc - target) < 32) {
                        if (target == loop_target) {
                            loop_count++;
                            if (loop_count > 1000) {
                                /* Force loop exit — skip the branch */
                                loop_count = 0;
                                loop_target = 0;
                                continue; /* don't take branch */
                            }
                        } else {
                            loop_target = target;
                            loop_count = 1;
                        }
                    } else {
                        loop_count = 0;
                    }
                    pc = target;
                }
            }
            continue;
        }

        /* ---- MOVE.B (group 1) / MOVE.W (group 3) / MOVE.L (group 2) ---- */
        if (group == 1 || group == 2 || group == 3) {
            int size = (group == 1) ? 0 : (group == 3) ? 1 : 2;
            int src_mode = (op >> 3) & 7;
            int src_reg = op & 7;
            int dst_reg = (op >> 9) & 7;
            int dst_mode = (op >> 6) & 7;

            uint32_t val = read_ea(&pc, src_mode, src_reg, size);

            if (dst_mode == 1) {
                /* MOVEA — no flags */
                if (size == 1) g_m68k.a[dst_reg] = (uint32_t)(int32_t)(int16_t)(uint16_t)val;
                else g_m68k.a[dst_reg] = val;
            } else {
                write_ea(&pc, dst_mode, dst_reg, size, val);
                /* Update N, Z; clear C, V */
                if (size == 0) { M68K_TST8((uint8_t)val); }
                else if (size == 1) { M68K_TST16((uint16_t)val); }
                else { M68K_TST32(val); }
            }
            continue;
        }

        /* ---- LEA ---- */
        if ((op & 0xF1C0) == 0x41C0) {
            int areg = (op >> 9) & 7;
            int ea_mode = (op >> 3) & 7;
            int ea_reg = op & 7;
            g_m68k.a[areg] = resolve_ea_addr(&pc, ea_mode, ea_reg);
            continue;
        }

        /* ---- JSR ---- */
        if ((op & 0xFFC0) == 0x4E80) {
            int ea_mode = (op >> 3) & 7;
            int ea_reg = op & 7;
            uint32_t target = resolve_ea_addr(&pc, ea_mode, ea_reg);
            g_m68k.a[7] -= 4;
            bus_write32(g_m68k.a[7], pc);
            if (!func_table_call(target)) {
                if (!interp_execute(target)) return false;
            }
            continue;
        }

        /* ---- JMP ---- */
        if ((op & 0xFFC0) == 0x4EC0) {
            int ea_mode = (op >> 3) & 7;
            int ea_reg = op & 7;
            uint32_t target = resolve_ea_addr(&pc, ea_mode, ea_reg);
            /* Try recompiled version; if found, tail-call it and return */
            if (func_table_call(target)) return true;
            /* Otherwise continue interpreting at the target */
            pc = target;
            continue;
        }

        /* ---- CLR ---- */
        if ((op & 0xFF00) == 0x4200) {
            int size = (op >> 6) & 3;
            int ea_mode = (op >> 3) & 7;
            int ea_reg = op & 7;
            if (size < 3) {
                write_ea(&pc, ea_mode, ea_reg, size, 0);
                g_m68k.flag_N = false; g_m68k.flag_Z = true;
                g_m68k.flag_V = false; g_m68k.flag_C = false;
            }
            continue;
        }

        /* ---- TST ---- */
        if ((op & 0xFF00) == 0x4A00) {
            int size = (op >> 6) & 3;
            int ea_mode = (op >> 3) & 7;
            int ea_reg = op & 7;
            if (size < 3) {
                uint32_t val = read_ea(&pc, ea_mode, ea_reg, size);
                if (size == 0) M68K_TST8((uint8_t)val);
                else if (size == 1) M68K_TST16((uint16_t)val);
                else M68K_TST32(val);
            }
            continue;
        }

        /* ---- ADDQ/SUBQ ---- */
        if (group == 5) {
            int size = (op >> 6) & 3;
            if (size < 3) {
                int data = (op >> 9) & 7;
                if (data == 0) data = 8;
                int ea_mode = (op >> 3) & 7;
                int ea_reg = op & 7;
                bool is_sub = (op & 0x0100) != 0;

                if (ea_mode == 1) {
                    if (is_sub) g_m68k.a[ea_reg] -= data;
                    else g_m68k.a[ea_reg] += data;
                } else {
                    uint32_t val = read_ea(&pc, ea_mode, ea_reg, size);
                    uint32_t save_pc = pc; /* write_ea may advance pc for complex modes */
                    if (size == 0) {
                        uint8_t tmp = (uint8_t)val;
                        if (is_sub) { M68K_SUB8(tmp, data); }
                        else { M68K_ADD8(tmp, data); }
                        val = tmp;
                    } else if (size == 1) {
                        uint16_t tmp = (uint16_t)val;
                        if (is_sub) { M68K_SUB16(tmp, data); }
                        else { M68K_ADD16(tmp, data); }
                        val = tmp;
                    } else {
                        if (is_sub) { M68K_SUB32(val, data); }
                        else { M68K_ADD32(val, data); }
                    }
                    /* Re-read extension for write (simplified: only handles Dn and direct) */
                    if (ea_mode == 0) {
                        write_ea(&save_pc, ea_mode, ea_reg, size, val);
                    }
                }
            } else {
                /* Scc / DBcc */
                int cc = (op >> 8) & 0xF;
                int ea_mode = (op >> 3) & 7;
                int ea_reg = op & 7;
                if (ea_mode == 1) {
                    /* DBcc */
                    int16_t disp = (int16_t)fetch16(&pc);
                    if (!test_cc(cc)) {
                        int16_t cnt = (int16_t)(uint16_t)g_m68k.d[ea_reg];
                        cnt--;
                        g_m68k.d[ea_reg] = (g_m68k.d[ea_reg] & 0xFFFF0000) | (uint16_t)cnt;
                        if (cnt != -1) {
                            pc = (pc - 2 + disp) & 0xFFFFFF;
                        }
                    }
                } else {
                    /* Scc */
                    uint8_t val = test_cc(cc) ? 0xFF : 0x00;
                    write_ea(&pc, ea_mode, ea_reg, 0, val);
                }
            }
            continue;
        }

        /* ---- MOVE USP ---- */
        if ((op & 0xFFF0) == 0x4E60) {
            int areg = op & 7;
            if (op & 0x0008) {
                g_m68k.a[areg] = g_m68k.usp;  /* MOVE USP, An */
            } else {
                g_m68k.usp = g_m68k.a[areg];  /* MOVE An, USP */
            }
            continue;
        }

        /* ---- BTST/BCHG/BCLR/BSET with register ---- */
        if (group == 0 && (op & 0x0100)) {
            int bit_reg = (op >> 9) & 7;
            int sub = (op >> 6) & 3;
            int ea_mode = (op >> 3) & 7;
            int ea_reg = op & 7;
            if (ea_mode == 1) {
                /* MOVEP — skip for now */
                fetch16(&pc);  /* skip displacement */
                continue;
            }
            int size = (ea_mode == 0) ? 2 : 0;  /* long for Dn, byte for mem */
            uint32_t val = read_ea(&pc, ea_mode, ea_reg, size);
            int bit = g_m68k.d[bit_reg] & ((ea_mode == 0) ? 31 : 7);
            g_m68k.flag_Z = !(val & (1u << bit));
            if (sub == 1) val ^= (1u << bit);       /* BCHG */
            else if (sub == 2) val &= ~(1u << bit); /* BCLR */
            else if (sub == 3) val |= (1u << bit);  /* BSET */
            if (sub > 0 && ea_mode == 0) {
                g_m68k.d[ea_reg] = val;
            }
            /* For memory, would need write-back — BTST doesn't need it */
            continue;
        }

        /* ---- BTST/BCHG/BCLR/BSET with immediate bit ---- */
        if (group == 0 && !((op >> 8) & 1) && ((op >> 9) & 7) == 4) {
            int sub = (op >> 6) & 3;
            int ea_mode = (op >> 3) & 7;
            int ea_reg = op & 7;
            int bit_num = fetch16(&pc) & 0xFF;
            int size = (ea_mode == 0) ? 2 : 0;
            uint32_t val = read_ea(&pc, ea_mode, ea_reg, size);
            int bit = bit_num & ((ea_mode == 0) ? 31 : 7);
            g_m68k.flag_Z = !(val & (1u << bit));
            if (sub == 1) val ^= (1u << bit);
            else if (sub == 2) val &= ~(1u << bit);
            else if (sub == 3) val |= (1u << bit);
            if (sub > 0 && ea_mode == 0) {
                g_m68k.d[ea_reg] = val;
            }
            continue;
        }

        /* ---- NOT ---- */
        if ((op & 0xFF00) == 0x4600) { /* NOT.B/W/L */
            int size = (op >> 6) & 3;
            int mode = (op >> 3) & 7;
            int reg = op & 7;
            if (mode == 0) { /* Data register direct */
                if (size == 0) { /* NOT.B */
                    uint8_t val = ~(uint8_t)g_m68k.d[reg];
                    g_m68k.d[reg] = (g_m68k.d[reg] & 0xFFFFFF00) | val;
                    M68K_TST8(val);
                } else if (size == 1) { /* NOT.W */
                    uint16_t val = ~(uint16_t)g_m68k.d[reg];
                    g_m68k.d[reg] = (g_m68k.d[reg] & 0xFFFF0000) | val;
                    M68K_TST16(val);
                } else { /* NOT.L */
                    g_m68k.d[reg] = ~g_m68k.d[reg];
                    M68K_TST32(g_m68k.d[reg]);
                }
                g_m68k.flag_C = false; g_m68k.flag_V = false;
                continue;
            }
        }

        /* ---- MOVE to/from SR, LINK, UNLK, SWAP, EXT ---- */
        if (op == 0x46FC) { /* MOVE #imm, SR */
            m68k_set_sr(fetch16(&pc));
            continue;
        }
        if ((op & 0xFFC0) == 0x46C0) { /* MOVE <ea>, SR */
            int ea_mode = (op >> 3) & 7;
            int ea_reg = op & 7;
            m68k_set_sr((uint16_t)read_ea(&pc, ea_mode, ea_reg, 1));
            continue;
        }
        if ((op & 0xFFF8) == 0x4E50) { /* LINK */
            int areg = op & 7;
            int16_t disp = (int16_t)fetch16(&pc);
            g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], g_m68k.a[areg]);
            g_m68k.a[areg] = g_m68k.a[7];
            g_m68k.a[7] += disp;
            continue;
        }
        if ((op & 0xFFF8) == 0x4E58) { /* UNLK */
            int areg = op & 7;
            g_m68k.a[7] = g_m68k.a[areg];
            g_m68k.a[areg] = bus_read32(g_m68k.a[7]); g_m68k.a[7] += 4;
            continue;
        }
        if ((op & 0xFFF8) == 0x4840) { /* SWAP */
            int dreg = op & 7;
            g_m68k.d[dreg] = (g_m68k.d[dreg] >> 16) | (g_m68k.d[dreg] << 16);
            M68K_TST32(g_m68k.d[dreg]);
            g_m68k.flag_C = false; g_m68k.flag_V = false;
            continue;
        }
        if ((op & 0xFFF8) == 0x4880) { /* EXT.W */
            int dreg = op & 7;
            g_m68k.d[dreg] = (g_m68k.d[dreg] & 0xFFFF0000) |
                             (uint16_t)(int16_t)(int8_t)(uint8_t)g_m68k.d[dreg];
            M68K_TST16((uint16_t)g_m68k.d[dreg]);
            g_m68k.flag_C = false; g_m68k.flag_V = false;
            continue;
        }
        if ((op & 0xFFF8) == 0x48C0) { /* EXT.L */
            int dreg = op & 7;
            g_m68k.d[dreg] = (uint32_t)(int32_t)(int16_t)(uint16_t)g_m68k.d[dreg];
            M68K_TST32(g_m68k.d[dreg]);
            g_m68k.flag_C = false; g_m68k.flag_V = false;
            continue;
        }

        /* ---- ADD/SUB/CMP/AND/OR with Dn (groups 8,9,B,C,D) ---- */
        if (group == 0xD || group == 9 || group == 0xB ||
            group == 8 || group == 0xC) {
            int opmode = (op >> 6) & 7;
            int dreg = (op >> 9) & 7;
            int ea_mode = (op >> 3) & 7;
            int ea_reg = op & 7;

            /* ADDA/SUBA */
            if ((group == 0xD || group == 9) && (opmode == 3 || opmode == 7)) {
                int size = (opmode == 3) ? 1 : 2;
                uint32_t val = read_ea(&pc, ea_mode, ea_reg, size);
                if (size == 1) val = (uint32_t)(int32_t)(int16_t)(uint16_t)val;
                if (group == 0xD) g_m68k.a[dreg] += val;
                else g_m68k.a[dreg] -= val;
                continue;
            }

            /* CMPA */
            if (group == 0xB && (opmode == 3 || opmode == 7)) {
                int size = (opmode == 3) ? 1 : 2;
                uint32_t val = read_ea(&pc, ea_mode, ea_reg, size);
                if (size == 1) val = (uint32_t)(int32_t)(int16_t)(uint16_t)val;
                M68K_CMP32(g_m68k.a[dreg], val);
                continue;
            }

            /* MULU/MULS */
            if (group == 0xC && opmode == 3) {
                uint16_t val = (uint16_t)read_ea(&pc, ea_mode, ea_reg, 1);
                M68K_MULU(g_m68k.d[dreg], val);
                continue;
            }
            if (group == 0xC && opmode == 7) {
                uint16_t val = (uint16_t)read_ea(&pc, ea_mode, ea_reg, 1);
                M68K_MULS(g_m68k.d[dreg], val);
                continue;
            }

            /* DIVU/DIVS */
            if (group == 8 && opmode == 3) {
                uint16_t val = (uint16_t)read_ea(&pc, ea_mode, ea_reg, 1);
                M68K_DIVU(g_m68k.d[dreg], val);
                continue;
            }
            if (group == 8 && opmode == 7) {
                uint16_t val = (uint16_t)read_ea(&pc, ea_mode, ea_reg, 1);
                M68K_DIVS(g_m68k.d[dreg], val);
                continue;
            }

            /* Dn op <ea> -> <ea> (opmode 4/5/6) — memory destination */
            if (opmode >= 4 && opmode <= 6 && group != 0xB) {
                int size = opmode - 4;
                /* Need to read, operate, then write back to EA */
                /* Save PC so we can re-read the EA for writing */
                uint32_t ea_pc = pc;
                uint32_t dst = read_ea(&pc, ea_mode, ea_reg, size);
                uint32_t src;
                if (size == 0) src = (uint8_t)g_m68k.d[dreg];
                else if (size == 1) src = (uint16_t)g_m68k.d[dreg];
                else src = g_m68k.d[dreg];

                uint32_t result = dst;
                if (size == 0) {
                    uint8_t tmp = (uint8_t)dst;
                    if (group == 0xD) M68K_ADD8(tmp, (uint8_t)src);
                    else if (group == 9) M68K_SUB8(tmp, (uint8_t)src);
                    else if (group == 8) M68K_OR8(tmp, (uint8_t)src);
                    else if (group == 0xC) M68K_AND8(tmp, (uint8_t)src);
                    result = tmp;
                } else if (size == 1) {
                    uint16_t tmp = (uint16_t)dst;
                    if (group == 0xD) M68K_ADD16(tmp, (uint16_t)src);
                    else if (group == 9) M68K_SUB16(tmp, (uint16_t)src);
                    else if (group == 8) M68K_OR16(tmp, (uint16_t)src);
                    else if (group == 0xC) M68K_AND16(tmp, (uint16_t)src);
                    result = tmp;
                } else {
                    if (group == 0xD) M68K_ADD32(result, src);
                    else if (group == 9) M68K_SUB32(result, src);
                    else if (group == 8) M68K_OR32(result, src);
                    else if (group == 0xC) M68K_AND32(result, src);
                }
                /* Write back — re-parse EA from saved position */
                write_ea(&ea_pc, ea_mode, ea_reg, size, result);
                continue;
            }

            /* <ea> op Dn -> Dn (opmode 0/1/2) */
            if (opmode <= 2) {
                int size = opmode;
                uint32_t src = read_ea(&pc, ea_mode, ea_reg, size);
                if (size == 0) {
                    uint8_t tmp = (uint8_t)g_m68k.d[dreg];
                    if (group == 0xD) M68K_ADD8(tmp, (uint8_t)src);
                    else if (group == 9) M68K_SUB8(tmp, (uint8_t)src);
                    else if (group == 0xB) { M68K_CMP8(tmp, (uint8_t)src); continue; }
                    else if (group == 8) M68K_OR8(tmp, (uint8_t)src);
                    else if (group == 0xC) M68K_AND8(tmp, (uint8_t)src);
                    g_m68k.d[dreg] = (g_m68k.d[dreg] & 0xFFFFFF00) | tmp;
                } else if (size == 1) {
                    uint16_t tmp = (uint16_t)g_m68k.d[dreg];
                    if (group == 0xD) M68K_ADD16(tmp, (uint16_t)src);
                    else if (group == 9) M68K_SUB16(tmp, (uint16_t)src);
                    else if (group == 0xB) { M68K_CMP16(tmp, (uint16_t)src); continue; }
                    else if (group == 8) M68K_OR16(tmp, (uint16_t)src);
                    else if (group == 0xC) M68K_AND16(tmp, (uint16_t)src);
                    g_m68k.d[dreg] = (g_m68k.d[dreg] & 0xFFFF0000) | tmp;
                } else {
                    if (group == 0xD) M68K_ADD32(g_m68k.d[dreg], src);
                    else if (group == 9) M68K_SUB32(g_m68k.d[dreg], src);
                    else if (group == 0xB) { M68K_CMP32(g_m68k.d[dreg], src); continue; }
                    else if (group == 8) M68K_OR32(g_m68k.d[dreg], src);
                    else if (group == 0xC) M68K_AND32(g_m68k.d[dreg], src);
                }
                continue;
            }
        }

        /* ---- MOVEM ---- */
        if ((op & 0xFB80) == 0x4880) {
            int direction = (op >> 10) & 1;
            int size = (op & 0x0040) ? 2 : 1;
            int ea_mode = (op >> 3) & 7;
            int ea_reg = op & 7;
            uint16_t mask = fetch16(&pc);
            int nbytes = (size == 2) ? 4 : 2;

            if (direction == 0) { /* reg to mem */
                if (ea_mode == 4) { /* -(An) */
                    uint32_t a = g_m68k.a[ea_reg];
                    for (int i = 15; i >= 0; i--) {
                        if (mask & (1 << (15 - i))) {
                            a -= nbytes;
                            uint32_t v = (i < 8) ? g_m68k.d[i] : g_m68k.a[i - 8];
                            if (size == 2) bus_write32(a, v);
                            else bus_write16(a, (uint16_t)v);
                        }
                    }
                    g_m68k.a[ea_reg] = a;
                } else {
                    uint32_t a = resolve_ea_addr(&pc, ea_mode, ea_reg);
                    for (int i = 0; i < 16; i++) {
                        if (mask & (1 << i)) {
                            uint32_t v = (i < 8) ? g_m68k.d[i] : g_m68k.a[i - 8];
                            if (size == 2) bus_write32(a, v);
                            else bus_write16(a, (uint16_t)v);
                            a += nbytes;
                        }
                    }
                }
            } else { /* mem to reg */
                uint32_t a;
                if (ea_mode == 3) a = g_m68k.a[ea_reg];
                else a = resolve_ea_addr(&pc, ea_mode, ea_reg);
                for (int i = 0; i < 16; i++) {
                    if (mask & (1 << i)) {
                        uint32_t v;
                        if (size == 2) v = bus_read32(a);
                        else v = (uint32_t)(int32_t)(int16_t)bus_read16(a);
                        if (i < 8) g_m68k.d[i] = v;
                        else g_m68k.a[i - 8] = v;
                        a += nbytes;
                    }
                }
                if (ea_mode == 3) g_m68k.a[ea_reg] = a;
            }
            continue;
        }

        /* ---- Immediate ops (group 0) ---- */
        if (group == 0 && !(op & 0x0100)) {
            int sub_op = (op >> 9) & 7;
            int size_field = (op >> 6) & 3;
            if (size_field < 3 && sub_op < 7 && sub_op != 4) {
                int size = size_field;
                uint32_t imm;
                if (size == 0) imm = fetch16(&pc) & 0xFF;
                else if (size == 1) imm = fetch16(&pc);
                else imm = fetch32(&pc);

                int ea_mode = (op >> 3) & 7;
                int ea_reg = op & 7;
                uint32_t val = read_ea(&pc, ea_mode, ea_reg, size);

                if (size == 0) {
                    uint8_t tmp = (uint8_t)val;
                    switch (sub_op) {
                    case 0: M68K_OR8(tmp, (uint8_t)imm); break;
                    case 1: M68K_AND8(tmp, (uint8_t)imm); break;
                    case 2: M68K_SUB8(tmp, (uint8_t)imm); break;
                    case 3: M68K_ADD8(tmp, (uint8_t)imm); break;
                    case 5: M68K_EOR8(tmp, (uint8_t)imm); break;
                    case 6: M68K_CMP8(tmp, (uint8_t)imm); continue;
                    }
                    if (ea_mode == 0) g_m68k.d[ea_reg] = (g_m68k.d[ea_reg] & 0xFFFFFF00) | tmp;
                } else if (size == 1) {
                    uint16_t tmp = (uint16_t)val;
                    switch (sub_op) {
                    case 0: M68K_OR16(tmp, (uint16_t)imm); break;
                    case 1: M68K_AND16(tmp, (uint16_t)imm); break;
                    case 2: M68K_SUB16(tmp, (uint16_t)imm); break;
                    case 3: M68K_ADD16(tmp, (uint16_t)imm); break;
                    case 5: M68K_EOR16(tmp, (uint16_t)imm); break;
                    case 6: M68K_CMP16(tmp, (uint16_t)imm); continue;
                    }
                    if (ea_mode == 0) g_m68k.d[ea_reg] = (g_m68k.d[ea_reg] & 0xFFFF0000) | tmp;
                } else {
                    switch (sub_op) {
                    case 0: M68K_OR32(val, imm); break;
                    case 1: M68K_AND32(val, imm); break;
                    case 2: M68K_SUB32(val, imm); break;
                    case 3: M68K_ADD32(val, imm); break;
                    case 5: M68K_EOR32(val, imm); break;
                    case 6: M68K_CMP32(val, imm); continue;
                    }
                    if (ea_mode == 0) g_m68k.d[ea_reg] = val;
                }
                continue;
            }
        }

        /* ---- Shifts (group 14) ---- */
        if (group == 0xE) {
            int size = (op >> 6) & 3;
            if (size < 3) {
                int count_reg = (op >> 9) & 7;
                int dr = op & 7;
                int direction = (op >> 8) & 1;
                int ir = (op >> 5) & 1;
                int count = ir ? (g_m68k.d[count_reg] & 63) : (count_reg ? count_reg : 8);

                if (size == 2) {
                    if (direction) { M68K_LSL32(g_m68k.d[dr], count); }
                    else { M68K_LSR32(g_m68k.d[dr], count); }
                } else if (size == 1) {
                    uint16_t tmp = (uint16_t)g_m68k.d[dr];
                    if (direction) { M68K_LSL16(tmp, count); }
                    else { M68K_LSR16(tmp, count); }
                    g_m68k.d[dr] = (g_m68k.d[dr] & 0xFFFF0000) | tmp;
                } else {
                    uint8_t tmp = (uint8_t)g_m68k.d[dr];
                    if (direction) { M68K_LSL8(tmp, count); }
                    else { M68K_LSR8(tmp, count); }
                    g_m68k.d[dr] = (g_m68k.d[dr] & 0xFFFFFF00) | tmp;
                }
                continue;
            }
        }

        /* ---- Unhandled: skip 1 word and hope for the best ---- */
        if (s_interp_logged <= INTERP_MAX_LOG) {
            fprintf(stderr, "  INTERP: unhandled opcode $%04X at $%06X\n", op, pc - 2);
        }
    }

    if (insn_count >= INTERP_MAX_INSNS) {
        fprintf(stderr, "  INTERP: hit instruction limit (%d) at $%06X\n",
                INTERP_MAX_INSNS, pc);
        fflush(stderr);
    }
    return false;
}
