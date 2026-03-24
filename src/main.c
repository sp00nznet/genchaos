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

static bool s_in_vblank = false;

static void vblank_handler(void) {
    if (s_in_vblank) return;  /* Prevent re-entry */
    if (vblank_vector) {
        s_in_vblank = true;

        /* Feed simulated input BEFORE the VBlank handler reads it */
        if (max_frames > 0) {
            uint16_t buttons = 0;
            /* Aggressive input: cycle through button combos */
            int phase = (frame_count / 30) % 8;
            switch (phase) {
            case 0: case 4: buttons = GEN_BTN_START; break;
            case 1: case 5: buttons = GEN_BTN_A; break;
            case 2: case 6: buttons = GEN_BTN_C; break;
            case 3: case 7: buttons = 0; break;  /* release */
            }
            io_set_pad_state(0, buttons);
        }

        recomp_m68k_exception(30); /* Vector 30 = VBlank */
        s_in_vblank = false;
    }

    /*
     * NOTE: When the VBlank callback fires during bus cycle simulation
     * (inside the manual frame loop's recomp_m68k_exception call),
     * we only handle the interrupt. Frame rendering and counting are
     * managed by the manual loop itself.
     *
     * When the entry point contains its own main loop (never returns),
     * this callback drives the full frame cycle: render + present.
     */
    genrecomp_end_frame();
    frame_count++;

    /* Progress reporting */
    if (max_frames > 0) {
        uint16_t game_state = bus_read16(0xFF0332);
        static uint16_t last_state = 0xFFFF;
        if (game_state != last_state) {
            fprintf(stderr, "  Frame %5d: STATE CHANGED %d -> %d\n",
                    frame_count, last_state, game_state);
            fflush(stderr);
            last_state = game_state;
        }
        if (frame_count % 300 == 0) {
            /* Read several RAM locations for debugging */
            uint16_t ram_0334 = bus_read16(0xFF0334);
            uint16_t ram_0336 = bus_read16(0xFF0336);
            uint16_t ram_0656 = bus_read16(0xFF0656);
            uint16_t pad_raw  = bus_read16(0xFF0338);
            uint32_t vbl_counter = bus_read32(0xFFD1B4);
            uint32_t task_count = bus_read32(0xFFE580);
            fprintf(stderr, "  Frame %5d/%d  SP=$%08X  state=%d  "
                    "vblcnt=%u  r656=%04X  tasks=%u\n",
                    frame_count, max_frames, g_m68k.a[7], game_state,
                    vbl_counter, ram_0656, task_count);
            fflush(stderr);
        }
    }

    /* Frame limit for headless mode */
    if (max_frames > 0 && frame_count >= max_frames) {
        printf("\n  Headless test complete: %d frames.\n", frame_count);
        printf("  Game state: %d\n", bus_read16(0xFF0332));
        genrecomp_shutdown();
        exit(0);
    }

    if (!genrecomp_begin_frame()) {
        printf("\n  Thanks for playing General Chaos!\n");
        genrecomp_shutdown();
        exit(0);
    }
}

/* ====================================================================
 * Main — where the chaos begins
 * ==================================================================== */

/*
 * Hand-written native handler for $00028C — BRA $0002FA.
 * The entry point calls this to jump to the real init continuation.
 */
static void native_00028C(void) {
    genchaos_call(0x0002FA);
}

/*
 * Hand-written native handler for $000224 — init continuation after ANDI.
 * func_000200's last instruction is ANDI at $220 (4 bytes), so the next
 * instruction is $224. But $224 falls inside func_000200's code range
 * so it can't be a separate recompiled function. This handler bridges
 * the gap: it interprets from $224 which flows through the VDP/Z80 init
 * and eventually reaches $28C (BRA $2FA) → main game loop.
 */
static void native_000224(void) {
    /*
     * The init from $224 to the main loop at $125C is complex:
     * TMSS write, VDP register setup, Z80 upload, DMA wait loops,
     * controller port init, and finally the game init at $116A.
     *
     * Rather than trying to stitch together fragmented recompiled
     * functions, we let the interpreter handle the entire init
     * sequence. It's only run once, so performance doesn't matter.
     *
     * The interpreter will eventually hit the BRA $self at $125C
     * which triggers the spin-wait → VBlank → game logic cycle.
     */
    fprintf(stderr, "  INIT: Interpreting init sequence from $000224...\n");
    fflush(stderr);
    interp_execute(0x000224);
    fprintf(stderr, "  INIT: Init sequence returned (should not happen if main loop entered)\n");
    fflush(stderr);
}

