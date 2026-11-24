#!/bin/sh
# Build the synapse_lock binary with g++ directly (no CMake needed).
# Run inside WSL/Linux from anywhere:   sh src/cpp/build_lock.sh
# Output: src/cpp/build/synapse_lock  (next to synapse_engine)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HERE/build"
g++ -std=c++17 -O2 -Wall -Wextra -pthread \
    -I "$HERE/include" \
    "$HERE/src/lock_main.cpp" "$HERE/src/lock_manager.cpp" "$HERE/src/file_lock.cpp" \
    -o "$HERE/build/synapse_lock"
echo "built $HERE/build/synapse_lock"
