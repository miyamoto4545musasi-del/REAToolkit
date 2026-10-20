cmake_minimum_required(VERSION 3.22)
project(reatk_host C)

# libretro.h is not vendored here. Fetch it from the libretro-common repository
# (or your core's source tree) and drop it next to this file before building.
add_library(reatk_host SHARED reatk_host.c)
target_include_directories(reatk_host PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(reatk_host dl log android aaudio)
