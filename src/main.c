/*
 * General Chaos — Native Static Recompilation
 * =============================================
 * A love letter to the 1993 Genesis classic by Brian Colin & Jeff Nauman.
 *
 * Original game: (C) 1993 Game Refuge Inc. / Electronic Arts
 * This project: Static recompilation using genrecomp.
 *
 * General Chaos is a beautifully chaotic squad-based strategy game where
 * you command a team of wacky soldiers in over-the-top military mayhem.
 * Pick your squad of 5 (or 3 commandos), choose your battlefield, and
 * unleash glorious cartoon violence upon your opponent.
 *
 * No emulation. No interpretation. Just pure, recompiled chaos.
 */

#include <genrecomp/genrecomp.h>
#include "genchaos.h"
#include "interp.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

/* ====================================================================
 * Function call miss tracking
 *
 * When the game calls an address we didn't recompile, we log it.
 * This helps identify missing code paths for future recompiler runs.
 * ==================================================================== */

#define MAX_MISS_LOG 256
static uint32_t s_miss_log[MAX_MISS_LOG];
static int s_miss_count = 0;

static void log_miss(uint32_t addr) {
    /* Check if already logged */
    for (int i = 0; i < s_miss_count; i++) {
        if (s_miss_log[i] == addr) return;
    }
    if (s_miss_count < MAX_MISS_LOG) {
        s_miss_log[s_miss_count++] = addr;
    }
}

/*
 * Enhanced function call: try recompiled code first, fall back to interpreter.
 * This is used for the entry point and VBlank handler where we need
 * the interpreter fallback. The recompiled code itself still uses
 * func_table_call directly (which is fast but doesn't have fallback).
 */
static bool call_with_fallback(uint32_t addr) {
    if (func_table_call(addr)) return true;
    log_miss(addr);
    return interp_execute(addr);
}

/* ====================================================================
 * VBlank handler — the heartbeat of the game
 *
 * On real Genesis hardware, VBlank fires as a level-6 interrupt at
 * ~60Hz (NTSC). The game's VBlank ISR handles DMA transfers, scroll
 * updates, palette changes, input polling, and sound driver ticks.
 *
 * In our recompilation, the bus cycle simulation fires this callback
 * when enough cycles have accumulated for v_counter to cross the
 * VBlank boundary. This keeps the game's timing-dependent code
 * working correctly.
 * ==================================================================== */

static uint32_t vblank_vector = 0;
static int frame_count = 0;

static int max_frames = 0;  /* 0 = unlimited */

static void vblank_handler(void) {
    if (vblank_vector) {
        call_with_fallback(vblank_vector);
    }

    /* Render and present this frame (if genrecomp is initialized) */
    genrecomp_end_frame();
    frame_count++;

    /* Check frame limit (headless mode) */
    if (max_frames > 0 && frame_count >= max_frames) {
        printf("\n  Headless test complete: %d frames.\n", frame_count);
        if (s_miss_count > 0) {
            printf("  %d unique function addresses were not recompiled:\n", s_miss_count);
            for (int i = 0; i < s_miss_count && i < 30; i++) {
                printf("    $%06X\n", s_miss_log[i]);
            }
        } else {
            printf("  All function calls resolved successfully!\n");
        }
        genrecomp_shutdown();
        exit(0);
    }

    /* Start next frame (poll input, reset cycle counters) */
    if (!genrecomp_begin_frame()) {
        /* User requested quit — exit the game */
        printf("\n  Ran %d frames. Thanks for playing!\n", frame_count);
        printf("  A Brian Colin / Jeff Nauman production.\n\n");
        if (s_miss_count > 0) {
            printf("  %d unique function addresses were not recompiled.\n", s_miss_count);
        }
        genrecomp_shutdown();
        exit(0);
    }
}

/* ====================================================================
 * Main — where the chaos begins
 * ==================================================================== */

