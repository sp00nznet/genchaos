cmake_minimum_required(VERSION 3.16)
project(genchaos C)

# genrecomp: the Genesis hardware runtime and recompiler, beside this repo
set(GENRECOMP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../genrecomp" CACHE PATH "genrecomp checkout")
if(NOT EXISTS "${GENRECOMP_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "genrecomp not found at ${GENRECOMP_DIR}. Run Setup.cmd, or clone it there.")
endif()
add_subdirectory(${GENRECOMP_DIR} genrecomp_build)

# src/recomp is generated from your own ROM and never committed (README,
# Getting Started): genrecomp's tools/recompiler/generate.py with recomp.json
file(GLOB RECOMP_SOURCES src/recomp/*.c)
if(NOT RECOMP_SOURCES)
    message(FATAL_ERROR "src/recomp/ is empty. Generate it from your ROM first:\n"
        "  python ../genrecomp/tools/recompiler/generate.py \"path/to/General Chaos.gen\" -o src/recomp -c recomp.json\n"
        "or run Setup.cmd, which does every step.")
endif()

add_executable(genchaos src/main.c ${RECOMP_SOURCES})
target_link_libraries(genchaos PRIVATE genrecomp)
target_include_directories(genchaos PRIVATE src/)
