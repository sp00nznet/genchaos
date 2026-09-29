# Bringing up General Chaos

General Chaos was the first compiled-C title on genrecomp (Pigskin Footbrawl
is hand-written assembly), and most of what it needed went into the toolkit:
genrecomp#6 (how calls, returns and interrupts behave) and #7 (the shared
recompiler). The general workflow is in genrecomp `docs/recompiler.md`; this
file is the title-specific record, verified against the reference runner.

## What the game is like inside

- **Compiled C with a C runtime.** Functions use `link a6` and read stack
  arguments at `8(a6)`. The runtime at `$0FE000`-`$0FF400` has `memset`
  (`$0FF228`, a Duff's-device store loop entered through a `switch`),
  `qsort` (`$0FE314`; the game's comparators return second minus first, so
  sorts are descending) and 32-bit divide/modulo helpers that pass their
  continuation on the stack (`pea cont(pc); bra div`).
- **Main loop in C**, never returning. It waits for frames with
  `move.l $FFD1B4,d0; loop: cmp.l $FFD1B4,d0; beq loop` (`$020F56`); the
  level-6 handler at `$01F9A4` increments `$FFD1B4`.
- **Controllers** are read in the VBlank handler through the EA 4-Way Play
  protocol (selecting players by writing `$A10005`) into `$FFD1B8`-`$FFD1BB`.
- **Sound commands** go through a table of 108 long offsets at `$0DFA50`
  relative to `$0DF2A4`, dispatched at `$0DFA2A`. That table is the
  `offset_tables` entry in `recomp.json`.
- **Game mode** is the word at `$FFFF0306`, set to 1 after the credits and
  overwritten by the main menu's choice; the menu (`$0C4C5E`) times out to
  the Boot Camp demo after 1250 frames.

## How it got in game

| Symptom | Cause | Fixed in |
|---|---|---|
| Garbage arguments everywhere | JSR didn't push a return address | genrecomp#6 |
| `jmp (a0)` to garbage from the divide | PEA-pushed continuation skipped on RTS | genrecomp#6 |
| Credits → War Room, skipping the menu | Interrupt clobbered flags mid-compare; ran while masked | genrecomp#6 |
| 165 untranslated `link.w` | Sized mnemonic never matched `'link'` | genrecomp#7 |
| Calls to `$0012E8`, `$0FF07A`... missing | Targets only in data | `recomp.json` seeds |
| `RTS to $000000` | An address-table "entry" inside a `muls` corrupted a loop count, which overran a stack buffer | genrecomp#7 |
| Jumps into the middle of routines | Compiled `switch` tables not recognised | genrecomp#7 |
| One missing sound routine per run | Offset dispatch table | `recomp.json` `offset_tables` |

The `RTS to $000000` hunt used the watchpoint: `GENRECOMP_WATCH=FFFEE1`
showed qsort's swap loop writing over the return address, qsort was being
called with 194 elements for an 80-element buffer, and the count came from
a loop whose `muls.w #6,d0` had been decoded as `ori.b #$c0,d6`.

## Known issues

- Some mid-function return points are logged by genrecomp as
  `RTS to ... has no function; returning` during battles. The game carries
  on; each is a candidate for a seed.
- Loading is faster than on hardware (register-only work costs no simulated
  time), so screens change a little earlier than on the reference runner.
- Audio is mixed but hasn't been listened to.
