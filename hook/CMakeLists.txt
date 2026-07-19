cmake_minimum_required(VERSION 3.15)
project(gbfr_hook C)

set(CMAKE_C_STANDARD 11)

add_library(gbfr_hook SHARED gbfr_hook.c)

target_link_libraries(gbfr_hook PRIVATE user32)

set_target_properties(gbfr_hook PROPERTIES
    OUTPUT_NAME "gbfr_hook"
    PREFIX ""
)
