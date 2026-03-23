# General Chaos — Code Analysis

Technical analysis of the General Chaos ROM structure, discovered through
static recompilation and code tracing.

## ROM Overview

| Field | Value |
|-------|-------|
| System | Sega Genesis / Mega Drive |
| ROM Size | 1,048,576 bytes (1 MB) |
| Copyright | (C) T-50 1993.AUG |
| Serial | GM T-50626 -00 |
| Region | UEJ (USA, Europe, Japan) |
| Publisher | Electronic Arts (T-50) |
| Developer | Game Refuge Inc. |

## Vector Table

| Vector | Address | Purpose |
|--------|---------|---------|
| Initial SSP | `$FFFFFFFE` | Stack pointer (top of RAM) |
| Entry Point | `$000200` | Game initialization |
| VBlank (Vec 30) | `$01F9A4` | Vertical blank interrupt handler |
| HBlank (Vec 28) | `$01FA60` | Horizontal blank interrupt (minimal) |
| Default Handler | `$01FA64` | All other exceptions point here |

## Code Structure

The recompiler discovered **940 functions** containing **46,282 instructions**
across the 1MB ROM, organized into clear functional regions:

### Memory Map (Code Regions)

```
$000000-$00FFFF  System init, main loop, state machine, low-level drivers
$010000-$01FFFF  Hardware setup, VBlank handler, interrupt management
$020000-$03FFFF  Menu system, UI rendering, font/text engine
$040000-$04FFFF  Team selection, squad configuration
$050000-$07FFFF  Graphics engine, sprite management, DMA routines
$080000-$09FFFF  Animation system, character state machines
$0A0000-$0AFFFF  Sound driver interface, music/SFX triggers
$0B0000-$0BFFFF  Battle engine core (140 subroutines — densest region)
$0C0000-$0CFFFF  Battle AI, pathfinding, combat resolution
$0D0000-$0DFFFF  Level/battlefield data, map rendering (135 subroutines)
$0E0000-$0EFFFF  Cutscenes, victory/defeat screens, story sequences
$0F0000-$0FFFFF  Data tables, lookup tables, ROM utilities
```

### Main State Machine

The game uses a classic state-machine architecture with a jump table at
`$002002`, dispatched via `JMP 2(PC,D0.W)` at `$001FFE`. The state variable
(likely in RAM) indexes into this table to drive the main game flow:

| State | Handler | Purpose (estimated) |
|-------|---------|---------------------|
| 0 | `$002072` | Active game state |
| 4-5 | `$002062` | Menu / team select |
| 7, 13 | `$00206A` | Transition state |
| 6, 18 | `$002072` | In-game / battle |
| Others | `$00208C` | Default / idle |

### Battle Engine

The battle engine at `$0B9322` is the heart of General Chaos. It uses a 20-state
machine (with more states in sub-tables) to manage the chaotic battlefield:

- States 0-3: Unit initialization and spawn
- States 4-7: Movement and pathfinding
- States 8-11: Attack execution and collision
- States 12-15: Damage resolution and reactions
- States 16-19: Death, respawn, and cleanup

The battle region (`$0B0000-$0BFFFF`) contains 140 subroutines — more than
any other region — reflecting the game's combat-focused design.

### Jump Tables Found

The recompiler identified **71 jump tables** (PC-relative word-offset tables)
containing **903 code targets**. Additionally, **608 pointer tables** (longword
address tables) were found with **640 function entries**. These are used for:

- State machine dispatch (main loop, battle, AI)
- Animation frame selection
- Menu navigation
- Sound effect indexing
- Level/map selection

### VBlank Handler

The VBlank interrupt handler at `$01F9A4` is lightweight:
1. Saves registers
2. Calls the VBlank service routine at `$01FD0C`
3. Restores registers and returns (RTE)

The VBlank service routine handles:
- VDP register updates (scroll, palette)
- DMA transfers (sprite table, tile data)
- Controller input polling
- Frame counter increment
- Sound driver tick

## Recompilation Statistics

| Metric | Value |
|--------|-------|
| Functions discovered | 1,139 |
| Instructions recompiled | 62,230 |
| Lines of C generated | ~80,000 |
| Jump tables detected | 71 |
| Pointer tables detected | 608 |
| Fall-through chains | 225 |
| False entries filtered | 269 |
| Source files generated | 6 + 1 register file |
| Interpreter fallback | Yes (handles ~30 instruction types) |
| Native executable size | ~3.5 MB |

## Original Creators

**General Chaos** was designed by **Brian Colin**, known for his earlier arcade
hits *Rampage* (1986) and *Xenophobe* (1987). Jeff Nauman served as lead
programmer, implementing the real-time squad control system that gave the game
its signature frantic feel.

The game was developed at **Game Refuge Inc.**, Colin's studio, and published by
**Electronic Arts** under their EA Sports label (despite not being a traditional
sports game — EA saw the team-based gameplay as sports-adjacent).

Brian Colin attempted to revive the franchise in the 2010s, but the project
never materialized despite fan enthusiasm. The game remains a beloved cult
classic among Genesis enthusiasts.
