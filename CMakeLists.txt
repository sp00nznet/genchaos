# ============================================================================
# General Chaos — Static Recompilation Build
# ============================================================================
# A love letter to the 1993 Genesis classic.
# Original game by Brian Colin & Jeff Nauman, Game Refuge Inc. / EA.
#
# This project uses genrecomp to provide Genesis hardware (VDP, YM2612,
# PSG, Z80, I/O) while the game's M68K code runs as native C.
# ============================================================================

cmake_minimum_required(VERSION 3.16)
project(genchaos C)

set(CMAKE_C_STANDARD 17)

# SDL2 is required for windowing, audio, and input
find_package(SDL2 REQUIRED)

# Pull in the genrecomp hardware backend library
# (adjust path if your genrecomp checkout is elsewhere)
set(GENRECOMP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../genrecomp"
    CACHE PATH "Path to genrecomp source directory")

if(NOT EXISTS "${GENRECOMP_DIR}/CMakeLists.txt")
    message(FATAL_ERROR
        "genrecomp not found at ${GENRECOMP_DIR}\n"
        "Set GENRECOMP_DIR to the path of your genrecomp checkout.")
endif()

add_subdirectory("${GENRECOMP_DIR}" genrecomp_build)

# ============================================================================
# Collect recompiled source files
# ============================================================================
file(GLOB RECOMPILED_SOURCES
    "${CMAKE_CURRENT_SOURCE_DIR}/src/recompiled/*.c"
)

# ============================================================================
# Main executable
# ============================================================================
add_executable(genchaos
    src/main.c
    src/interp.c
    ${RECOMPILED_SOURCES}
)

target_include_directories(genchaos PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
)

target_link_libraries(genchaos PRIVATE
    genrecomp
    SDL2::SDL2main
)

# MSVC: use console subsystem so we get printf output
if(MSVC)
    set_target_properties(genchaos PROPERTIES
        LINK_FLAGS "/SUBSYSTEM:CONSOLE"
    )
    # Suppress some noisy warnings in generated code
    target_compile_options(genchaos PRIVATE
        /W3 /wd4244 /wd4267 /wd4146 /wd4018
    )
else()
    target_compile_options(genchaos PRIVATE
        -Wall -Wno-unused-label -Wno-unused-variable
    )
endif()

# ============================================================================
# Install
# ============================================================================
install(TARGETS genchaos RUNTIME DESTINATION bin)

message(STATUS "")
message(STATUS "  ============================================")
message(STATUS "  |     G E N E R A L   C H A O S           |")
message(STATUS "  |     Static Recompilation Build           |")
message(STATUS "  ============================================")
message(STATUS "")
message(STATUS "  genrecomp: ${GENRECOMP_DIR}")
message(STATUS "  Recompiled sources: ${CMAKE_CURRENT_SOURCE_DIR}/src/recompiled/")
message(STATUS "")
