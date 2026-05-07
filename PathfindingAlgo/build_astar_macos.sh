#!/usr/bin/env bash
set -euo pipefail

echo "Building libastar.dylib with clang++..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

clang++ -O2 -std=c++17 -dynamiclib -fPIC -o libastar.dylib astar.cpp

echo "Build successful: libastar.dylib"

