cmake_minimum_required(VERSION 3.16)
project(synapse_engine CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O3 -Wall -Wextra -pthread")

find_package(OpenSSL REQUIRED)

add_executable(synapse_engine
    src/main.cpp
    src/cas.cpp
    src/chunker.cpp
    src/sha256.cpp
)

target_include_directories(synapse_engine PRIVATE include)

target_link_libraries(synapse_engine PRIVATE OpenSSL::Crypto stdc++fs)
