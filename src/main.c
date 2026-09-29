/*
 * General Chaos -- statically recompiled.
 *
 * The game is compiled C (EA/Game Refuge, 1993). Its main loop runs inside
 * the reset vector's code and never returns; it waits for frames by polling
 * the VBlank counter its level-6 handler increments. So the frame is driven
 * from genrecomp's scanline clock: at VBlank it calls genchaos_vblank(),
 * which raises the game's VBlank interrupt and presents the frame.
 * Everything else is title-agnostic: entry, stack and VBlank come from the
 * ROM's vector table. How this title was brought up: docs/debugging.md.
 */

#include <genrecomp/genrecomp.h>
#include <genrecomp/bus.h>
#include <genrecomp/input.h>
#include <genrecomp/platform.h>
#include <genrecomp/func_table.h>
#include "recomp/recomp_funcs.h"
#include <stdio.h>
#include <stdlib.h>

static int s_frame_count = 0;

static void genchaos_vblank(void) {
    /* Input first, so the handler reads fresh pad state */
    if (!genrecomp_begin_frame()) {
        printf("\nWindow closed, exiting.\n");
        genrecomp_shutdown();
        exit(0);
    }
    genrecomp_vblank_irq();   /* the game's level-6 handler, context saved */
    genrecomp_end_frame();

    s_frame_count++;
    /* GENCHAOS_STACK=1: where is the main thread, every second */
    if (getenv("GENCHAOS_STACK") && s_frame_count % 60 == 0) {
        printf("Frame %d (SP=$%08X) ", s_frame_count, g_m68k.a[7]);
        func_table_dump_stack(stdout);
        fflush(stdout);
    }
}

int main(int argc, char *argv[]) {
    argc = platform_parse_args(argc, argv);

    printf("General Chaos -- Static Recompilation\n\n");
    if (!genrecomp_init("General Chaos (Recompiled)", 3)) {
        fprintf(stderr, "Failed to initialize genrecomp\n");
        return 1;
    }

    const char *rom_path = (argc > 1) ? argv[1] : "General Chaos (USA, Europe).gen";
    if (!genrecomp_load_rom(rom_path)) {
        fprintf(stderr, "Failed to load ROM: %s\n", rom_path);
        return 1;
    }

    recomp_register_all();
    bus_set_vblank_callback(genchaos_vblank);

    /* Reset: stack and entry from the vector table, interrupts masked */
    g_m68k.a[7] = bus_read32(0);
    g_m68k.pc = bus_read32(4);
    m68k_set_sr(0x2700);

    /* Through the dispatcher, so the entry code's tail jumps are taken */
    func_table_call(g_m68k.pc);

    printf("The game's entry point returned unexpectedly\n");
    genrecomp_shutdown();
    return 0;
}
