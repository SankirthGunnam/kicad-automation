# Building the A* Router DLL

The A* routing algorithm is implemented in C++ and compiled as a Windows DLL (`astar.dll`) for significantly better performance than the pure Python implementation.

## Prerequisites

### Install MSYS2

Download and install MSYS2 from [https://www.msys2.org](https://www.msys2.org).

The default install path is `C:\msys64`.

### Install MinGW-w64 GCC

Open a terminal (PowerShell or CMD) and run:

```powershell
C:\msys64\usr\bin\bash.exe -l -c "pacman -S --noconfirm mingw-w64-x86_64-gcc"
```

This installs GCC 15+ with the full MinGW-w64 toolchain (~550 MB).

## Building

Run the provided batch script from the project directory:

```batch
build_astar.bat
```

Or build manually with PowerShell:

```powershell
$env:PATH = "C:\msys64\mingw64\bin;" + $env:PATH
g++ -O2 -shared -o astar.dll astar.cpp -static-libgcc -static-libstdc++ -Wl,-Bstatic -lpthread -Wl,-Bdynamic
```

A successful build prints `BUILD_OK` and produces `astar.dll` in the project directory.

### Runtime Dependency

`astar.dll` requires `libwinpthread-1.dll` at runtime. The build script copies it automatically from the MinGW install. If you build manually, copy it yourself:

```powershell
Copy-Item "C:\msys64\mingw64\bin\libwinpthread-1.dll" .
```

Python loads it automatically via `os.add_dll_directory()` — no PATH changes needed.

## DLL Exports

| Function | Purpose |
|---|---|
| `astar_route` | Route a single connection (used as fallback) |
| `route_all_cpp` | Route all connections in one call (fast path) |

### `astar_route` signature

```c
int astar_route(
    int cols, int rows,
    double ox, double oy, double cell_size,
    const int* blocked_data, int n_blocked,      // flat [r,c, r,c, ...]
    const int* congestion_data, int n_congestion, // flat [r,c,count, ...]
    double start_x, double start_y,
    double end_x,   double end_y,
    int turn_penalty, int congestion_penalty,
    double* out_x, double* out_y, int max_out    // output path
);
// Returns: number of points written, or -1 if no path found
```

### `route_all_cpp` signature

```c
int route_all_cpp(
    int cols, int rows,
    double ox, double oy, double cell_size,
    const int* blocked_data, int n_blocked,
    const double* endpoints, int n_conns,          // flat [sx,sy,ex,ey, ...]
    const double* stable_pts,                      // flat [x,y, x,y, ...] interleaved
    const int* stable_counts, int n_stable,        // point count per stable path
    int turn_penalty, int congestion_penalty,
    int* out_counts,                               // output: points per connection
    double* out_x, double* out_y, int max_total   // output: all path points
);
// Returns: total points written across all connections
```

`route_all_cpp` is the primary entry point. It:
1. Builds flat obstacle and congestion maps once
2. Seeds congestion from already-routed stable connections
3. Routes all affected connections sequentially, updating congestion after each one
4. Returns simplified (collinear points removed) paths for all connections

## Performance Design

### Flat arrays instead of hash maps

The original Python implementation used `dict` / `set` for the A* state space. The C++ implementation uses flat `vector<int>` arrays indexed by `(r * cols + c) * 5 + (dir + 1)`, giving O(1) cache-friendly access with no hash overhead.

### Dirty-restore scratch arrays

`g_scores` and `came_from` arrays are shared across all `route_all_cpp` connections. A dirty-list tracks which entries were written so only those are restored to their sentinel values (`INF` / `-1`) between routes — no full `memset` per connection.

### Single DLL call per re-route

Rather than one DLL round-trip per wire, Python makes a single call to `route_all_cpp` passing all connection endpoints at once. This eliminates Python↔C++ marshalling overhead that would otherwise dominate for many small routes.

### Timing

Both `cad_viewer.py` and `cad_viewer copy.py` measure routing time with `time.perf_counter()` and display it in the status bar:

```
Components: 8   Connections: 16   |   Routed: 16   Fallback: 0   |   C++ DLL  3.2 ms
```

To compare against the Python fallback, rename or delete `astar.dll` — the viewer falls back automatically and the status bar will show `Python  XXX ms`.

## File Overview

```
CAD/
├── astar.cpp             # C++ A* implementation
├── astar.dll             # Compiled DLL (Windows x64)
├── libwinpthread-1.dll   # MinGW pthreads runtime (required by astar.dll)
├── build_astar.bat       # Build script
├── cad_viewer.py         # RF frontend schematic viewer
└── cad_viewer copy.py    # Generic schematic viewer (random components)
```