int main(int argc, char *argv[]) {
    const char *rom_path = NULL;
    int scale = 3;  /* 960x672 default window */

    (void)argc; (void)argv;

    printf("\n");
    printf("  ============================================\n");
    printf("  |     G E N E R A L   C H A O S           |\n");
    printf("  |     Static Recompilation Project         |\n");
    printf("  |                                          |\n");
    printf("  |  Original: Brian Colin & Jeff Nauman     |\n");
    printf("  |  Game Refuge Inc. / Electronic Arts      |\n");
    printf("  |  1993 — Sega Genesis / Mega Drive        |\n");
    printf("  ============================================\n");
    printf("\n");

    int headless_frames = 0;  /* 0 = normal, >0 = headless test mode */

    /* Parse command line */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--scale") == 0 && i + 1 < argc) {
            scale = atoi(argv[++i]);
            if (scale < 1) scale = 1;
            if (scale > 8) scale = 8;
        } else if (strcmp(argv[i], "--headless") == 0 && i + 1 < argc) {
            headless_frames = atoi(argv[++i]);
            if (headless_frames < 1) headless_frames = 60;
        } else if (argv[i][0] != '-') {
            rom_path = argv[i];
        }
    }

    if (headless_frames > 0) {
        printf("  HEADLESS MODE: running %d frames without display\n\n", headless_frames);
    }

    /* Find ROM path from args or default location */
    if (!rom_path) {
        static const char *rom_paths[] = {
            "rom/General Chaos (USA, Europe).gen",
            "General Chaos (USA, Europe).gen",
            "generalchaos.gen",
            "genchaos.gen",
            NULL
        };
        for (int i = 0; rom_paths[i]; i++) {
            FILE *f = fopen(rom_paths[i], "rb");
            if (f) {
                fclose(f);
                rom_path = rom_paths[i];
                break;
            }
        }
    }

    if (!rom_path) {
        fprintf(stderr, "No ROM file found!\n");
        fprintf(stderr, "Usage: genchaos [--scale N] [rom_file]\n");
        fprintf(stderr, "  Place your General Chaos ROM in the rom/ directory.\n");
        return 1;
    }

    printf("  ROM:   %s\n", rom_path);
    printf("  Scale: %dx (%dx%d window)\n\n", scale, 320 * scale, 224 * scale);

    /* Initialize genrecomp (creates SDL2 window + Genesis hardware) */
    if (headless_frames > 0) {
        /* In headless mode, suppress audio to avoid device issues */
#ifdef _WIN32
        _putenv("SDL_AUDIODRIVER=dummy");
#else
        setenv("SDL_AUDIODRIVER", "dummy", 1);
#endif
    }

    if (!genrecomp_init("General Chaos — A Brian Colin Production", scale)) {
        fprintf(stderr, "Failed to initialize genrecomp!\n");
        fprintf(stderr, "(If running headless, ensure a display/GPU is available)\n");
        return 1;
    }

    /* Load the ROM */
    if (!genrecomp_load_rom(rom_path)) {
        fprintf(stderr, "Failed to load ROM: %s\n", rom_path);
        genrecomp_shutdown();
        return 1;
    }

    /* Register all recompiled functions */
    genchaos_register_all();
    printf("  Registered %d recompiled functions\n", GENCHAOS_NUM_FUNCS);

    /* Read vector table */
    uint32_t entry_pc = bus_read32(0x000004);
    vblank_vector = bus_read32(0x000078);

    printf("  Entry:  $%06X\n", entry_pc);
    printf("  VBlank: $%06X\n", vblank_vector);
    printf("\n");
    printf("  Controls: Arrows=D-pad  Z/X/C=A/B/C  Enter=Start  Esc=Quit\n");
    printf("\n");
    printf("  LET THE CHAOS BEGIN!\n\n");
    fflush(stdout);

    /* ================================================================
     * Set up the VBlank callback
     *
     * The game's main loop lives INSIDE the entry point. It never
     * returns. The game polls VDP status in tight loops waiting for
     * VBlank. Our bus cycle simulation advances v_counter on each
     * memory access, and when VBlank is reached, this callback fires.
     *
     * The callback:
     *   1. Calls the game's VBlank ISR (DMA, scroll, sound tick)
     *   2. Renders the frame via genrecomp_end_frame()
     *   3. Starts the next frame via genrecomp_begin_frame()
     *
     * This gives us proper 60Hz frame-locked rendering driven by the
     * game's own control flow, exactly like real hardware.
     * ================================================================ */
    bus_set_vblank_callback(vblank_handler);
    max_frames = headless_frames;

    /* Start the first frame */
    genrecomp_begin_frame();

    /*
     * Run the entry point. On a real Genesis, the M68K starts executing
     * from the reset vector and runs forever. The game's init code sets
     * up VDP, loads palettes and tiles, then enters the main game loop.
     *
     * The main loop typically:
     *   1. Waits for VBlank (polling VDP status bit 3)
     *   2. Processes game logic for this frame
     *   3. Updates VDP registers and prepares DMA
     *   4. Loops back to step 1
     *
     * Since the entry point contains this infinite loop, this call
     * should never return. Frame rendering happens inside the VBlank
     * callback whenever the bus simulation detects VBlank timing.
     */
    if (call_with_fallback(entry_pc)) {
        /* Entry point returned — unusual, but handle it.
         * Fall through to a VBlank-driven frame loop. */
        printf("  Entry point returned. Running VBlank-driven loop.\n");
        fflush(stdout);

        while (genrecomp_begin_frame()) {
            /* Call VBlank handler directly if registered */
            if (vblank_vector) {
                call_with_fallback(vblank_vector);
            }
            genrecomp_trigger_vblank();
            genrecomp_end_frame();
            frame_count++;

            if (max_frames > 0 && frame_count >= max_frames) {
                printf("\n  Headless test complete: %d frames.\n", frame_count);
                if (s_miss_count > 0) {
                    printf("  %d unique function addresses were not recompiled:\n", s_miss_count);
                    for (int i = 0; i < s_miss_count && i < 30; i++) {
                        printf("    $%06X\n", s_miss_log[i]);
                    }
                } else {
                    printf("  All function calls resolved!\n");
                }
                break;
            }
        }
    } else {
        fprintf(stderr, "  Entry point $%06X could not be executed!\n", entry_pc);
        fprintf(stderr, "  (Neither recompiled code nor interpreter succeeded)\n");
    }

    printf("\n  Ran %d frames. Thanks for playing!\n", frame_count);
    printf("  A Brian Colin / Jeff Nauman production.\n\n");

    genrecomp_shutdown();
    return 0;
}
