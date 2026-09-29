# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Needs
genrecomp#6 and #7 (genrecomp `master` from b390007).

## [Unreleased]

### Added
- In game: menus, War Room, map, squad selection, battles, Battle Report.
- `recomp.json`: 16 seeds and the sound-command offset table, for genrecomp's
  shared recompiler.
- `src/main.c`, title-agnostic: entry, stack and VBlank from the vector
  table; headless runs, recording and scripted input via genrecomp.
- `Setup.cmd`: checks prerequisites, validates your ROM, generates, builds,
  smoke-tests, and leaves `Play General Chaos.cmd`.
- `LICENSE`, `ROADMAP.md`, `docs/debugging.md`, screenshots.

### Removed
- The hand-written 68K decoder (`tools/recompile.py`), the interpreter
  fallback (`src/interp.c`) and hand-patched generated source: replaced by
  genrecomp's recompiler. Generated source and headers are no longer in the
  repo or its history.
- `docs/game_analysis.md`: its figures described the old generator and
  several descriptions were guesses; `docs/debugging.md` has what was
  verified.
- `CLAUDE.md` from the repo and its history.

## Earlier work - 2026-03 (untagged)

A first recompiler and interpreter fallback; the game ran its init and main
loop without showing anything past the EA logo.
