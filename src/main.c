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
 * This recompilation converts the original M68K machine code into native
 * C that runs on modern hardware with full Genesis accuracy — real VDP
 * rendering, YM2612 FM synthesis, and SN76489 PSG audio.
 *
 * No emulation. No interpretation. Just pure, recompiled chaos.
 */

#include <genrecomp/genrecomp.h>
#include "genchaos.h"
#include <stdio.h>
#include <string.h>

/* ====================================================================
 * VBlank handler — called when the display reaches vertical blank.
 * The original game's VBlank ISR lives at the address stored in the
 * vector table at offset 0x78 (vector 30).
 * ==================================================================== */

static uint32_t vblank_vector = 0;

static void vblank_handler(void) {
    if (vblank_vector) {
        func_table_call(vblank_vector);
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

    /* Find ROM path from args or default location */
    if (argc > 1) {
        rom_path = argv[1];
    } else {
        /* Try common locations */
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
        fprintf(stderr, "Usage: genchaos [rom_file]\n");
        fprintf(stderr, "  Place your General Chaos ROM in the rom/ directory.\n");
        return 1;
    }

    printf("  ROM: %s\n\n", rom_path);

    /* Initialize genrecomp (creates SDL2 window + Genesis hardware) */
    if (!genrecomp_init("General Chaos", scale)) {
        fprintf(stderr, "Failed to initialize genrecomp!\n");
        return 1;
    }

    /* Load the ROM — this populates the Genesis memory map */
    if (!genrecomp_load_rom(rom_path)) {
        fprintf(stderr, "Failed to load ROM: %s\n", rom_path);
        genrecomp_shutdown();
        return 1;
    }

    /* Register all recompiled functions */
    genchaos_register_all();
    printf("  Registered %d recompiled functions\n", GENCHAOS_NUM_FUNCS);

    /* Read vector table from loaded ROM */
    uint32_t entry_pc = bus_read32(0x000004);
    vblank_vector = bus_read32(0x000078);

    printf("  Entry point:    $%06X\n", entry_pc);
    printf("  VBlank handler: $%06X\n", vblank_vector);
    printf("\n");
    printf("  Controls:\n");
    printf("    Arrow keys  = D-pad\n");
    printf("    Z           = A button\n");
    printf("    X           = B button\n");
    printf("    C           = C button\n");
    printf("    Enter       = Start\n");
    printf("    Escape      = Quit\n");
    printf("\n");
    printf("  LET THE CHAOS BEGIN!\n\n");

    /* Set up VBlank callback for bus cycle simulation */
    bus_set_vblank_callback(vblank_handler);

    /* Initialize CPU state */
    g_m68k.ssp = bus_read32(0x000000);
    g_m68k.a[7] = g_m68k.ssp;
    g_m68k.pc = entry_pc;
    g_m68k.flag_S = true;
    g_m68k.int_mask = 7;

    /* Run the entry point (game initialization) */
    func_table_call(entry_pc);

    /* Main game loop */
    while (genrecomp_begin_frame()) {
        /* The game's main loop is driven by VBlank interrupts.
         * genrecomp_end_frame() renders all scanlines and triggers
         * the VBlank callback at the appropriate time. */
        genrecomp_trigger_vblank();
        genrecomp_end_frame();
    }

    printf("  Thanks for playing General Chaos!\n");
    printf("  A Brian Colin / Jeff Nauman production.\n\n");

    genrecomp_shutdown();
    return 0;
}
