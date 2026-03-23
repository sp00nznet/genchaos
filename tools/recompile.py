#!/usr/bin/env python3
"""
General Chaos — M68K Static Recompiler
=======================================
Converts a Sega Genesis ROM into native C code using genrecomp.

This tool performs recursive-descent disassembly of Motorola 68000
machine code and emits C functions that use genrecomp's hardware
backend (VDP, YM2612, PSG, Z80, I/O) for full Genesis fidelity.

Usage:
    python recompile.py <rom_file> <output_dir>

The generated C files can be compiled and linked against genrecomp
to produce a native executable that runs the game without emulation.

Original game by Brian Colin & Jeff Nauman at Game Refuge Inc.
Published by Electronic Arts, 1993.
"""

import struct
import sys
import os
from collections import defaultdict

# ============================================================================
# M68K Instruction Decoder
# ============================================================================

# Condition code names (for Bcc, DBcc, Scc)
CC_NAMES = [
    "T", "F", "HI", "LS", "CC", "CS", "NE", "EQ",
    "VC", "VS", "PL", "MI", "GE", "LT", "GT", "LE"
]

# Condition code C macro names
CC_MACROS = [
    "M68K_CC_T",  "M68K_CC_F",  "M68K_CC_HI", "M68K_CC_LS",
    "M68K_CC_CC", "M68K_CC_CS", "M68K_CC_NE", "M68K_CC_EQ",
    "M68K_CC_VC", "M68K_CC_VS", "M68K_CC_PL", "M68K_CC_MI",
    "M68K_CC_GE", "M68K_CC_LT", "M68K_CC_GT", "M68K_CC_LE"
]

SIZE_BYTE = 0
SIZE_WORD = 1
SIZE_LONG = 2

SIZE_SUFFIX = {SIZE_BYTE: "8", SIZE_WORD: "16", SIZE_LONG: "32"}
SIZE_NAMES = {SIZE_BYTE: "B", SIZE_WORD: "W", SIZE_LONG: "L"}
SIZE_BYTES = {SIZE_BYTE: 1, SIZE_WORD: 2, SIZE_LONG: 4}