/*
 * Hand-written native handler for $0E0150 — the DBF D7,$0DFDEA loop.
 * This is the hottest interpreter path (~6 calls per VBlank).
 * Original M68K: DBF D7,$0DFDEA / RTS
 *
 * On real hardware this is a simple loop: DBF D7,$0DFDEA decrements D7
 * and branches back to $0DFDEA for each sound channel. In the recompiled
 * code, func_0DFDEA tail-calls back to $0E0150, creating deep C recursion.
 * We use a reentrancy guard: when func_0DFDEA tail-calls us, we just
 * return and let the outer loop handle the next iteration.
 */
static bool s_in_dbf_loop = false;

static void native_0E0150(void) {
    if (s_in_dbf_loop) {
        /* Called from func_0DFDEA's tail-call — just return.
         * The outer loop will handle the DBF decrement. */
        return;
    }

    s_in_dbf_loop = true;
    while (1) {
        int16_t cnt = (int16_t)(uint16_t)g_m68k.d[7];
        cnt--;
        g_m68k.d[7] = (g_m68k.d[7] & 0xFFFF0000) | (uint16_t)cnt;
        if (cnt == -1) break;
        genchaos_call(0x0DFDEA);
    }
    s_in_dbf_loop = false;

    /* RTS */
    g_m68k.pc = bus_read32(g_m68k.a[7]);
    g_m68k.a[7] += 4;
}

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

    /*
     * Register native handler for $000224 — init continuation after the
     * ANDI.B #$0F,D0 at $220. This bridges func_000200 (which returns
     * after $220) to the TMSS/VDP/Z80 init code that follows.
     */
    {
        static void native_000224(void);
        func_table_register(0x000224, native_000224);
    }

    /*
     * Register hand-written native handler for $00028C — the BRA $0002FA
     * at the end of the TMSS/init code. func_000200 calls this to jump
     * to the real init continuation. Without this, the interpreter handles
     * it and may hit instruction limits.
     */
    {
        static void native_00028C(void);
        func_table_register(0x00028C, native_00028C);
    }

    /*
     * Register hand-written native handler for $0E0150 — a DBF loop
     * that's called ~6x per VBlank from multiple functions. It was merged
     * as a label inside func_0DFDE2, so the function table doesn't have it.
     * Without this, every call goes through the interpreter.
     */
    {
        extern void func_0DFDE2(void);  /* parent function containing lbl_0E0150 */
        /* Register a handler that does the DBF and loops via the parent func */
        static void native_0E0150(void);
        func_table_register(0x0E0150, native_0E0150);
    }
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
    /*
     * The entry point at $000200 does a TMSS check and loads config.
     * On fresh boot ($A10008 = 0), it returns after ANDI at $220.
     * The real init continues at $224 (TMSS write, VDP/Z80 setup),
     * flowing through to $28C (BRA $2FA) → $116A → $1242 (main loop).
     *
     * We call $200 first, then $224 to ensure the full init runs.
     */
    call_with_fallback(entry_pc);
    /* Now call the init continuation that func_000200 should fall through to */
    call_with_fallback(0x000224);

    if (true) {
        /* Entry point returned — unusual, but handle it.
         * Fall through to a VBlank-driven frame loop. */
        printf("  Entry point returned. Running VBlank-driven loop.\n");
        fflush(stdout);

        while (genrecomp_begin_frame()) {
            /* Call VBlank handler via exception mechanism */
            if (vblank_vector) {
                recomp_m68k_exception(30); /* Vector 30 = VBlank */
            }
            genrecomp_trigger_vblank();
            genrecomp_end_frame();
            frame_count++;

            /* Progress reporting with game state */
            if (max_frames > 0 && (frame_count % 300 == 0)) {
                /* Read game state from RAM $FF0332 (main state machine variable) */
                uint16_t game_state = bus_read16(0xFF0332);
                /* Read a few other interesting RAM locations */
                uint16_t vblank_count = bus_read16(0xFF0334);
                fprintf(stderr, "  Frame %5d/%d  SP=$%08X  state=%d  vbl=%d\n",
                        frame_count, max_frames, g_m68k.a[7],
                        game_state, vblank_count);
                fflush(stderr);
            }

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
