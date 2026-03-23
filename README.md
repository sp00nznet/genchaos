# General Chaos — Native Recompilation

```
  ============================================
  |     G E N E R A L   C H A O S           |
  |                                          |
  |        "IT'S WAR, IT'S FUNNY,            |
  |         AND NOBODY DIES!"                |
  ============================================
```

**A love letter to the 1993 Sega Genesis classic.**

This project statically recompiles *General Chaos* from its original Motorola 68000
machine code into native C, then links it against real Genesis hardware emulation
for pixel-perfect fidelity. No interpreter loop. No JIT. Just raw, recompiled chaos
running at full native speed.

## The Game

**General Chaos** was created by **Brian Colin** and **Jeff Nauman** at
**Game Refuge Inc.**, published by **Electronic Arts** in August 1993.

It's a squad-based strategy game where two goofy generals — General Chaos and
General Havoc — wage cartoon war across increasingly absurd battlefields. You
pick a squad of 5 specialists (or 3 tough commandos), choose your theater of
war, and watch the glorious pandemonium unfold.

The genius of General Chaos is its accessibility. While other strategy games
drowned players in menus and stats, Brian Colin designed something anyone could
pick up: point at the bad guys and let the rockets fly. The frantic 2-player
versus mode was legendary among Genesis owners — pure couch warfare.

### The Revival That Never Was

In the 2010s, Brian Colin attempted to revive General Chaos through crowdfunding
and various initiatives. Despite genuine fan enthusiasm and Colin's infectious
passion for the franchise, the revival never quite materialized. The gaming
landscape had changed, and bringing back a niche Genesis title proved
harder than storming General Havoc's beach fortress.

This recompilation project doesn't presume to fill that gap. It's just a fan
saying: *this game mattered, it was special, and it deserves to run on
modern hardware without fiddling with emulator settings.*

## How It Works

```
  Original M68K ROM (1993)
         |
         v
  [Static Recompiler]     tools/recompile.py
         |                   - Recursive descent disassembly
         |                   - M68K -> C code generation
         v                   - 1,408 functions, 46,000+ instructions
  Native C Functions
         |
         v
  [genrecomp]              Genesis hardware backend
         |                   - Genesis Plus GX VDP rendering
         |                   - YM2612 FM synthesis
         |                   - SN76489 PSG audio
         |                   - Z80 sound driver
         v                   - Controller I/O
  Native Executable        Pure native code, ~60 FPS

  Stats: 1,408 functions | 62,000+ instructions | 92K lines of C
         71 jump tables | 608 pointer tables | full ROM call scan
```

Every M68K instruction in the original ROM has been converted to equivalent C
code using genrecomp's hardware macros. When the recompiled code writes to VDP
registers, the write goes through Genesis Plus GX's real VDP implementation.
When it triggers YM2612 FM voices, you get real FM synthesis. The Z80 sound
driver runs alongside the recompiled M68K code just like on original hardware.

**The M68K CPU is not emulated.** Your native code *is* the CPU.

## Building

### Prerequisites

- **CMake** 3.16+
- **SDL2** development libraries
- **C17 compiler** (MSVC 2019+, GCC 10+, Clang 11+)
- **Python 3** (only needed to re-run the recompiler)
- **vcpkg** (recommended for SDL2 on Windows)

### Quick Start

```bash
# Clone the repo
git clone https://github.com/sp00nznet/genchaos.git
cd genchaos

# Place your General Chaos ROM in the rom/ directory
# (The ROM is not included — you'll need your own copy)
mkdir rom
cp /path/to/your/General\ Chaos\ \(USA,\ Europe\).gen rom/

# Re-run the recompiler (optional — generated code is committed)
python tools/recompile.py "rom/General Chaos (USA, Europe).gen" src/recompiled

# Build (Windows with vcpkg)
cmake -B build -G "Visual Studio 17 2022" -A x64 \
  -DCMAKE_TOOLCHAIN_FILE=C:/vcpkg/scripts/buildsystems/vcpkg.cmake
cmake --build build --config Release

# Build (Linux/macOS)
cmake -B build
cmake --build build

# Run!
./build/Release/genchaos "rom/General Chaos (USA, Europe).gen"
```

## Controls

| Key        | Genesis Button |
|------------|---------------|
| Arrow Keys | D-Pad         |
| Z          | A             |
| X          | B             |
| C          | C             |
| Enter      | Start         |
| Escape     | Quit          |

## Project Structure

```
genchaos/
├── CMakeLists.txt          Build configuration
├── README.md               You are here
├── tools/
│   └── recompile.py        M68K → C static recompiler
├── docs/
│   └── game_analysis.md    ROM structure, code regions, state machines
├── src/
│   ├── main.c              Game harness (init, main loop, shutdown)
│   └── recompiled/         Generated C files (1,408 functions)
│       ├── recomp_000.c    Functions chunk 0
│       ├── recomp_001.c    Functions chunk 1
│       ├── ...             (5 source files total)
│       └── recomp_register.c  Function table registration
├── include/
│   └── genchaos.h          Generated function declarations
└── rom/                    Your ROM goes here (gitignored)
```

## Credits

### Original Game
- **Brian Colin** — Game Designer, Artist
- **Jeff Nauman** — Lead Programmer
- **Game Refuge Inc.** — Development Studio
- **Electronic Arts** — Publisher
- Released August 1993 for Sega Genesis / Mega Drive

### This Recompilation
- Built with [genrecomp](https://github.com/sp00nznet/genrecomp) — Genesis hardware backend
- Powered by [Genesis Plus GX](https://github.com/ekeeke/Genesis-Plus-GX) — VDP, YM2612, PSG, Z80
- Static recompiler written in Python

## Legal

This project is a static recompilation tool and game harness. It does **not**
include any copyrighted ROM data. You must supply your own legally obtained
copy of General Chaos to use this project.

General Chaos is (C) 1993 Game Refuge Inc. / Electronic Arts.
All rights reserved by their respective owners.

This is a non-commercial fan project created out of love for the game.

---

*"You don't need a plan. You need more grenades."*
— General Chaos, probably