class Disassembler:
    """M68K recursive-descent disassembler and C code generator."""

    def __init__(self, rom_data):
        self.rom = rom_data
        self.rom_size = len(rom_data)
        # Sets of addresses
        self.code_addrs = set()          # All addresses that are code
        self.func_entries = set()        # Function entry points
        self.branch_targets = set()      # Branch/jump targets within functions
        self.call_targets = set()        # JSR/BSR targets
        self.data_refs = set()           # Known data references
        self.visited = set()             # Visited during recursive descent
        self.instructions = {}           # addr -> (size, mnemonic, operands, raw_words)
        self.func_bounds = {}            # entry_addr -> (start, end, set_of_addrs)
        self.pending = []                # Addresses to explore
        self.jump_tables = {}            # addr -> list of target addresses
        self.unhandled_opcodes = set()   # Track opcodes we couldn't decode

    def read16(self, addr):
        """Read a big-endian 16-bit word from ROM."""
        if addr < 0 or addr + 1 >= self.rom_size:
            return 0
        return struct.unpack_from(">H", self.rom, addr)[0]

    def read32(self, addr):
        """Read a big-endian 32-bit long from ROM."""
        if addr < 0 or addr + 3 >= self.rom_size:
            return 0
        return struct.unpack_from(">I", self.rom, addr)[0]

    def sext8(self, val):
        return val if val < 0x80 else val - 0x100

    def sext16(self, val):
        return val if val < 0x8000 else val - 0x10000

    # ========================================================================
    # Effective Address Decoding
    # ========================================================================

    def decode_ea(self, mode, reg, size, pc, for_read=True):
        """
        Decode an M68K effective address.
        Returns (c_expr_read, c_expr_write, ext_words_consumed, extra_info).
        ext_words_consumed is the number of additional bytes consumed.
        """
        if mode == 0:  # Dn
            sz = SIZE_SUFFIX[size]
            r = f"g_m68k.d[{reg}]"
            return r, r, 0, None

        elif mode == 1:  # An
            r = f"g_m68k.a[{reg}]"
            return r, r, 0, None

        elif mode == 2:  # (An)
            addr_expr = f"g_m68k.a[{reg}]"
            sz = SIZE_SUFFIX[size]
            r = f"bus_read{sz}({addr_expr})"
            w = f"bus_write{sz}({addr_expr}, {{val}})"
            return r, w, 0, ("mem", addr_expr)

        elif mode == 3:  # (An)+
            addr_expr = f"g_m68k.a[{reg}]"
            inc = max(2, SIZE_BYTES[size]) if reg == 7 else SIZE_BYTES[size]
            sz = SIZE_SUFFIX[size]
            r = f"bus_read{sz}({addr_expr})"
            w = f"bus_write{sz}({addr_expr}, {{val}})"
            return r, w, 0, ("postinc", reg, inc)

        elif mode == 4:  # -(An)
            dec = max(2, SIZE_BYTES[size]) if reg == 7 else SIZE_BYTES[size]
            sz = SIZE_SUFFIX[size]
            r = f"bus_read{sz}(g_m68k.a[{reg}] - {dec})"
            w = f"bus_write{sz}(g_m68k.a[{reg}] - {dec}, {{val}})"
            return r, w, 0, ("predec", reg, dec)

        elif mode == 5:  # d16(An)
            disp = self.sext16(self.read16(pc))
            addr_expr = f"(g_m68k.a[{reg}] + {disp})" if disp >= 0 else f"(g_m68k.a[{reg}] - {-disp})"
            if disp == 0:
                addr_expr = f"g_m68k.a[{reg}]"
            sz = SIZE_SUFFIX[size]
            r = f"bus_read{sz}({addr_expr})"
            w = f"bus_write{sz}({addr_expr}, {{val}})"
            return r, w, 2, ("mem", addr_expr)

        elif mode == 6:  # d8(An,Xn.s)
            ext = self.read16(pc)
            disp = self.sext8(ext & 0xFF)
            xreg = (ext >> 12) & 7
            xtype = "a" if (ext & 0x8000) else "d"
            xsize = ".l" if (ext & 0x0800) else ".w"
            if xsize == ".w":
                idx_expr = f"(int16_t)g_m68k.{xtype}[{xreg}]"
            else:
                idx_expr = f"g_m68k.{xtype}[{xreg}]"
            if disp == 0:
                addr_expr = f"(g_m68k.a[{reg}] + (uint32_t)(int32_t){idx_expr})"
            elif disp > 0:
                addr_expr = f"(g_m68k.a[{reg}] + (uint32_t)(int32_t){idx_expr} + {disp})"
            else:
                addr_expr = f"(g_m68k.a[{reg}] + (uint32_t)(int32_t){idx_expr} - {-disp})"
            sz = SIZE_SUFFIX[size]
            r = f"bus_read{sz}({addr_expr})"
            w = f"bus_write{sz}({addr_expr}, {{val}})"
            return r, w, 2, ("mem", addr_expr)

        elif mode == 7:
            if reg == 0:  # xxx.W (absolute short)
                addr_val = self.sext16(self.read16(pc))
                addr_u = addr_val & 0xFFFFFF
                sz = SIZE_SUFFIX[size]
                r = f"bus_read{sz}(0x{addr_u:06X})"
                w = f"bus_write{sz}(0x{addr_u:06X}, {{val}})"
                return r, w, 2, ("mem_abs", addr_u)

            elif reg == 1:  # xxx.L (absolute long)
                addr_val = self.read32(pc)
                addr_u = addr_val & 0xFFFFFF
                sz = SIZE_SUFFIX[size]
                r = f"bus_read{sz}(0x{addr_u:06X})"
                w = f"bus_write{sz}(0x{addr_u:06X}, {{val}})"
                return r, w, 4, ("mem_abs", addr_u)

            elif reg == 2:  # d16(PC)
                disp = self.sext16(self.read16(pc))
                addr_val = (pc + disp) & 0xFFFFFF
                sz = SIZE_SUFFIX[size]
                r = f"bus_read{sz}(0x{addr_val:06X})"
                w = f"bus_write{sz}(0x{addr_val:06X}, {{val}})"
                return r, w, 2, ("mem_abs", addr_val)

            elif reg == 3:  # d8(PC,Xn)
                ext = self.read16(pc)
                disp = self.sext8(ext & 0xFF)
                xreg = (ext >> 12) & 7
                xtype = "a" if (ext & 0x8000) else "d"
                xsize = ".l" if (ext & 0x0800) else ".w"
                base_addr = pc + disp
                if xsize == ".w":
                    idx_expr = f"(int16_t)g_m68k.{xtype}[{xreg}]"
                else:
                    idx_expr = f"g_m68k.{xtype}[{xreg}]"
                addr_expr = f"(0x{base_addr & 0xFFFFFF:06X} + (uint32_t)(int32_t){idx_expr})"
                sz = SIZE_SUFFIX[size]
                r = f"bus_read{sz}({addr_expr})"
                w = f"bus_write{sz}({addr_expr}, {{val}})"
                return r, w, 2, ("mem_idx", base_addr & 0xFFFFFF)

            elif reg == 4:  # #immediate
                nop_write = "(void)({val}) /* imm write */"
                if size == SIZE_BYTE:
                    val = self.read16(pc) & 0xFF
                    return f"0x{val:02X}", nop_write, 2, ("imm", val)
                elif size == SIZE_WORD:
                    val = self.read16(pc)
                    return f"0x{val:04X}", nop_write, 2, ("imm", val)
                elif size == SIZE_LONG:
                    val = self.read32(pc)
                    return f"0x{val:08X}", nop_write, 4, ("imm", val)

        return "0 /* UNKNOWN_EA */", "(void)({val}) /* UNKNOWN_EA_WRITE */", 0, None

    # ========================================================================
    # Instruction Decoding — returns (length_bytes, c_code_lines, is_terminal, branch_targets)
    # ========================================================================

    def decode_insn(self, addr):
        """
        Decode a single M68K instruction at addr.
        Returns (byte_length, c_lines[], is_terminal, [branch_target_addrs], is_call).
        """
        if addr + 1 >= self.rom_size:
            return 2, [f"/* ${addr:06X}: beyond ROM */"], True, [], False

        op = self.read16(addr)
        pc = addr + 2  # PC after reading opcode word

        group = (op >> 12) & 0xF

        # ---- Group 0: Immediate ops, bit ops, MOVEP ----
        if group == 0:
            return self._decode_group0(addr, op, pc)

        # ---- Group 1: MOVE.B ----
        elif group == 1:
            return self._decode_move(addr, op, pc, SIZE_BYTE)

        # ---- Group 2: MOVE.L ----
        elif group == 2:
            return self._decode_move(addr, op, pc, SIZE_LONG)

        # ---- Group 3: MOVE.W ----
        elif group == 3:
            return self._decode_move(addr, op, pc, SIZE_WORD)

        # ---- Group 4: Miscellaneous ----
        elif group == 4:
            return self._decode_group4(addr, op, pc)

        # ---- Group 5: ADDQ/SUBQ/Scc/DBcc ----
        elif group == 5:
            return self._decode_group5(addr, op, pc)

        # ---- Group 6: Bcc/BSR/BRA ----
        elif group == 6:
            return self._decode_group6(addr, op, pc)

        # ---- Group 7: MOVEQ ----
        elif group == 7:
            return self._decode_moveq(addr, op, pc)

        # ---- Group 8: OR/DIV/SBCD ----
        elif group == 8:
            return self._decode_group8(addr, op, pc)

        # ---- Group 9: SUB/SUBA/SUBX ----
        elif group == 9:
            return self._decode_group9(addr, op, pc)

        # ---- Group 10: Line-A (unimplemented) ----
        elif group == 0xA:
            return 2, [f"/* ${addr:06X}: LINE-A ${op:04X} (unimplemented) */", "return;"], True, [], False

        # ---- Group 11: CMP/CMPA/EOR/CMPM ----
        elif group == 0xB:
            return self._decode_groupB(addr, op, pc)

        # ---- Group 12: AND/MUL/ABCD/EXG ----
        elif group == 0xC:
            return self._decode_groupC(addr, op, pc)

        # ---- Group 13: ADD/ADDA/ADDX ----
        elif group == 0xD:
            return self._decode_groupD(addr, op, pc)

        # ---- Group 14: Shift/Rotate ----
        elif group == 0xE:
            return self._decode_groupE(addr, op, pc)

        # ---- Group 15: Line-F ----
        elif group == 0xF:
            return 2, [f"/* ${addr:06X}: LINE-F ${op:04X} (unimplemented) */", "return;"], True, [], False

        self.unhandled_opcodes.add(op)
        return 2, [f"/* ${addr:06X}: UNKNOWN ${op:04X} */"], False, [], False

    # ========================================================================
    # MOVE instructions (groups 1, 2, 3)
    # ========================================================================

    def _decode_move(self, addr, op, pc, size):
        # Source EA
        src_mode = (op >> 3) & 7
        src_reg = op & 7
        src_read, _, src_ext, src_info = self.decode_ea(src_mode, src_reg, size, pc)
        pc += src_ext

        # Destination EA (note: reversed field order in MOVE encoding)
        dst_reg = (op >> 9) & 7
        dst_mode = (op >> 6) & 7
        dst_read, dst_write, dst_ext, dst_info = self.decode_ea(dst_mode, dst_reg, size, pc)
        pc += dst_ext

        lines = []
        sz = SIZE_SUFFIX[size]
        total_len = pc - addr

        # Handle pre-decrements and post-increments
        pre_lines, post_lines = self._ea_side_effects(src_mode, src_reg, src_info, size, "src")
        pre_lines2, post_lines2 = self._ea_side_effects(dst_mode, dst_reg, dst_info, size, "dst")
        lines.extend(pre_lines)
        lines.extend(pre_lines2)

        if dst_mode == 0:  # MOVE to Dn
            if size == SIZE_BYTE:
                lines.append(f"{{ uint8_t _val = (uint8_t)({src_read}); "
                             f"g_m68k.d[{dst_reg}] = (g_m68k.d[{dst_reg}] & 0xFFFFFF00) | _val; "
                             f"M68K_TST8(_val); }}")
            elif size == SIZE_WORD:
                lines.append(f"{{ uint16_t _val = (uint16_t)({src_read}); "
                             f"g_m68k.d[{dst_reg}] = (g_m68k.d[{dst_reg}] & 0xFFFF0000) | _val; "
                             f"M68K_TST16(_val); }}")
            else:
                lines.append(f"g_m68k.d[{dst_reg}] = {src_read}; "
                             f"M68K_TST32(g_m68k.d[{dst_reg}]);")
        elif dst_mode == 1:  # MOVEA
            # MOVEA does not update flags
            if size == SIZE_WORD:
                lines.append(f"g_m68k.a[{dst_reg}] = (uint32_t)(int32_t)(int16_t)({src_read});")
            else:
                lines.append(f"g_m68k.a[{dst_reg}] = {src_read};")
        else:
            # MOVE to memory
            val_expr = src_read
            if dst_write is None:
                lines.append(f"/* MOVE to unknown dest ${addr:06X} */")
            else:
                lines.append(f"{{ uint{sz}_t _val = (uint{sz}_t)({val_expr}); "
                             f"{dst_write.replace('{val}', '_val')}; M68K_TST{sz}(_val); }}")

        lines.extend(post_lines)
        lines.extend(post_lines2)

        return total_len, lines, False, [], False

    def _ea_write_expr(self, ea_write, val_expr, addr=0):
        """Safely generate a write expression, handling None ea_write."""
        if ea_write is None:
            return f"/* write to read-only EA at ${addr:06X} */"
        return ea_write.replace("{val}", val_expr)

    def _ea_side_effects(self, mode, reg, info, size, prefix):
        """Generate pre/post lines for -(An) and (An)+ addressing."""
        pre = []
        post = []
        if mode == 3 and info and info[0] == "postinc":
            _, r, inc = info
            post.append(f"g_m68k.a[{r}] += {inc};")
        elif mode == 4 and info and info[0] == "predec":
            _, r, dec = info
            pre.append(f"g_m68k.a[{r}] -= {dec};")
        return pre, post

    # ========================================================================
    # Group 0: ORI, ANDI, SUBI, ADDI, EORI, CMPI, BTST, BCHG, BCLR, BSET
    # ========================================================================

    def _decode_group0(self, addr, op, pc):
        if op & 0x0100:  # Bit operations with register
            return self._decode_bit_reg(addr, op, pc)

        sub_op = (op >> 9) & 7
        size_field = (op >> 6) & 3

        # BTST/BCHG/BCLR/BSET with immediate bit number
        if sub_op == 4:
            return self._decode_bit_imm(addr, op, pc)

        if size_field == 3:
            # Special: could be CAS, CAS2, etc. — rare, skip
            self.unhandled_opcodes.add(op)
            return 2, [f"/* ${addr:06X}: unhandled group0 ${op:04X} */"], False, [], False

        size = size_field
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7

        # Read immediate value
        if size == SIZE_BYTE:
            imm = self.read16(pc) & 0xFF
            imm_str = f"0x{imm:02X}"
            pc += 2
        elif size == SIZE_WORD:
            imm = self.read16(pc)
            imm_str = f"0x{imm:04X}"
            pc += 2
        else:
            imm = self.read32(pc)
            imm_str = f"0x{imm:08X}"
            pc += 4

        # Special case: ORI/ANDI/EORI to CCR/SR
        if ea_mode == 7 and ea_reg == 4:
            if sub_op == 0:  # ORI to CCR/SR
                return pc - addr, [f"m68k_set_ccr(m68k_get_ccr() | {imm_str});"], False, [], False
            elif sub_op == 1:  # ANDI to CCR/SR
                return pc - addr, [f"m68k_set_ccr(m68k_get_ccr() & {imm_str});"], False, [], False
            elif sub_op == 5:  # EORI to CCR/SR
                return pc - addr, [f"m68k_set_ccr(m68k_get_ccr() ^ {imm_str});"], False, [], False

        ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, size, pc)
        pc += ext
        sz = SIZE_SUFFIX[size]
        lines = []

        pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, size, "")
        lines.extend(pre)

        op_names = ["ORI", "ANDI", "SUBI", "ADDI", None, "EORI", "CMPI", None]
        macro_names = ["OR", "AND", "SUB", "ADD", None, "EOR", "CMP", None]
        name = op_names[sub_op]
        macro = macro_names[sub_op]

        if name is None:
            self.unhandled_opcodes.add(op)
            return pc - addr, [f"/* ${addr:06X}: unhandled imm op ${op:04X} */"], False, [], False

        if ea_mode == 0:  # Dn
            if macro == "CMP":
                lines.append(f"M68K_CMP{sz}(g_m68k.d[{ea_reg}], {imm_str});")
            elif size == SIZE_BYTE:
                lines.append(f"{{ uint8_t _tmp = (uint8_t)g_m68k.d[{ea_reg}]; "
                             f"M68K_{macro}8(_tmp, {imm_str}); "
                             f"g_m68k.d[{ea_reg}] = (g_m68k.d[{ea_reg}] & 0xFFFFFF00) | _tmp; }}")
            elif size == SIZE_WORD:
                lines.append(f"{{ uint16_t _tmp = (uint16_t)g_m68k.d[{ea_reg}]; "
                             f"M68K_{macro}16(_tmp, {imm_str}); "
                             f"g_m68k.d[{ea_reg}] = (g_m68k.d[{ea_reg}] & 0xFFFF0000) | _tmp; }}")
            else:
                lines.append(f"M68K_{macro}32(g_m68k.d[{ea_reg}], {imm_str});")
        else:  # memory EA
            if macro == "CMP":
                lines.append(f"M68K_CMP{sz}({ea_read}, {imm_str});")
            elif ea_write is None:
                lines.append(f"/* ${addr:06X}: {name} to read-only EA ${op:04X} */")
            else:
                lines.append(f"{{ uint{sz}_t _tmp = {ea_read}; "
                             f"M68K_{macro}{sz}(_tmp, {imm_str}); "
                             f"{ea_write.replace('{val}', '_tmp')}; }}")

        lines.extend(post)
        return pc - addr, lines, False, [], False

    def _decode_bit_reg(self, addr, op, pc):
        """BTST/BCHG/BCLR/BSET with Dn as bit number, and MOVEP."""
        bit_reg = (op >> 9) & 7
        sub = (op >> 6) & 3
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7

        # MOVEP: mode is 001 (address register) with opmode 4-7
        if ea_mode == 1:
            disp = self.sext16(self.read16(pc))
            pc += 2
            opmode = (op >> 6) & 7
            reg = (op >> 9) & 7
            areg = op & 7
            addr_expr = f"(g_m68k.a[{areg}] + {disp})" if disp >= 0 else f"(g_m68k.a[{areg}] - {-disp})"
            if disp == 0:
                addr_expr = f"g_m68k.a[{areg}]"
            if opmode == 4:  # MOVEP.W Dn, d(An) - reg to mem
                return pc - addr, [
                    f"bus_write8({addr_expr}, (uint8_t)(g_m68k.d[{reg}] >> 8));",
                    f"bus_write8({addr_expr} + 2, (uint8_t)g_m68k.d[{reg}]);"
                ], False, [], False
            elif opmode == 5:  # MOVEP.L Dn, d(An)
                return pc - addr, [
                    f"bus_write8({addr_expr}, (uint8_t)(g_m68k.d[{reg}] >> 24));",
                    f"bus_write8({addr_expr} + 2, (uint8_t)(g_m68k.d[{reg}] >> 16));",
                    f"bus_write8({addr_expr} + 4, (uint8_t)(g_m68k.d[{reg}] >> 8));",
                    f"bus_write8({addr_expr} + 6, (uint8_t)g_m68k.d[{reg}]);"
                ], False, [], False
            elif opmode == 6:  # MOVEP.W d(An), Dn - mem to reg
                return pc - addr, [
                    f"g_m68k.d[{reg}] = (g_m68k.d[{reg}] & 0xFFFF0000) | "
                    f"((uint16_t)bus_read8({addr_expr}) << 8) | bus_read8({addr_expr} + 2);"
                ], False, [], False
            elif opmode == 7:  # MOVEP.L d(An), Dn
                return pc - addr, [
                    f"g_m68k.d[{reg}] = ((uint32_t)bus_read8({addr_expr}) << 24) | "
                    f"((uint32_t)bus_read8({addr_expr} + 2) << 16) | "
                    f"((uint32_t)bus_read8({addr_expr} + 4) << 8) | "
                    f"bus_read8({addr_expr} + 6);"
                ], False, [], False
            self.unhandled_opcodes.add(op)
            return pc - addr, [f"/* ${addr:06X}: unhandled MOVEP ${op:04X} */"], False, [], False

        size = SIZE_LONG if ea_mode == 0 else SIZE_BYTE
        ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, size, pc)
        pc += ext
        sz = SIZE_SUFFIX[size]
        bit_expr = f"g_m68k.d[{bit_reg}]"

        lines = []
        pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, size, "")
        lines.extend(pre)

        ops = ["BTST", "BCHG", "BCLR", "BSET"]
        op_name = ops[sub]

        if ea_mode == 0:
            if sub == 0:
                lines.append(f"M68K_BTST32(g_m68k.d[{ea_reg}], {bit_expr});")
            elif sub == 1:
                lines.append(f"M68K_BCHG32(g_m68k.d[{ea_reg}], {bit_expr});")
            elif sub == 2:
                lines.append(f"M68K_BCLR32(g_m68k.d[{ea_reg}], {bit_expr});")
            elif sub == 3:
                lines.append(f"M68K_BSET32(g_m68k.d[{ea_reg}], {bit_expr});")
        else:
            if sub == 0:
                lines.append(f"M68K_BTST8({ea_read}, {bit_expr});")
            elif ea_write is None:
                lines.append(f"/* ${addr:06X}: bit op on read-only EA ${op:04X} */")
            else:
                lines.append(f"{{ uint8_t _tmp = {ea_read}; ")
                if sub == 1:
                    lines.append(f"  g_m68k.flag_Z = !(_tmp & (1u << ({bit_expr} & 7))); _tmp ^= (1u << ({bit_expr} & 7)); ")
                elif sub == 2:
                    lines.append(f"  g_m68k.flag_Z = !(_tmp & (1u << ({bit_expr} & 7))); _tmp &= ~(1u << ({bit_expr} & 7)); ")
                elif sub == 3:
                    lines.append(f"  g_m68k.flag_Z = !(_tmp & (1u << ({bit_expr} & 7))); _tmp |= (1u << ({bit_expr} & 7)); ")
                lines.append(f"  {ea_write.replace('{val}', '_tmp')}; }}")

        lines.extend(post)
        return pc - addr, lines, False, [], False

    def _decode_bit_imm(self, addr, op, pc):
        """BTST/BCHG/BCLR/BSET with immediate bit number."""
        sub = (op >> 6) & 3
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        bit_num = self.read16(pc) & 0xFF
        pc += 2
        size = SIZE_LONG if ea_mode == 0 else SIZE_BYTE
        ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, size, pc)
        pc += ext
        bit_mask = bit_num & (31 if ea_mode == 0 else 7)

        lines = []
        pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, size, "")
        lines.extend(pre)

        if ea_mode == 0:
            if sub == 0:
                lines.append(f"M68K_BTST32(g_m68k.d[{ea_reg}], {bit_mask});")
            elif sub == 1:
                lines.append(f"M68K_BCHG32(g_m68k.d[{ea_reg}], {bit_mask});")
            elif sub == 2:
                lines.append(f"M68K_BCLR32(g_m68k.d[{ea_reg}], {bit_mask});")
            elif sub == 3:
                lines.append(f"M68K_BSET32(g_m68k.d[{ea_reg}], {bit_mask});")
        else:
            if sub == 0:
                lines.append(f"M68K_BTST8({ea_read}, {bit_mask});")
            else:
                lines.append(f"{{ uint8_t _tmp = {ea_read};")
                if sub == 1:
                    lines.append(f"  g_m68k.flag_Z = !(_tmp & (1u << {bit_mask})); _tmp ^= (1u << {bit_mask});")
                elif sub == 2:
                    lines.append(f"  g_m68k.flag_Z = !(_tmp & (1u << {bit_mask})); _tmp &= ~(1u << {bit_mask});")
                elif sub == 3:
                    lines.append(f"  g_m68k.flag_Z = !(_tmp & (1u << {bit_mask})); _tmp |= (1u << {bit_mask});")
                lines.append(f"  {ea_write.replace('{val}', '_tmp')}; }}")

        lines.extend(post)
        return pc - addr, lines, False, [], False

    # ========================================================================
    # Group 4: Miscellaneous (LEA, PEA, CLR, NEG, NOT, TST, JSR, JMP, etc.)
    # ========================================================================

    def _decode_group4(self, addr, op, pc):
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        sub = (op >> 8) & 0xF

        # ---- NOP ----
        if op == 0x4E71:
            return 2, ["/* NOP */"], False, [], False

        # ---- RTS ----
        if op == 0x4E75:
            return 2, [
                "g_m68k.pc = bus_read32(g_m68k.a[7]); g_m68k.a[7] += 4;",
                "return;"
            ], True, [], False

        # ---- RTE ----
        if op == 0x4E73:
            # RTE just returns to the C caller. The exception frame (SR + PC)
            # is popped by recomp_m68k_exception() after this function returns.
            # If called outside an exception context, the frame is already
            # handled by the caller's stack management.
            return 2, [
                "/* RTE — return from exception (frame popped by caller) */",
                "return;"
            ], True, [], False

        # ---- TRAP #n ----
        if (op & 0xFFF0) == 0x4E40:
            trap_num = op & 0xF
            return 2, [f"/* TRAP #{trap_num} */",
                       f"recomp_m68k_exception({32 + trap_num});"], False, [], False

        # ---- LINK An, #disp ----
        if (op & 0xFFF8) == 0x4E50:
            reg = op & 7
            disp = self.sext16(self.read16(pc))
            return 4, [
                f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], g_m68k.a[{reg}]);",
                f"g_m68k.a[{reg}] = g_m68k.a[7];",
                f"g_m68k.a[7] += {disp};"
            ], False, [], False

        # ---- UNLK An ----
        if (op & 0xFFF8) == 0x4E58:
            reg = op & 7
            return 2, [
                f"g_m68k.a[7] = g_m68k.a[{reg}];",
                f"g_m68k.a[{reg}] = bus_read32(g_m68k.a[7]); g_m68k.a[7] += 4;"
            ], False, [], False

        # ---- MOVE USP ----
        if (op & 0xFFF0) == 0x4E60:
            reg = op & 7
            if op & 0x0008:
                return 2, [f"g_m68k.a[{reg}] = g_m68k.usp;"], False, [], False
            else:
                return 2, [f"g_m68k.usp = g_m68k.a[{reg}];"], False, [], False

        # ---- STOP #imm ----
        if op == 0x4E72:
            imm = self.read16(pc)
            return 4, [f"m68k_set_sr(0x{imm:04X}); /* STOP */"], False, [], False

        # ---- JSR ----
        if (op & 0xFFC0) == 0x4E80:
            return self._decode_jsr(addr, op, pc)

        # ---- JMP ----
        if (op & 0xFFC0) == 0x4EC0:
            return self._decode_jmp(addr, op, pc)

        # ---- LEA ----
        if (op & 0xF1C0) == 0x41C0:
            reg = (op >> 9) & 7
            return self._decode_lea(addr, op, pc, reg)

        # ---- PEA ----
        if (op & 0xFFC0) == 0x4840:
            return self._decode_pea(addr, op, pc)

        # ---- CLR ----
        if (op & 0xFF00) == 0x4200:
            size = (op >> 6) & 3
            if size == 3:
                self.unhandled_opcodes.add(op)
                return 2, [f"/* ${addr:06X}: unhandled ${op:04X} */"], False, [], False
            return self._decode_clr(addr, op, pc, size)

        # ---- NEG ----
        if (op & 0xFF00) == 0x4400:
            size = (op >> 6) & 3
            if size == 3:
                self.unhandled_opcodes.add(op)
                return 2, [f"/* ${addr:06X}: unhandled ${op:04X} */"], False, [], False
            return self._decode_unary(addr, op, pc, size, "NEG")

        # ---- NEGX ----
        if (op & 0xFF00) == 0x4000:
            size = (op >> 6) & 3
            if size == 3:
                # MOVE from SR
                ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
                pc += ext
                lines = []
                pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, SIZE_WORD, "")
                lines.extend(pre)
                if ea_mode == 0:
                    lines.append(f"g_m68k.d[{ea_reg}] = (g_m68k.d[{ea_reg}] & 0xFFFF0000) | m68k_get_sr();")
                else:
                    lines.append(f"{ea_write.replace('{val}', 'm68k_get_sr()')};")
                lines.extend(post)
                return pc - addr, lines, False, [], False
            return self._decode_unary(addr, op, pc, size, "NEGX")

        # ---- NOT ----
        if (op & 0xFF00) == 0x4600:
            size = (op >> 6) & 3
            if size == 3:
                self.unhandled_opcodes.add(op)
                return 2, [f"/* ${addr:06X}: unhandled ${op:04X} */"], False, [], False
            return self._decode_unary(addr, op, pc, size, "NOT")

        # ---- TST ----
        if (op & 0xFF00) == 0x4A00:
            size = (op >> 6) & 3
            if size == 3:
                # TAS or illegal
                if ea_mode == 0:
                    return 2, [f"M68K_TST8((uint8_t)g_m68k.d[{ea_reg}]); "
                               f"g_m68k.d[{ea_reg}] |= 0x80; /* TAS */"], False, [], False
                ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, SIZE_BYTE, pc)
                pc += ext
                return pc - addr, [f"{{ uint8_t _v = {ea_read}; M68K_TST8(_v); _v |= 0x80; "
                                   f"{ea_write.replace('{val}', '_v')}; }} /* TAS */"], False, [], False
            return self._decode_tst(addr, op, pc, size)

        # ---- SWAP ----
        if (op & 0xFFF8) == 0x4840:
            reg = op & 7
            return 2, [f"g_m68k.d[{reg}] = (g_m68k.d[{reg}] >> 16) | (g_m68k.d[{reg}] << 16); "
                       f"M68K_TST32(g_m68k.d[{reg}]); g_m68k.flag_C = false; g_m68k.flag_V = false;"], False, [], False

        # ---- EXT ----
        if (op & 0xFFF8) == 0x4880:
            reg = op & 7
            return 2, [
                f"g_m68k.d[{reg}] = (g_m68k.d[{reg}] & 0xFFFF0000) | "
                f"(uint16_t)(int16_t)(int8_t)(uint8_t)g_m68k.d[{reg}];",
                f"M68K_TST16((uint16_t)g_m68k.d[{reg}]); g_m68k.flag_C = false; g_m68k.flag_V = false;"
            ], False, [], False

        if (op & 0xFFF8) == 0x48C0:
            reg = op & 7
            return 2, [
                f"g_m68k.d[{reg}] = (uint32_t)(int32_t)(int16_t)(uint16_t)g_m68k.d[{reg}];",
                f"M68K_TST32(g_m68k.d[{reg}]); g_m68k.flag_C = false; g_m68k.flag_V = false;"
            ], False, [], False

        # ---- MOVEM ----
        if (op & 0xFB80) == 0x4880:
            return self._decode_movem(addr, op, pc)

        # ---- MOVE to CCR ----
        if (op & 0xFFC0) == 0x44C0:
            ea_read, _, ext, ea_info = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
            pc += ext
            return pc - addr, [f"m68k_set_ccr((uint8_t)({ea_read}));"], False, [], False

        # ---- MOVE to SR ----
        if (op & 0xFFC0) == 0x46C0:
            ea_read, _, ext, ea_info = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
            pc += ext
            return pc - addr, [f"m68k_set_sr({ea_read});"], False, [], False

        self.unhandled_opcodes.add(op)
        return 2, [f"/* ${addr:06X}: unhandled group4 ${op:04X} */"], False, [], False

    def _decode_jsr(self, addr, op, pc):
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7

        if ea_mode == 7 and ea_reg == 1:  # JSR xxx.L
            target = self.read32(pc) & 0xFFFFFF
            pc += 4
            return pc - addr, [
                f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], 0x{pc & 0xFFFFFF:06X});",
                f"genchaos_call(0x{target:06X});"
            ], False, [target], True

        elif ea_mode == 7 and ea_reg == 0:  # JSR xxx.W
            target = self.sext16(self.read16(pc)) & 0xFFFFFF
            pc += 2
            return pc - addr, [
                f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], 0x{pc & 0xFFFFFF:06X});",
                f"genchaos_call(0x{target:06X});"
            ], False, [target], True

        elif ea_mode == 7 and ea_reg == 2:  # JSR d16(PC)
            disp = self.sext16(self.read16(pc))
            target = (pc + disp) & 0xFFFFFF
            pc += 2
            return pc - addr, [
                f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], 0x{pc & 0xFFFFFF:06X});",
                f"genchaos_call(0x{target:06X});"
            ], False, [target], True

        elif ea_mode == 2:  # JSR (An)
            pc_after = pc
            return pc - addr, [
                f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], 0x{pc_after & 0xFFFFFF:06X});",
                f"genchaos_call(g_m68k.a[{ea_reg}]);"
            ], False, [], True

        elif ea_mode == 5:  # JSR d16(An)
            disp = self.sext16(self.read16(pc))
            pc += 2
            if disp >= 0:
                addr_expr = f"(g_m68k.a[{ea_reg}] + {disp})"
            else:
                addr_expr = f"(g_m68k.a[{ea_reg}] - {-disp})"
            return pc - addr, [
                f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], 0x{pc & 0xFFFFFF:06X});",
                f"genchaos_call({addr_expr});"
            ], False, [], True

        elif ea_mode == 6:  # JSR d8(An,Xn)
            ext = self.read16(pc)
            disp = self.sext8(ext & 0xFF)
            xreg = (ext >> 12) & 7
            xtype = "a" if (ext & 0x8000) else "d"
            xsize = "(int32_t)" if (ext & 0x0800) else "(int32_t)(int16_t)"
            pc += 2
            addr_expr = f"(g_m68k.a[{ea_reg}] + (uint32_t){xsize}g_m68k.{xtype}[{xreg}] + {disp})"
            return pc - addr, [
                f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], 0x{pc & 0xFFFFFF:06X});",
                f"genchaos_call({addr_expr});"
            ], False, [], True

        self.unhandled_opcodes.add(op)
        return 2, [f"/* ${addr:06X}: unhandled JSR mode ${op:04X} */"], False, [], False

    def _decode_jmp(self, addr, op, pc):
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7

        if ea_mode == 7 and ea_reg == 1:  # JMP xxx.L
            target = self.read32(pc) & 0xFFFFFF
            pc += 4
            return pc - addr, [
                f"genchaos_call(0x{target:06X});",
                f"return;"
            ], True, [target], False

        elif ea_mode == 7 and ea_reg == 0:  # JMP xxx.W
            target = self.sext16(self.read16(pc)) & 0xFFFFFF
            pc += 2
            return pc - addr, [
                f"genchaos_call(0x{target:06X});",
                f"return;"
            ], True, [target], False

        elif ea_mode == 7 and ea_reg == 2:  # JMP d16(PC)
            disp = self.sext16(self.read16(pc))
            target = (pc + disp) & 0xFFFFFF
            pc += 2
            return pc - addr, [
                f"genchaos_call(0x{target:06X});",
                f"return;"
            ], True, [target], False

        elif ea_mode == 2:  # JMP (An)
            return 2, [
                f"genchaos_call(g_m68k.a[{ea_reg}]);",
                f"return;"
            ], True, [], False

        elif ea_mode == 5:  # JMP d16(An)
            disp = self.sext16(self.read16(pc))
            pc += 2
            if disp >= 0:
                addr_expr = f"(g_m68k.a[{ea_reg}] + {disp})"
            else:
                addr_expr = f"(g_m68k.a[{ea_reg}] - {-disp})"
            return pc - addr, [
                f"genchaos_call({addr_expr});",
                f"return;"
            ], True, [], False

        elif ea_mode == 6:  # JMP d8(An,Xn)
            ext = self.read16(pc)
            disp = self.sext8(ext & 0xFF)
            xreg = (ext >> 12) & 7
            xtype = "a" if (ext & 0x8000) else "d"
            xsize = "(int32_t)" if (ext & 0x0800) else "(int32_t)(int16_t)"
            pc += 2
            addr_expr = f"(g_m68k.a[{ea_reg}] + (uint32_t){xsize}g_m68k.{xtype}[{xreg}] + {disp})"
            return pc - addr, [
                f"genchaos_call({addr_expr});",
                f"return;"
            ], True, [], False

        self.unhandled_opcodes.add(op)
        return 2, [f"/* ${addr:06X}: unhandled JMP mode ${op:04X} */"], False, [], False

    def _decode_lea(self, addr, op, pc, reg):
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7

        if ea_mode == 2:  # LEA (An), Ax
            return 2, [f"g_m68k.a[{reg}] = g_m68k.a[{ea_reg}];"], False, [], False
        elif ea_mode == 5:  # LEA d16(An), Ax
            disp = self.sext16(self.read16(pc))
            pc += 2
            if disp == 0:
                return pc - addr, [f"g_m68k.a[{reg}] = g_m68k.a[{ea_reg}];"], False, [], False
            elif disp > 0:
                return pc - addr, [f"g_m68k.a[{reg}] = g_m68k.a[{ea_reg}] + {disp};"], False, [], False
            else:
                return pc - addr, [f"g_m68k.a[{reg}] = g_m68k.a[{ea_reg}] - {-disp};"], False, [], False
        elif ea_mode == 6:  # LEA d8(An,Xn), Ax
            ext = self.read16(pc)
            disp = self.sext8(ext & 0xFF)
            xreg = (ext >> 12) & 7
            xtype = "a" if (ext & 0x8000) else "d"
            xsize = "(int32_t)" if (ext & 0x0800) else "(int32_t)(int16_t)"
            pc += 2
            return pc - addr, [
                f"g_m68k.a[{reg}] = g_m68k.a[{ea_reg}] + (uint32_t){xsize}g_m68k.{xtype}[{xreg}] + {disp};"
            ], False, [], False
        elif ea_mode == 7 and ea_reg == 0:  # LEA xxx.W
            val = self.sext16(self.read16(pc)) & 0xFFFFFF
            pc += 2
            return pc - addr, [f"g_m68k.a[{reg}] = 0x{val:06X};"], False, [], False
        elif ea_mode == 7 and ea_reg == 1:  # LEA xxx.L
            val = self.read32(pc) & 0xFFFFFF
            pc += 4
            return pc - addr, [f"g_m68k.a[{reg}] = 0x{val:08X};"], False, [], False
        elif ea_mode == 7 and ea_reg == 2:  # LEA d16(PC)
            disp = self.sext16(self.read16(pc))
            val = (pc + disp) & 0xFFFFFF
            pc += 2
            return pc - addr, [f"g_m68k.a[{reg}] = 0x{val:06X};"], False, [], False
        elif ea_mode == 7 and ea_reg == 3:  # LEA d8(PC,Xn)
            ext = self.read16(pc)
            disp = self.sext8(ext & 0xFF)
            xreg = (ext >> 12) & 7
            xtype = "a" if (ext & 0x8000) else "d"
            xsize = "(int32_t)" if (ext & 0x0800) else "(int32_t)(int16_t)"
            base = (pc + disp) & 0xFFFFFF
            pc += 2
            return pc - addr, [
                f"g_m68k.a[{reg}] = 0x{base:06X} + (uint32_t){xsize}g_m68k.{xtype}[{xreg}];"
            ], False, [], False

        self.unhandled_opcodes.add(op)
        return 2, [f"/* ${addr:06X}: unhandled LEA ${op:04X} */"], False, [], False

    def _decode_pea(self, addr, op, pc):
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7

        if ea_mode == 2:  # PEA (An)
            return 2, [f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], g_m68k.a[{ea_reg}]);"], False, [], False
        elif ea_mode == 5:
            disp = self.sext16(self.read16(pc)); pc += 2
            expr = f"g_m68k.a[{ea_reg}] + {disp}" if disp >= 0 else f"g_m68k.a[{ea_reg}] - {-disp}"
            return pc - addr, [f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], {expr});"], False, [], False
        elif ea_mode == 7 and ea_reg == 0:
            val = self.sext16(self.read16(pc)) & 0xFFFFFF; pc += 2
            return pc - addr, [f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], 0x{val:06X});"], False, [], False
        elif ea_mode == 7 and ea_reg == 1:
            val = self.read32(pc) & 0xFFFFFF; pc += 4
            return pc - addr, [f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], 0x{val:08X});"], False, [], False
        elif ea_mode == 7 and ea_reg == 2:
            disp = self.sext16(self.read16(pc)); val = (pc + disp) & 0xFFFFFF; pc += 2
            return pc - addr, [f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], 0x{val:06X});"], False, [], False

        self.unhandled_opcodes.add(op)
        return 2, [f"/* ${addr:06X}: unhandled PEA ${op:04X} */"], False, [], False

    def _decode_clr(self, addr, op, pc, size):
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        sz = SIZE_SUFFIX[size]

        if ea_mode == 0:
            if size == SIZE_BYTE:
                lines = [f"g_m68k.d[{ea_reg}] &= 0xFFFFFF00;"]
            elif size == SIZE_WORD:
                lines = [f"g_m68k.d[{ea_reg}] &= 0xFFFF0000;"]
            else:
                lines = [f"g_m68k.d[{ea_reg}] = 0;"]
            lines.append("g_m68k.flag_N = false; g_m68k.flag_Z = true; g_m68k.flag_V = false; g_m68k.flag_C = false;")
            return 2, lines, False, [], False

        ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, size, pc)
        pc += ext
        lines = []
        pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, size, "")
        lines.extend(pre)
        lines.append(f"{ea_write.replace('{val}', '0')};")
        lines.append("g_m68k.flag_N = false; g_m68k.flag_Z = true; g_m68k.flag_V = false; g_m68k.flag_C = false;")
        lines.extend(post)
        return pc - addr, lines, False, [], False

    def _decode_unary(self, addr, op, pc, size, op_name):
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        sz = SIZE_SUFFIX[size]

        if ea_mode == 0:
            if size == SIZE_BYTE:
                lines = [f"{{ uint8_t _tmp = (uint8_t)g_m68k.d[{ea_reg}]; M68K_{op_name}8(_tmp); "
                         f"g_m68k.d[{ea_reg}] = (g_m68k.d[{ea_reg}] & 0xFFFFFF00) | _tmp; }}"]
            elif size == SIZE_WORD:
                lines = [f"{{ uint16_t _tmp = (uint16_t)g_m68k.d[{ea_reg}]; M68K_{op_name}16(_tmp); "
                         f"g_m68k.d[{ea_reg}] = (g_m68k.d[{ea_reg}] & 0xFFFF0000) | _tmp; }}"]
            else:
                lines = [f"M68K_{op_name}32(g_m68k.d[{ea_reg}]);"]
            return 2, lines, False, [], False

        ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, size, pc)
        pc += ext
        lines = []
        pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, size, "")
        lines.extend(pre)
        lines.append(f"{{ uint{sz}_t _tmp = {ea_read}; M68K_{op_name}{sz}(_tmp); "
                     f"{ea_write.replace('{val}', '_tmp')}; }}")
        lines.extend(post)
        return pc - addr, lines, False, [], False

    def _decode_tst(self, addr, op, pc, size):
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        sz = SIZE_SUFFIX[size]

        if ea_mode == 0:
            return 2, [f"M68K_TST{sz}((uint{sz}_t)g_m68k.d[{ea_reg}]);"], False, [], False

        ea_read, _, ext, ea_info = self.decode_ea(ea_mode, ea_reg, size, pc)
        pc += ext
        lines = []
        pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, size, "")
        lines.extend(pre)
        lines.append(f"M68K_TST{sz}({ea_read});")
        lines.extend(post)
        return pc - addr, lines, False, [], False

    def _decode_movem(self, addr, op, pc):
        """MOVEM register list to/from memory."""
        direction = (op >> 10) & 1  # 0=reg-to-mem, 1=mem-to-reg
        size = SIZE_LONG if (op & 0x0040) else SIZE_WORD
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        reg_mask = self.read16(pc)
        pc += 2
        sz = SIZE_SUFFIX[size]
        nbytes = SIZE_BYTES[size]

        lines = []

        if direction == 0:  # Register to memory
            if ea_mode == 4:  # -(An) — reversed bit order
                lines.append(f"{{ uint32_t _addr = g_m68k.a[{ea_reg}];")
                for i in range(15, -1, -1):
                    if reg_mask & (1 << (15 - i)):
                        if i < 8:
                            reg_expr = f"g_m68k.d[{i}]"
                        else:
                            reg_expr = f"g_m68k.a[{i-8}]"
                        lines.append(f"  _addr -= {nbytes}; bus_write{sz}(_addr, (uint{sz}_t){reg_expr});")
                lines.append(f"  g_m68k.a[{ea_reg}] = _addr; }}")
            else:
                # Calculate base address
                if ea_mode == 5:
                    disp = self.sext16(self.read16(pc)); pc += 2
                    base = f"(g_m68k.a[{ea_reg}] + {disp})" if disp >= 0 else f"(g_m68k.a[{ea_reg}] - {-disp})"
                elif ea_mode == 7 and ea_reg == 0:
                    aval = self.sext16(self.read16(pc)) & 0xFFFFFF; pc += 2
                    base = f"0x{aval:06X}"
                elif ea_mode == 7 and ea_reg == 1:
                    aval = self.read32(pc) & 0xFFFFFF; pc += 4
                    base = f"0x{aval:06X}"
                elif ea_mode == 2:
                    base = f"g_m68k.a[{ea_reg}]"
                else:
                    base = f"g_m68k.a[{ea_reg}]"

                lines.append(f"{{ uint32_t _addr = {base};")
                for i in range(16):
                    if reg_mask & (1 << i):
                        if i < 8:
                            reg_expr = f"g_m68k.d[{i}]"
                        else:
                            reg_expr = f"g_m68k.a[{i-8}]"
                        lines.append(f"  bus_write{sz}(_addr, (uint{sz}_t){reg_expr}); _addr += {nbytes};")
                lines.append("}")
        else:  # Memory to register
            if ea_mode == 3:  # (An)+ post-increment
                base = f"g_m68k.a[{ea_reg}]"
            elif ea_mode == 5:
                disp = self.sext16(self.read16(pc)); pc += 2
                base = f"(g_m68k.a[{ea_reg}] + {disp})" if disp >= 0 else f"(g_m68k.a[{ea_reg}] - {-disp})"
            elif ea_mode == 7 and ea_reg == 0:
                aval = self.sext16(self.read16(pc)) & 0xFFFFFF; pc += 2
                base = f"0x{aval:06X}"
            elif ea_mode == 7 and ea_reg == 1:
                aval = self.read32(pc) & 0xFFFFFF; pc += 4
                base = f"0x{aval:06X}"
            elif ea_mode == 7 and ea_reg == 2:
                disp = self.sext16(self.read16(pc)); pc += 2
                base = f"0x{(pc - 2 + disp) & 0xFFFFFF:06X}"
            elif ea_mode == 2:
                base = f"g_m68k.a[{ea_reg}]"
            else:
                base = f"g_m68k.a[{ea_reg}]"

            lines.append(f"{{ uint32_t _addr = {base};")
            for i in range(16):
                if reg_mask & (1 << i):
                    if i < 8:
                        if size == SIZE_WORD:
                            lines.append(f"  g_m68k.d[{i}] = (uint32_t)(int32_t)(int16_t)bus_read16(_addr); _addr += {nbytes};")
                        else:
                            lines.append(f"  g_m68k.d[{i}] = bus_read32(_addr); _addr += {nbytes};")
                    else:
                        if size == SIZE_WORD:
                            lines.append(f"  g_m68k.a[{i-8}] = (uint32_t)(int32_t)(int16_t)bus_read16(_addr); _addr += {nbytes};")
                        else:
                            lines.append(f"  g_m68k.a[{i-8}] = bus_read32(_addr); _addr += {nbytes};")

            if ea_mode == 3:  # Update An for post-increment
                lines.append(f"  g_m68k.a[{ea_reg}] = _addr;")
            lines.append("}")

        return pc - addr, lines, False, [], False

    # ========================================================================
    # Group 5: ADDQ/SUBQ/Scc/DBcc
    # ========================================================================

    def _decode_group5(self, addr, op, pc):
        size = (op >> 6) & 3
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7

        if size == 3:
            # Scc or DBcc
            cc = (op >> 8) & 0xF
            if ea_mode == 1:
                # DBcc
                disp = self.sext16(self.read16(pc))
                target = (pc + disp) & 0xFFFFFF
                pc += 2
                cc_macro = CC_MACROS[cc]
                lines = [
                    f"if (!({cc_macro})) {{",
                    f"  int16_t _cnt = (int16_t)(uint16_t)g_m68k.d[{ea_reg}];",
                    f"  _cnt--;",
                    f"  g_m68k.d[{ea_reg}] = (g_m68k.d[{ea_reg}] & 0xFFFF0000) | (uint16_t)_cnt;",
                    f"  if (_cnt != -1) goto lbl_{target:06X};",
                    f"}}"
                ]
                return pc - addr, lines, False, [target], False
            else:
                # Scc
                cc_macro = CC_MACROS[cc]
                if ea_mode == 0:
                    lines = [f"g_m68k.d[{ea_reg}] = (g_m68k.d[{ea_reg}] & 0xFFFFFF00) | "
                             f"(({cc_macro}) ? 0xFF : 0x00);"]
                    return 2, lines, False, [], False
                ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, SIZE_BYTE, pc)
                pc += ext
                lines = []
                pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, SIZE_BYTE, "")
                lines.extend(pre)
                lines.append(f"{ea_write.replace('{val}', f'({cc_macro}) ? 0xFF : 0x00')};")
                lines.extend(post)
                return pc - addr, lines, False, [], False

        # ADDQ / SUBQ
        data = (op >> 9) & 7
        if data == 0:
            data = 8

        is_sub = (op & 0x0100) != 0
        op_name = "SUB" if is_sub else "ADD"
        sz = SIZE_SUFFIX[size]

        if ea_mode == 1:  # Address register — no flags
            if is_sub:
                return 2, [f"g_m68k.a[{ea_reg}] -= {data};"], False, [], False
            else:
                return 2, [f"g_m68k.a[{ea_reg}] += {data};"], False, [], False

        if ea_mode == 0:  # Data register
            if size == SIZE_BYTE:
                lines = [f"{{ uint8_t _tmp = (uint8_t)g_m68k.d[{ea_reg}]; M68K_{op_name}8(_tmp, {data}); "
                         f"g_m68k.d[{ea_reg}] = (g_m68k.d[{ea_reg}] & 0xFFFFFF00) | _tmp; }}"]
            elif size == SIZE_WORD:
                lines = [f"{{ uint16_t _tmp = (uint16_t)g_m68k.d[{ea_reg}]; M68K_{op_name}16(_tmp, {data}); "
                         f"g_m68k.d[{ea_reg}] = (g_m68k.d[{ea_reg}] & 0xFFFF0000) | _tmp; }}"]
            else:
                lines = [f"M68K_{op_name}32(g_m68k.d[{ea_reg}], {data});"]
            return 2, lines, False, [], False

        ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, size, pc)
        pc += ext
        lines = []
        pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, size, "")
        lines.extend(pre)
        lines.append(f"{{ uint{sz}_t _tmp = {ea_read}; M68K_{op_name}{sz}(_tmp, {data}); "
                     f"{ea_write.replace('{val}', '_tmp')}; }}")
        lines.extend(post)
        return pc - addr, lines, False, [], False

    # ========================================================================
    # Group 6: Bcc/BSR/BRA
    # ========================================================================

    def _decode_group6(self, addr, op, pc):
        cc = (op >> 8) & 0xF
        disp8 = op & 0xFF

        if disp8 == 0:
            disp = self.sext16(self.read16(pc))
            target = (pc + disp) & 0xFFFFFF
            pc += 2
        elif disp8 == 0xFF:
            disp = struct.unpack_from(">i", self.rom, pc)[0]
            target = (pc + disp) & 0xFFFFFF
            pc += 4
        else:
            disp = self.sext8(disp8)
            target = (pc + disp) & 0xFFFFFF

        total_len = pc - addr

        if cc == 0:  # BRA
            return total_len, [f"goto lbl_{target:06X};"], True, [target], False

        elif cc == 1:  # BSR
            return total_len, [
                f"g_m68k.a[7] -= 4; bus_write32(g_m68k.a[7], 0x{pc & 0xFFFFFF:06X});",
                f"genchaos_call(0x{target:06X});"
            ], False, [target], True

        else:  # Bcc
            cc_macro = CC_MACROS[cc]
            return total_len, [f"if ({cc_macro}) goto lbl_{target:06X};"], False, [target], False

    # ========================================================================
    # Group 7: MOVEQ
    # ========================================================================

    def _decode_moveq(self, addr, op, pc):
        reg = (op >> 9) & 7
        data = self.sext8(op & 0xFF)
        if data >= 0:
            lines = [f"g_m68k.d[{reg}] = 0x{data & 0xFF:02X}; M68K_TST32(g_m68k.d[{reg}]);"]
        else:
            lines = [f"g_m68k.d[{reg}] = 0x{data & 0xFFFFFFFF:08X}; M68K_TST32(g_m68k.d[{reg}]);"]
        return 2, lines, False, [], False

    # ========================================================================
    # Group 8: OR/DIV/SBCD
    # ========================================================================

    def _decode_group8(self, addr, op, pc):
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        reg = (op >> 9) & 7
        opmode = (op >> 6) & 7

        # DIVU
        if opmode == 3:
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
            pc += ext
            return pc - addr, [f"M68K_DIVU(g_m68k.d[{reg}], {ea_read});"], False, [], False

        # DIVS
        if opmode == 7:
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
            pc += ext
            return pc - addr, [f"M68K_DIVS(g_m68k.d[{reg}], {ea_read});"], False, [], False

        # SBCD
        if opmode == 4 and (ea_mode == 0 or ea_mode == 1):
            # Skip SBCD for now — rare
            return 2, [f"/* ${addr:06X}: SBCD ${op:04X} (not implemented) */"], False, [], False

        # OR
        return self._decode_alu_op(addr, op, pc, "OR")

    # ========================================================================
    # Group 9: SUB/SUBA/SUBX
    # ========================================================================

    def _decode_group9(self, addr, op, pc):
        opmode = (op >> 6) & 7
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        reg = (op >> 9) & 7

        # SUBA
        if opmode == 3:
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
            pc += ext
            return pc - addr, [f"g_m68k.a[{reg}] -= (uint32_t)(int32_t)(int16_t)({ea_read});"], False, [], False
        if opmode == 7:
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, SIZE_LONG, pc)
            pc += ext
            return pc - addr, [f"g_m68k.a[{reg}] -= {ea_read};"], False, [], False

        # SUBX
        if (opmode in [0, 1, 2]) and ea_mode in [0, 1]:
            if ea_mode == 0:
                size = opmode
                sz = SIZE_SUFFIX[size]
                if size == SIZE_BYTE:
                    return 2, [f"{{ uint8_t _tmp = (uint8_t)g_m68k.d[{reg}]; "
                               f"M68K_SUBX8(_tmp, (uint8_t)g_m68k.d[{ea_reg}]); "
                               f"g_m68k.d[{reg}] = (g_m68k.d[{reg}] & 0xFFFFFF00) | _tmp; }}"], False, [], False
                elif size == SIZE_WORD:
                    return 2, [f"{{ uint16_t _tmp = (uint16_t)g_m68k.d[{reg}]; "
                               f"M68K_SUBX16(_tmp, (uint16_t)g_m68k.d[{ea_reg}]); "
                               f"g_m68k.d[{reg}] = (g_m68k.d[{reg}] & 0xFFFF0000) | _tmp; }}"], False, [], False
                else:
                    return 2, [f"M68K_SUBX32(g_m68k.d[{reg}], g_m68k.d[{ea_reg}]);"], False, [], False

        # Regular SUB
        return self._decode_alu_op(addr, op, pc, "SUB")

    # ========================================================================
    # Group B: CMP/CMPA/EOR/CMPM
    # ========================================================================

    def _decode_groupB(self, addr, op, pc):
        opmode = (op >> 6) & 7
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        reg = (op >> 9) & 7

        # CMPA.W
        if opmode == 3:
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
            pc += ext
            return pc - addr, [f"M68K_CMP32(g_m68k.a[{reg}], (uint32_t)(int32_t)(int16_t)({ea_read}));"], False, [], False

        # CMPA.L
        if opmode == 7:
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, SIZE_LONG, pc)
            pc += ext
            return pc - addr, [f"M68K_CMP32(g_m68k.a[{reg}], {ea_read});"], False, [], False

        # CMPM (An)+, (An)+
        if ea_mode == 1 and opmode in [4, 5, 6]:
            size = opmode - 4
            sz = SIZE_SUFFIX[size]
            inc = SIZE_BYTES[size]
            lines = [
                f"{{ uint{sz}_t _s = bus_read{sz}(g_m68k.a[{ea_reg}]); g_m68k.a[{ea_reg}] += {inc};",
                f"  uint{sz}_t _d = bus_read{sz}(g_m68k.a[{reg}]); g_m68k.a[{reg}] += {inc};",
                f"  M68K_CMP{sz}(_d, _s); }}"
            ]
            return 2, lines, False, [], False

        # EOR Dn, <ea>
        if opmode in [4, 5, 6] and ea_mode != 1:
            size = opmode - 4
            sz = SIZE_SUFFIX[size]
            if ea_mode == 0:
                if size == SIZE_BYTE:
                    return 2, [f"{{ uint8_t _tmp = (uint8_t)g_m68k.d[{ea_reg}]; "
                               f"M68K_EOR8(_tmp, (uint8_t)g_m68k.d[{reg}]); "
                               f"g_m68k.d[{ea_reg}] = (g_m68k.d[{ea_reg}] & 0xFFFFFF00) | _tmp; }}"], False, [], False
                elif size == SIZE_WORD:
                    return 2, [f"{{ uint16_t _tmp = (uint16_t)g_m68k.d[{ea_reg}]; "
                               f"M68K_EOR16(_tmp, (uint16_t)g_m68k.d[{reg}]); "
                               f"g_m68k.d[{ea_reg}] = (g_m68k.d[{ea_reg}] & 0xFFFF0000) | _tmp; }}"], False, [], False
                else:
                    return 2, [f"M68K_EOR32(g_m68k.d[{ea_reg}], g_m68k.d[{reg}]);"], False, [], False

            ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, size, pc)
            pc += ext
            lines = []
            pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, size, "")
            lines.extend(pre)
            lines.append(f"{{ uint{sz}_t _tmp = {ea_read}; M68K_EOR{sz}(_tmp, (uint{sz}_t)g_m68k.d[{reg}]); "
                         f"{ea_write.replace('{val}', '_tmp')}; }}")
            lines.extend(post)
            return pc - addr, lines, False, [], False

        # CMP <ea>, Dn
        if opmode in [0, 1, 2]:
            size = opmode
            sz = SIZE_SUFFIX[size]
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, size, pc)
            pc += ext
            return pc - addr, [f"M68K_CMP{sz}((uint{sz}_t)g_m68k.d[{reg}], {ea_read});"], False, [], False

        self.unhandled_opcodes.add(op)
        return 2, [f"/* ${addr:06X}: unhandled groupB ${op:04X} */"], False, [], False

    # ========================================================================
    # Group C: AND/MUL/ABCD/EXG
    # ========================================================================

    def _decode_groupC(self, addr, op, pc):
        opmode = (op >> 6) & 7
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        reg = (op >> 9) & 7

        # MULU
        if opmode == 3:
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
            pc += ext
            return pc - addr, [f"M68K_MULU(g_m68k.d[{reg}], {ea_read});"], False, [], False

        # MULS
        if opmode == 7:
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
            pc += ext
            return pc - addr, [f"M68K_MULS(g_m68k.d[{reg}], {ea_read});"], False, [], False

        # EXG
        if opmode == 5 and ea_mode == 0:  # EXG Dx, Dy
            return 2, [f"{{ uint32_t _t = g_m68k.d[{reg}]; g_m68k.d[{reg}] = g_m68k.d[{ea_reg}]; g_m68k.d[{ea_reg}] = _t; }}"], False, [], False
        if opmode == 5 and ea_mode == 1:  # EXG Ax, Ay
            return 2, [f"{{ uint32_t _t = g_m68k.a[{reg}]; g_m68k.a[{reg}] = g_m68k.a[{ea_reg}]; g_m68k.a[{ea_reg}] = _t; }}"], False, [], False
        if opmode == 6 and ea_mode == 1:  # EXG Dx, Ay
            return 2, [f"{{ uint32_t _t = g_m68k.d[{reg}]; g_m68k.d[{reg}] = g_m68k.a[{ea_reg}]; g_m68k.a[{ea_reg}] = _t; }}"], False, [], False

        # Regular AND
        return self._decode_alu_op(addr, op, pc, "AND")

    # ========================================================================
    # Group D: ADD/ADDA/ADDX
    # ========================================================================

    def _decode_groupD(self, addr, op, pc):
        opmode = (op >> 6) & 7
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        reg = (op >> 9) & 7

        # ADDA.W
        if opmode == 3:
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
            pc += ext
            return pc - addr, [f"g_m68k.a[{reg}] += (uint32_t)(int32_t)(int16_t)({ea_read});"], False, [], False

        # ADDA.L
        if opmode == 7:
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, SIZE_LONG, pc)
            pc += ext
            return pc - addr, [f"g_m68k.a[{reg}] += {ea_read};"], False, [], False

        # ADDX
        if (opmode in [0, 1, 2]) and ea_mode in [0, 1]:
            if ea_mode == 0:
                size = opmode
                sz = SIZE_SUFFIX[size]
                if size == SIZE_BYTE:
                    return 2, [f"{{ uint8_t _tmp = (uint8_t)g_m68k.d[{reg}]; "
                               f"M68K_ADDX8(_tmp, (uint8_t)g_m68k.d[{ea_reg}]); "
                               f"g_m68k.d[{reg}] = (g_m68k.d[{reg}] & 0xFFFFFF00) | _tmp; }}"], False, [], False
                elif size == SIZE_WORD:
                    return 2, [f"{{ uint16_t _tmp = (uint16_t)g_m68k.d[{reg}]; "
                               f"M68K_ADDX16(_tmp, (uint16_t)g_m68k.d[{ea_reg}]); "
                               f"g_m68k.d[{reg}] = (g_m68k.d[{reg}] & 0xFFFF0000) | _tmp; }}"], False, [], False
                else:
                    return 2, [f"M68K_ADDX32(g_m68k.d[{reg}], g_m68k.d[{ea_reg}]);"], False, [], False

        # Regular ADD
        return self._decode_alu_op(addr, op, pc, "ADD")

    # ========================================================================
    # Group E: Shift/Rotate
    # ========================================================================

    def _decode_groupE(self, addr, op, pc):
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7

        if (op & 0xC0) == 0xC0:
            # Memory shift/rotate (word size, shift by 1)
            shift_type = (op >> 9) & 3
            direction = (op >> 8) & 1  # 0=right, 1=left
            shift_names_r = ["ASR", "LSR", "ROXR", "ROR"]
            shift_names_l = ["LSL", "LSL", "ROXL", "ROL"]  # ASL == LSL
            if direction:
                name = shift_names_l[shift_type]
            else:
                name = shift_names_r[shift_type]

            # Handle ROXL/ROXR inline for memory
            if name == "ROXL":
                ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
                pc += ext
                lines = []
                pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, SIZE_WORD, "")
                lines.extend(pre)
                lines.append(f"{{ uint16_t _d = {ea_read}; bool _msb = (_d & 0x8000) != 0; "
                             f"_d = (_d << 1) | (g_m68k.flag_X ? 1 : 0); "
                             f"g_m68k.flag_X = g_m68k.flag_C = _msb; g_m68k.flag_V = false; "
                             f"m68k_update_nz16(_d); {ea_write.replace('{val}', '_d')}; }}")
                lines.extend(post)
                return pc - addr, lines, False, [], False
            if name == "ROXR":
                ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
                pc += ext
                lines = []
                pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, SIZE_WORD, "")
                lines.extend(pre)
                lines.append(f"{{ uint16_t _d = {ea_read}; bool _lsb = (_d & 1) != 0; "
                             f"_d = (_d >> 1) | (g_m68k.flag_X ? 0x8000 : 0); "
                             f"g_m68k.flag_X = g_m68k.flag_C = _lsb; g_m68k.flag_V = false; "
                             f"m68k_update_nz16(_d); {ea_write.replace('{val}', '_d')}; }}")
                lines.extend(post)
                return pc - addr, lines, False, [], False

            # Map to genrecomp macros
            macro = f"M68K_{name}16"
            ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, SIZE_WORD, pc)
            pc += ext
            lines = []
            pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, SIZE_WORD, "")
            lines.extend(pre)
            lines.append(f"{{ uint16_t _tmp = {ea_read}; {macro}(_tmp, 1); "
                         f"{ea_write.replace('{val}', '_tmp')}; }}")
            lines.extend(post)
            return pc - addr, lines, False, [], False

        # Register shift/rotate
        size = (op >> 6) & 3
        sz = SIZE_SUFFIX[size]
        ir = (op >> 5) & 1  # 0=immediate count, 1=register count
        direction = (op >> 8) & 1
        shift_type = (op >> 3) & 3
        count_reg = (op >> 9) & 7
        data_reg = op & 7

        shift_names_r = ["ASR", "LSR", "ROXR", "ROR"]
        shift_names_l = ["LSL", "LSL", "ROXL", "ROL"]  # ASL == LSL on 68000
        if direction:
            name = shift_names_l[shift_type]
        else:
            name = shift_names_r[shift_type]

        if ir:
            count_expr = f"g_m68k.d[{count_reg}]"
        else:
            count = count_reg if count_reg != 0 else 8
            count_expr = str(count)

        # Handle ROXL/ROXR inline (rotate through extend)
        if name == "ROXL":
            lines = [f"/* ROXL */"]
            if size == SIZE_BYTE:
                lines.append(f"{{ uint8_t _d = (uint8_t)g_m68k.d[{data_reg}]; uint8_t _cnt = (uint8_t)({count_expr}) & 63;")
                lines.append(f"  for (uint8_t _i = 0; _i < _cnt; _i++) {{ bool _msb = (_d & 0x80) != 0; _d = (_d << 1) | (g_m68k.flag_X ? 1 : 0); g_m68k.flag_X = g_m68k.flag_C = _msb; }}")
                lines.append(f"  g_m68k.flag_V = false; m68k_update_nz8(_d); g_m68k.d[{data_reg}] = (g_m68k.d[{data_reg}] & 0xFFFFFF00) | _d; }}")
            elif size == SIZE_WORD:
                lines.append(f"{{ uint16_t _d = (uint16_t)g_m68k.d[{data_reg}]; uint8_t _cnt = (uint8_t)({count_expr}) & 63;")
                lines.append(f"  for (uint8_t _i = 0; _i < _cnt; _i++) {{ bool _msb = (_d & 0x8000) != 0; _d = (_d << 1) | (g_m68k.flag_X ? 1 : 0); g_m68k.flag_X = g_m68k.flag_C = _msb; }}")
                lines.append(f"  g_m68k.flag_V = false; m68k_update_nz16(_d); g_m68k.d[{data_reg}] = (g_m68k.d[{data_reg}] & 0xFFFF0000) | _d; }}")
            else:
                lines.append(f"{{ uint32_t _d = g_m68k.d[{data_reg}]; uint8_t _cnt = (uint8_t)({count_expr}) & 63;")
                lines.append(f"  for (uint8_t _i = 0; _i < _cnt; _i++) {{ bool _msb = (_d & 0x80000000u) != 0; _d = (_d << 1) | (g_m68k.flag_X ? 1u : 0u); g_m68k.flag_X = g_m68k.flag_C = _msb; }}")
                lines.append(f"  g_m68k.flag_V = false; m68k_update_nz32(_d); g_m68k.d[{data_reg}] = _d; }}")
            return 2, lines, False, [], False

        if name == "ROXR":
            lines = [f"/* ROXR */"]
            if size == SIZE_BYTE:
                lines.append(f"{{ uint8_t _d = (uint8_t)g_m68k.d[{data_reg}]; uint8_t _cnt = (uint8_t)({count_expr}) & 63;")
                lines.append(f"  for (uint8_t _i = 0; _i < _cnt; _i++) {{ bool _lsb = (_d & 1) != 0; _d = (_d >> 1) | (g_m68k.flag_X ? 0x80 : 0); g_m68k.flag_X = g_m68k.flag_C = _lsb; }}")
                lines.append(f"  g_m68k.flag_V = false; m68k_update_nz8(_d); g_m68k.d[{data_reg}] = (g_m68k.d[{data_reg}] & 0xFFFFFF00) | _d; }}")
            elif size == SIZE_WORD:
                lines.append(f"{{ uint16_t _d = (uint16_t)g_m68k.d[{data_reg}]; uint8_t _cnt = (uint8_t)({count_expr}) & 63;")
                lines.append(f"  for (uint8_t _i = 0; _i < _cnt; _i++) {{ bool _lsb = (_d & 1) != 0; _d = (_d >> 1) | (g_m68k.flag_X ? 0x8000 : 0); g_m68k.flag_X = g_m68k.flag_C = _lsb; }}")
                lines.append(f"  g_m68k.flag_V = false; m68k_update_nz16(_d); g_m68k.d[{data_reg}] = (g_m68k.d[{data_reg}] & 0xFFFF0000) | _d; }}")
            else:
                lines.append(f"{{ uint32_t _d = g_m68k.d[{data_reg}]; uint8_t _cnt = (uint8_t)({count_expr}) & 63;")
                lines.append(f"  for (uint8_t _i = 0; _i < _cnt; _i++) {{ bool _lsb = (_d & 1) != 0; _d = (_d >> 1) | (g_m68k.flag_X ? 0x80000000u : 0u); g_m68k.flag_X = g_m68k.flag_C = _lsb; }}")
                lines.append(f"  g_m68k.flag_V = false; m68k_update_nz32(_d); g_m68k.d[{data_reg}] = _d; }}")
            return 2, lines, False, [], False

        macro = f"M68K_{name}{sz}"

        if size == SIZE_BYTE:
            return 2, [f"{{ uint8_t _tmp = (uint8_t)g_m68k.d[{data_reg}]; "
                       f"{macro}(_tmp, {count_expr}); "
                       f"g_m68k.d[{data_reg}] = (g_m68k.d[{data_reg}] & 0xFFFFFF00) | _tmp; }}"], False, [], False
        elif size == SIZE_WORD:
            return 2, [f"{{ uint16_t _tmp = (uint16_t)g_m68k.d[{data_reg}]; "
                       f"{macro}(_tmp, {count_expr}); "
                       f"g_m68k.d[{data_reg}] = (g_m68k.d[{data_reg}] & 0xFFFF0000) | _tmp; }}"], False, [], False
        else:
            return 2, [f"{macro}(g_m68k.d[{data_reg}], {count_expr});"], False, [], False

    # ========================================================================
    # Generic ALU operation decoder (ADD/SUB/AND/OR with EA)
    # ========================================================================

    def _decode_alu_op(self, addr, op, pc, op_name):
        opmode = (op >> 6) & 7
        ea_mode = (op >> 3) & 7
        ea_reg = op & 7
        reg = (op >> 9) & 7

        if opmode <= 2:
            # <ea> op Dn -> Dn
            size = opmode
            sz = SIZE_SUFFIX[size]
            ea_read, _, ext, _ = self.decode_ea(ea_mode, ea_reg, size, pc)
            pc += ext

            if size == SIZE_BYTE:
                return pc - addr, [f"{{ uint8_t _tmp = (uint8_t)g_m68k.d[{reg}]; "
                                   f"M68K_{op_name}8(_tmp, {ea_read}); "
                                   f"g_m68k.d[{reg}] = (g_m68k.d[{reg}] & 0xFFFFFF00) | _tmp; }}"], False, [], False
            elif size == SIZE_WORD:
                return pc - addr, [f"{{ uint16_t _tmp = (uint16_t)g_m68k.d[{reg}]; "
                                   f"M68K_{op_name}16(_tmp, {ea_read}); "
                                   f"g_m68k.d[{reg}] = (g_m68k.d[{reg}] & 0xFFFF0000) | _tmp; }}"], False, [], False
            else:
                return pc - addr, [f"M68K_{op_name}32(g_m68k.d[{reg}], {ea_read});"], False, [], False

        elif opmode >= 4 and opmode <= 6:
            # Dn op <ea> -> <ea>
            size = opmode - 4
            sz = SIZE_SUFFIX[size]
            ea_read, ea_write, ext, ea_info = self.decode_ea(ea_mode, ea_reg, size, pc)
            pc += ext
            lines = []
            pre, post = self._ea_side_effects(ea_mode, ea_reg, ea_info, size, "")
            lines.extend(pre)
            if ea_write is None:
                lines.append(f"/* ${addr:06X}: {op_name} to read-only EA */")
            else:
                lines.append(f"{{ uint{sz}_t _tmp = {ea_read}; "
                             f"M68K_{op_name}{sz}(_tmp, (uint{sz}_t)g_m68k.d[{reg}]); "
                             f"{ea_write.replace('{val}', '_tmp')}; }}")
            lines.extend(post)
            return pc - addr, lines, False, [], False

        self.unhandled_opcodes.add(op)
        return 2, [f"/* ${addr:06X}: unhandled {op_name} mode ${op:04X} */"], False, [], False

    # ========================================================================
    # Recursive Descent Code Discovery
    # ========================================================================

    def discover_code(self):
        """Find all reachable code using recursive descent from entry points."""
        # Parse vector table
        entry_pc = self.read32(4)
        vblank = self.read32(0x78)  # Vector 30
        hblank = self.read32(0x70)  # Vector 28

        print(f"  Entry point: ${entry_pc:06X}")
        print(f"  VBlank handler: ${vblank:06X}")
        print(f"  HBlank handler: ${hblank:06X}")

        # Seed with entry points
        self.func_entries.add(entry_pc)
        self.pending.append(entry_pc)
        if vblank < self.rom_size:
            self.func_entries.add(vblank)
            self.pending.append(vblank)
        if hblank < self.rom_size and hblank != vblank:
            self.func_entries.add(hblank)
            self.pending.append(hblank)

        # Also check other vectors
        for i in range(2, 64):
            vec = self.read32(i * 4)
            if vec > 0x200 and vec < self.rom_size and vec not in self.func_entries:
                self.func_entries.add(vec)
                self.pending.append(vec)

        # Add manually-discovered hot-path functions from runtime testing
        # These are addresses the interpreter handles frequently that should
        # be recompiled for performance.
        hot_path_addrs = [
            0x0DF826,  # Z80/sound driver init — called every VBlank
            # Note: $0E0150 and $0DFDEA are NOT seeded as separate functions
            # because they form a DBcc loop. $0E0150 branches back to
            # $0DFDEA which is inside func_0DFDE2. Seeding them as
            # separate entries would create cross-function call loops.
        ]
        for addr in hot_path_addrs:
            if 0x200 <= addr < self.rom_size and addr not in self.func_entries:
                self.func_entries.add(addr)
                self.pending.append(addr)
                print(f"  Hot-path seed: ${addr:06X}")

        # Scan for jump tables and subroutine calls throughout the ROM
        self._scan_jump_tables()
        self._scan_all_calls()

        # Recursive descent
        pass_num = 0
        while self.pending:
            pass_num += 1
            addr = self.pending.pop(0)

            if addr in self.visited:
                continue
            if addr >= self.rom_size or addr < 0x200:
                continue
            if addr & 1:  # Odd address — not valid code
                continue

            self._trace_block(addr)

        print(f"  Discovery complete: {len(self.func_entries)} functions, "
              f"{len(self.code_addrs)} instruction addresses")

    def _scan_jump_tables(self):
        """Scan ROM for JMP d8(PC,Xn) patterns and extract jump table targets."""
        tables_found = 0
        targets_found = 0

        for addr in range(0x200, self.rom_size - 4, 2):
            op = self.read16(addr)

            # JMP d8(PC,Xn) = $4EFB
            if op == 0x4EFB:
                ext = self.read16(addr + 2)
                disp = self.sext8(ext & 0xFF)
                xreg = (ext >> 12) & 7
                # Only handle the common case: JMP 2(PC,D0.W) or similar
                # where the table immediately follows the instruction
                table_base = (addr + 2 + disp) & 0xFFFFFF
                if table_base < self.rom_size and table_base >= 0x200:
                    entries = self._read_word_offset_table(table_base, max_entries=30)
                    if len(entries) >= 2:
                        self.jump_tables[addr] = entries
                        tables_found += 1
                        for target in entries:
                            if target not in self.func_entries and target not in self.visited:
                                self.branch_targets.add(target)
                                if target not in self.pending:
                                    self.pending.append(target)
                                targets_found += 1

        print(f"  Jump tables: {tables_found} tables, {targets_found} new code targets")

        # Also scan for longword pointer tables (common for JSR (An) dispatch)
        # Look for sequences of 4+ valid code pointers
        ltables = 0
        ltargets = 0
        addr = 0x200
        while addr < self.rom_size - 16:
            # Check if this could be a longword table
            count = 0
            for i in range(30):
                ptr = self.read32(addr + i * 4)
                if 0x200 <= ptr < self.rom_size and (ptr & 1) == 0:
                    tgt_op = self.read16(ptr)
                    tgt_group = (tgt_op >> 12) & 0xF
                    if tgt_group != 0xA and tgt_group != 0xF:
                        count += 1
                    else:
                        break
                else:
                    break
            if count >= 6:
                # Verify this isn't inside known code
                if addr not in self.code_addrs:
                    for i in range(count):
                        ptr = self.read32(addr + i * 4)
                        if ptr not in self.func_entries and ptr not in self.visited:
                            self.func_entries.add(ptr)
                            if ptr not in self.pending:
                                self.pending.append(ptr)
                            ltargets += 1
                    ltables += 1
                    addr += count * 4
                    continue
            addr += 2

        if ltables:
            print(f"  Pointer tables: {ltables} tables, {ltargets} new function entries")

    def _read_word_offset_table(self, table_base, max_entries=30):
        """Read a table of word offsets from table_base, returning absolute targets."""
        entries = []
        seen = set()
        for i in range(max_entries):
            offset_addr = table_base + i * 2
            if offset_addr + 1 >= self.rom_size:
                break
            offset = self.sext16(self.read16(offset_addr))
            target = (table_base + offset) & 0xFFFFFF
            # Validate: target should be in ROM, word-aligned, and look like code
            if target < 0x200 or target >= self.rom_size or (target & 1):
                break
            # Check the opcode at target isn't obviously data
            target_op = self.read16(target)
            group = (target_op >> 12) & 0xF
            if group == 0xA or group == 0xF:  # Line-A or Line-F
                break
            entries.append(target)
            if target in seen and len(entries) > 2:
                # Repeated entry — likely end of table or valid repeat
                pass
            seen.add(target)
        return entries

    def _scan_all_calls(self):
        """Scan the entire ROM for JSR and BSR instructions to find all subroutine targets."""
        targets_found = 0
        for addr in range(0x200, self.rom_size - 6, 2):
            op = self.read16(addr)
            pc = addr + 2

            # JSR xxx.L ($4EB9)
            if op == 0x4EB9:
                target = self.read32(pc) & 0xFFFFFF
                if 0x200 <= target < self.rom_size and (target & 1) == 0:
                    if target not in self.func_entries:
                        self.func_entries.add(target)
                        if target not in self.pending:
                            self.pending.append(target)
                            targets_found += 1

            # JSR xxx.W ($4EB8)
            elif op == 0x4EB8:
                target = self.sext16(self.read16(pc)) & 0xFFFFFF
                if 0x200 <= target < self.rom_size and (target & 1) == 0:
                    if target not in self.func_entries:
                        self.func_entries.add(target)
                        if target not in self.pending:
                            self.pending.append(target)
                            targets_found += 1

            # JSR d16(PC) ($4EBA)
            elif op == 0x4EBA:
                disp = self.sext16(self.read16(pc))
                target = (pc + disp) & 0xFFFFFF
                if 0x200 <= target < self.rom_size and (target & 1) == 0:
                    if target not in self.func_entries:
                        self.func_entries.add(target)
                        if target not in self.pending:
                            self.pending.append(target)
                            targets_found += 1

            # BSR.W ($6100)
            elif op == 0x6100:
                disp = self.sext16(self.read16(pc))
                target = (pc + disp) & 0xFFFFFF
                if 0x200 <= target < self.rom_size and (target & 1) == 0:
                    if target not in self.func_entries:
                        self.func_entries.add(target)
                        if target not in self.pending:
                            self.pending.append(target)
                            targets_found += 1

            # BSR.B ($61xx, xx != 00 and xx != FF)
            elif (op & 0xFF00) == 0x6100 and (op & 0xFF) not in (0x00, 0xFF):
                disp = self.sext8(op & 0xFF)
                target = (pc + disp) & 0xFFFFFF
                if 0x200 <= target < self.rom_size and (target & 1) == 0:
                    if target not in self.func_entries:
                        self.func_entries.add(target)
                        if target not in self.pending:
                            self.pending.append(target)
                            targets_found += 1

        print(f"  Full ROM call scan: {targets_found} new subroutine targets")

        if self.unhandled_opcodes:
            print(f"  Unhandled opcodes: {len(self.unhandled_opcodes)}")
            for op in sorted(self.unhandled_opcodes)[:20]:
                print(f"    ${op:04X}")

    def _trace_block(self, start_addr):
        """Trace a linear block of code starting at start_addr."""
        addr = start_addr

        while addr < self.rom_size:
            if addr in self.visited:
                break
            self.visited.add(addr)
            self.code_addrs.add(addr)

            length, lines, is_terminal, targets, is_call = self.decode_insn(addr)
            self.instructions[addr] = (length, lines, is_terminal, targets, is_call)

            for t in targets:
                if is_call:
                    self.call_targets.add(t)
                    self.func_entries.add(t)
                    if t not in self.visited and t not in self.pending:
                        self.pending.append(t)
                else:
                    self.branch_targets.add(t)
                    if t not in self.visited and t not in self.pending:
                        self.pending.append(t)

            if is_terminal:
                break

            addr += length

    # ========================================================================
    # Function Boundary Detection
    # ========================================================================

    def build_functions(self):
        """Group instructions into functions based on entry points and boundaries."""
        # Sort all code addresses
        all_addrs = sorted(self.code_addrs)
        if not all_addrs:
            return

        # Assign each code address to its nearest preceding function entry
        func_list = sorted(self.func_entries)

        # Build a map: func_entry -> [addrs in this function]
        func_code = defaultdict(list)

        for a in all_addrs:
            # Find which function this belongs to
            best_func = None
            for f in func_list:
                if f <= a:
                    best_func = f
                else:
                    break
            if best_func is not None:
                func_code[best_func].append(a)

        # Filter out function entries that fall inside decoded instructions
        # (false positives from the ROM call scan)
        bad_entries = set()
        for addr, (length, _, _, _, _) in self.instructions.items():
            if length > 2:
                for offset in range(2, length, 2):
                    mid = addr + offset
                    if mid in self.func_entries and mid != addr:
                        bad_entries.add(mid)

        if bad_entries:
            print(f"  Filtered {len(bad_entries)} mid-instruction false function entries")
            for bad in bad_entries:
                self.func_entries.discard(bad)
                if bad in func_code:
                    del func_code[bad]

        self.func_bounds = {}
        for entry, addrs in func_code.items():
            if entry not in bad_entries:
                self.func_bounds[entry] = sorted(addrs)

        print(f"  Built {len(self.func_bounds)} function boundaries")

    # ========================================================================
    # C Code Generation
    # ========================================================================

    def generate_c(self, output_dir):
        """Generate C source files from disassembled functions."""
        os.makedirs(output_dir, exist_ok=True)

        # Clean up old generated files
        import glob
        for old_file in glob.glob(os.path.join(output_dir, "recomp_*.c")):
            os.remove(old_file)

        func_list = sorted(self.func_bounds.keys())
        total_funcs = len(func_list)

        # Split into multiple files to avoid monster compilation units
        funcs_per_file = 200
        file_idx = 0
        func_idx = 0

        all_files = []
        header_lines = []

        while func_idx < total_funcs:
            chunk = func_list[func_idx:func_idx + funcs_per_file]
            filename = f"recomp_{file_idx:03d}.c"
            filepath = os.path.join(output_dir, filename)
            all_files.append(filename)

            with open(filepath, "w") as f:
                f.write(f"/* General Chaos — Recompiled Code (chunk {file_idx}) */\n")
                f.write(f"/* Auto-generated by genchaos recompiler — DO NOT EDIT */\n")
                f.write(f"/* Original game (C) 1993 Game Refuge Inc. / Electronic Arts */\n\n")
                f.write('#include <genrecomp/genrecomp.h>\n')
                f.write('#include "genchaos.h"\n')
                f.write('#include "interp.h"\n\n')

                # Forward declarations for functions in this chunk
                for entry in chunk:
                    f.write(f"void func_{entry:06X}(void);\n")
                f.write("\n")

                for entry in chunk:
                    self._write_function(f, entry)
                    header_lines.append(f"void func_{entry:06X}(void);")

            print(f"  Wrote {filepath} ({len(chunk)} functions)")
            func_idx += funcs_per_file
            file_idx += 1

        # Write header with all forward declarations
        header_path = os.path.join(output_dir, "..", "..", "include", "genchaos.h")
        os.makedirs(os.path.dirname(header_path), exist_ok=True)
        with open(header_path, "w") as f:
            f.write("/* General Chaos — Recompiled Function Declarations */\n")
            f.write("/* Auto-generated — DO NOT EDIT */\n\n")
            f.write("#ifndef GENCHAOS_H\n")
            f.write("#define GENCHAOS_H\n\n")
            f.write("#include <genrecomp/genrecomp.h>\n\n")
            f.write(f"#define GENCHAOS_NUM_FUNCS {total_funcs}\n\n")
            for line in header_lines:
                f.write(line + "\n")
            f.write("\n/* Register all recompiled functions with the function table */\n")
            f.write("void genchaos_register_all(void);\n\n")
            f.write("#endif /* GENCHAOS_H */\n")

        # Write registration function
        reg_path = os.path.join(output_dir, "recomp_register.c")
        with open(reg_path, "w") as f:
            f.write("/* General Chaos — Function Registration */\n")
            f.write("/* Auto-generated — DO NOT EDIT */\n\n")
            f.write('#include "genchaos.h"\n\n')
            f.write("void genchaos_register_all(void) {\n")
            for entry in func_list:
                f.write(f"    func_table_register(0x{entry:06X}, func_{entry:06X});\n")
            f.write("}\n")

        all_files.append("recomp_register.c")

        print(f"\n  Total: {total_funcs} functions in {file_idx} source files")
        print(f"  Header: {header_path}")

        return all_files

    def _write_function(self, f, entry):
        """Write a single recompiled function to file."""
        addrs = self.func_bounds.get(entry, [])
        if not addrs:
            f.write(f"void func_{entry:06X}(void) {{ /* empty */ }}\n\n")
            return

        addr_set = set(addrs)

        f.write(f"/* ${entry:06X} */\n")
        f.write(f"void func_{entry:06X}(void) {{\n")

        for a in addrs:
            if a not in self.instructions:
                continue

            length, lines, is_terminal, targets, is_call = self.instructions[a]

            # Emit label if this address is a branch target
            if a in self.branch_targets or a == entry:
                f.write(f"lbl_{a:06X}:\n")

            # Fix cross-function gotos: if a goto targets an address outside
            # this function, replace it with func_table_call + return
            fixed_lines = []
            for line in lines:
                if "goto lbl_" in line:
                    # Extract target address from goto
                    import re
                    m = re.search(r'goto lbl_([0-9A-Fa-f]{6})', line)
                    if m:
                        target_addr = int(m.group(1), 16)
                        if target_addr not in addr_set:
                            # Cross-function jump — use func_table_call
                            if "if (" in line:
                                cond = line[:line.index("goto")]
                                fixed_lines.append(f"{cond}{{ genchaos_call(0x{target_addr:06X}); return; }}")
                            else:
                                fixed_lines.append(f"genchaos_call(0x{target_addr:06X}); return;")
                            continue
                fixed_lines.append(line)

            # Emit the C code with address comment
            for i, line in enumerate(fixed_lines):
                if i == 0:
                    f.write(f"    {line} /* ${a:06X} */\n")
                else:
                    f.write(f"    {line}\n")

        # Safety: ensure function always returns
        if addrs:
            last = addrs[-1]
            if last in self.instructions:
                length, _, is_term, _, _ = self.instructions[last]
                if not is_term:
                    # Check if the next address is a known function entry
                    # If so, tail-call it to maintain flow continuity
                    next_addr = last + length
                    if next_addr in self.func_entries:
                        f.write(f"    genchaos_call(0x{next_addr:06X}); /* fall through */ \n")
                    f.write("    return;\n")

        f.write("}\n\n")


# ============================================================================
# Main
# ============================================================================

def main():
    if len(sys.argv) < 3:
        print("Usage: python recompile.py <rom_file> <output_dir>")
        print("  rom_file:   Path to General Chaos ROM (.gen/.md/.bin)")
        print("  output_dir: Directory for generated C files")
        sys.exit(1)

    rom_path = sys.argv[1]
    output_dir = sys.argv[2]

    print("=" * 60)
    print("  GENERAL CHAOS — Static Recompiler")
    print("  Original game by Brian Colin & Jeff Nauman")
    print("  Game Refuge Inc. / Electronic Arts, 1993")
    print("=" * 60)
    print()

    # Load ROM
    with open(rom_path, "rb") as f:
        rom_data = f.read()

    print(f"ROM: {rom_path} ({len(rom_data)} bytes)")

    # Parse header
    header = rom_data[0x100:0x200]
    game_name = header[32:80].decode("ascii", errors="replace").strip()
    copyright = header[16:32].decode("ascii", errors="replace").strip()
    print(f"Game: {game_name}")
    print(f"Copyright: {copyright}")
    print()

    # Disassemble
    print("Phase 1: Recursive descent code discovery...")
    dis = Disassembler(rom_data)
    dis.discover_code()
    print()

    print("Phase 2: Building function boundaries...")
    dis.build_functions()
    print()

    print("Phase 3: Generating C code...")
    files = dis.generate_c(output_dir)
    print()

    print("=" * 60)
    print("  Recompilation complete!")
    print(f"  Generated files in: {output_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
