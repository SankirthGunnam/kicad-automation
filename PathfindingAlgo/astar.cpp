#include <vector>
#include <queue>
#include <cmath>
#include <algorithm>
#include <climits>
#include <tuple>
#include <cstring>

static const int INF = INT_MAX / 2;

// ── Shared-library exports (Windows DLL / macOS dylib / Linux so) ─────────────
#if defined(_WIN32) || defined(__CYGWIN__)
  #define ASTAR_EXPORT __declspec(dllexport)
#else
  #define ASTAR_EXPORT __attribute__((visibility("default")))
#endif

// Directions: 0=up(-1,0)  1=down(+1,0)  2=left(0,-1)  3=right(0,+1)
static const int DR[] = {-1, 1, 0, 0};
static const int DC[] = {0, 0, -1, 1};

// State space: (r, c, dir)  dir in [-1..3] encoded as dir+1 → [0..4]
// Flat index: (r * cols + c) * 5 + (dir + 1)
inline int sidx(int r, int c, int d, int cols) {
    return (r * cols + c) * 5 + (d + 1);
}

// In-place simplify: remove collinear middle points, write result to ox/oy.
// Returns new count.
static int simplify(const double* px, const double* py, int n,
                    double* ox, double* oy) {
    if (n <= 2) {
        for (int i = 0; i < n; i++) { ox[i] = px[i]; oy[i] = py[i]; }
        return n;
    }
    int out = 0;
    ox[out] = px[0]; oy[out] = py[0]; ++out;
    for (int i = 1; i < n - 1; i++) {
        bool same_x = (fabs(ox[out-1] - px[i]) < 0.5) && (fabs(px[i] - px[i+1]) < 0.5);
        bool same_y = (fabs(oy[out-1] - py[i]) < 0.5) && (fabs(py[i] - py[i+1]) < 0.5);
        if (!(same_x || same_y)) { ox[out] = px[i]; oy[out] = py[i]; ++out; }
    }
    ox[out] = px[n-1]; oy[out] = py[n-1]; ++out;
    return out;
}

// Core A* using pre-built flat arrays.
// g_scores and came_from must be size rows*cols*5, pre-filled with INF / -1.
// They are reset to INF/-1 for the cells touched during this call before returning.
// Returns simplified point count written to out_x/out_y, or -1 if no path.
static int astar_single(
    int cols, int rows,
    double ox, double oy, double cell_size,
    const std::vector<bool>& blocked_map,   // rows*cols
    const std::vector<int>& cong_map,        // rows*cols
    double sx, double sy,
    double ex, double ey,
    int turn_penalty, int congestion_penalty,
    std::vector<int>& g_scores,              // rows*cols*5, pre-INF
    std::vector<int>& came_from,             // rows*cols*5, pre -1
    std::vector<int>& dirty,                 // scratch — cleared each call
    double* out_x, double* out_y, int max_out
) {
    int sr = (int)((sy - oy) / cell_size);
    int sc = (int)((sx - ox) / cell_size);
    int er = (int)((ey - oy) / cell_size);
    int ec = (int)((ex - ox) / cell_size);

    if (sr == er && sc == ec) {
        if (max_out >= 2) { out_x[0]=sx; out_y[0]=sy; out_x[1]=ex; out_y[1]=ey; }
        return (max_out >= 2) ? 2 : -1;
    }

    dirty.clear();

    auto touch = [&](int s, int g_val, int cf_val) {
        if (g_scores[s] == INF) dirty.push_back(s);
        g_scores[s]  = g_val;
        came_from[s] = cf_val;
    };

    auto is_blocked = [&](int r, int c) -> bool {
        if (r < 0 || r >= rows || c < 0 || c >= cols) return true;
        if (r == sr && c == sc) return false;
        if (r == er && c == ec) return false;
        return blocked_map[r * cols + c];
    };

    auto h = [&](int r, int c) -> int {
        return abs(r - er) + abs(c - ec);
    };

    using PQ = std::tuple<int, int, int, int, int>; // f,g,r,c,d
    std::priority_queue<PQ, std::vector<PQ>, std::greater<PQ>> heap;

    int s0 = sidx(sr, sc, -1, cols);
    touch(s0, 0, -1);
    heap.push({h(sr, sc), 0, sr, sc, -1});

    bool found  = false;
    int  goal_d = -1;

    while (!heap.empty()) {
        auto [f, g, r, c, d] = heap.top(); heap.pop();

        int cur_s = sidx(r, c, d, cols);
        if (g > g_scores[cur_s]) continue;

        if (r == er && c == ec) { found = true; goal_d = d; break; }

        for (int di = 0; di < 4; di++) {
            int nr = r + DR[di], nc = c + DC[di];
            if (is_blocked(nr, nc)) continue;
            int turn_cost = (d != -1 && di != d) ? turn_penalty : 0;
            int cong = cong_map[nr * cols + nc] * congestion_penalty;
            int ng   = g + 1 + turn_cost + cong;
            int ns   = sidx(nr, nc, di, cols);
            if (ng < g_scores[ns]) {
                touch(ns, ng, cur_s);
                heap.push({ng + h(nr, nc), ng, nr, nc, di});
            }
        }
    }

    // Restore touched cells so scratch arrays stay "clean" for next call
    for (int s : dirty) { g_scores[s] = INF; came_from[s] = -1; }

    if (!found) return -1;

    // Reconstruct
    std::vector<std::pair<int,int>> cells;
    int cur_s = sidx(er, ec, goal_d, cols);
    // Temporarily re-run A* result — came_from was cleared, so we must
    // collect the path BEFORE clearing. Re-run is wasteful; keep a
    // separate path-came_from that we don't clear.
    // ---- Use a local map just for path reconstruction ----
    // (The dirty-restore trick above erased came_from — fix: store path separately)
    // We'll re-do this cleanly: don't restore came_from inside astar_single;
    // instead restore only g_scores and let came_from persist until after reconstruct.
    // This requires a redesign — see below.
    // For simplicity here we fall back to reconstructing before clearing.
    // The fix: collect path before restoring. Restructure below.
    (void)cur_s; // suppress warning — real path is below
    return -1;   // placeholder, see restructured version
}


// ── Corrected, self-contained A* (path collected before dirty-restore) ────────

static int astar_route_impl(
    int cols, int rows,
    double ox, double oy, double cell_size,
    const std::vector<bool>& blocked_map,
    const std::vector<int>& cong_map,
    double sx, double sy, double ex, double ey,
    int turn_penalty, int congestion_penalty,
    std::vector<int>& g_scores,   // rows*cols*5, pre-INF (reset on exit)
    std::vector<int>& cf,         // rows*cols*5, pre -1  (reset on exit)
    double* out_x, double* out_y, int max_out
) {
    int sr = (int)((sy - oy) / cell_size);
    int sc = (int)((sx - ox) / cell_size);
    int er = (int)((ey - oy) / cell_size);
    int ec = (int)((ex - ox) / cell_size);

    if (sr == er && sc == ec) {
        if (max_out >= 2) { out_x[0]=sx; out_y[0]=sy; out_x[1]=ex; out_y[1]=ey; }
        return (max_out >= 2) ? 2 : -1;
    }

    std::vector<int> dirty;
    dirty.reserve(512);

    auto touch_g = [&](int s, int val) {
        if (g_scores[s] == INF) dirty.push_back(s);
        g_scores[s] = val;
    };

    auto is_blocked = [&](int r, int c) -> bool {
        if (r < 0 || r >= rows || c < 0 || c >= cols) return true;
        if (r == sr && c == sc) return false;
        if (r == er && c == ec) return false;
        return blocked_map[r * cols + c];
    };

    auto h = [&](int r, int c) -> int { return abs(r-er)+abs(c-ec); };

    using PQ = std::tuple<int,int,int,int,int>;
    std::priority_queue<PQ,std::vector<PQ>,std::greater<PQ>> heap;

    int s0 = sidx(sr, sc, -1, cols);
    touch_g(s0, 0);
    cf[s0] = -1;
    heap.push({h(sr,sc), 0, sr, sc, -1});

    bool found = false;
    int  goal_s = -1;

    while (!heap.empty()) {
        auto [f,g,r,c,d] = heap.top(); heap.pop();
        int cur_s = sidx(r,c,d,cols);
        if (g > g_scores[cur_s]) continue;
        if (r==er && c==ec) { found=true; goal_s=cur_s; break; }

        for (int di=0; di<4; di++) {
            int nr=r+DR[di], nc=c+DC[di];
            if (is_blocked(nr,nc)) continue;
            int turn_cost = (d!=-1 && di!=d) ? turn_penalty : 0;
            int cong = cong_map[nr*cols+nc]*congestion_penalty;
            int ng   = g+1+turn_cost+cong;
            int ns   = sidx(nr,nc,di,cols);
            if (ng < g_scores[ns]) {
                touch_g(ns, ng);
                cf[ns] = cur_s;
                heap.push({ng+h(nr,nc), ng, nr, nc, di});
            }
        }
    }

    // Reconstruct path BEFORE restoring arrays
    std::vector<std::pair<int,int>> cells;
    if (found) {
        int cur = goal_s;
        while (cur != -1) {
            int cell = cur / 5;
            cells.push_back({cell / cols, cell % cols});
            cur = cf[cur];
        }
        std::reverse(cells.begin(), cells.end());
    }

    // Restore scratch arrays
    for (int s : dirty) { g_scores[s] = INF; cf[s] = -1; }

    if (!found) return -1;

    int n = (int)cells.size();
    std::vector<double> tx(n), ty(n);
    for (int i=0; i<n; i++) {
        tx[i] = cells[i].second * cell_size + ox + cell_size/2.0;
        ty[i] = cells[i].first  * cell_size + oy + cell_size/2.0;
    }
    return simplify(tx.data(), ty.data(), n, out_x, out_y);
}


// ── Mark path cells into congestion map ───────────────────────────────────────

static void mark_path(
    const double* pts_x, const double* pts_y, int n_pts,
    double ox, double oy, double cell_size,
    int cols, int rows,
    std::vector<int>& cong_map
) {
    auto to_r = [&](double y){ return (int)((y-oy)/cell_size); };
    auto to_c = [&](double x){ return (int)((x-ox)/cell_size); };
    for (int i=0; i<n_pts-1; i++) {
        int r1=to_r(pts_y[i]), c1=to_c(pts_x[i]);
        int r2=to_r(pts_y[i+1]), c2=to_c(pts_x[i+1]);
        int steps = std::max(abs(r2-r1), abs(c2-c1));
        if (steps==0) {
            if (r1>=0&&r1<rows&&c1>=0&&c1<cols) cong_map[r1*cols+c1]++;
            continue;
        }
        for (int t=0; t<=steps; t++) {
            int r = r1 + (int)round((r2-r1)*(double)t/steps);
            int c = c1 + (int)round((c2-c1)*(double)t/steps);
            if (r>=0&&r<rows&&c>=0&&c<cols) cong_map[r*cols+c]++;
        }
    }
}


extern "C" {

// ── Single-connection route (used by cad_viewer.py per-call fallback) ─────────
ASTAR_EXPORT int astar_route(
    int cols, int rows,
    double ox, double oy, double cell_size,
    const int* blocked_data, int n_blocked,
    const int* congestion_data, int n_congestion,
    double sx, double sy, double ex, double ey,
    int turn_penalty, int congestion_penalty,
    double* out_x, double* out_y, int max_out
) {
    std::vector<bool> blocked_map(rows*cols, false);
    for (int i=0; i<n_blocked; i++)
        blocked_map[blocked_data[i*2]*cols + blocked_data[i*2+1]] = true;

    std::vector<int> cong_map(rows*cols, 0);
    for (int i=0; i<n_congestion; i++)
        cong_map[congestion_data[i*3]*cols + congestion_data[i*3+1]] =
            congestion_data[i*3+2];

    std::vector<int> g_scores(rows*cols*5, INF);
    std::vector<int> cf(rows*cols*5, -1);

    return astar_route_impl(cols,rows,ox,oy,cell_size,
                            blocked_map,cong_map,
                            sx,sy,ex,ey,
                            turn_penalty,congestion_penalty,
                            g_scores,cf,
                            out_x,out_y,max_out);
}


// ── Batch route ALL connections in one call ────────────────────────────────────
//
// endpoints[i*4..i*4+3] = {sx, sy, ex, ey} for connection i (sorted by caller).
//
// stable_pts / stable_counts: pre-routed (stable) connection paths used only
//   to seed the congestion map.  stable_pts is a flat list of alternating x,y;
//   stable_counts[i] = number of points in that path.
//
// out_counts[i]: number of simplified points written for connection i, -1 if failed.
// out_x / out_y : concatenated output points for all routed connections.
// Returns total points written.
ASTAR_EXPORT int route_all_cpp(
    int cols, int rows,
    double ox, double oy, double cell_size,
    const int* blocked_data, int n_blocked,
    const double* endpoints, int n_conns,
    const double* stable_pts, const int* stable_counts, int n_stable,
    int turn_penalty, int congestion_penalty,
    int* out_counts,
    double* out_x, double* out_y, int max_total
) {
    // ── Build flat obstacle map ───────────────────────────────────────────────
    std::vector<bool> blocked_map(rows*cols, false);
    for (int i=0; i<n_blocked; i++)
        blocked_map[blocked_data[i*2]*cols + blocked_data[i*2+1]] = true;

    // ── Build congestion map from stable paths ────────────────────────────────
    std::vector<int> cong_map(rows*cols, 0);
    int soff = 0;
    for (int i=0; i<n_stable; i++) {
        int n = stable_counts[i];
        mark_path(stable_pts+soff, stable_pts+soff+1, n,
                  ox, oy, cell_size, cols, rows, cong_map);
        // stable_pts is interleaved x,y pairs → stride 2
        // mark_path expects separate x/y arrays — re-extract
        soff += n * 2;
    }

    // Redo stable path marking with correct interleaved layout
    // (The mark_path above used wrong pointer arithmetic — fix below)
    std::fill(cong_map.begin(), cong_map.end(), 0);
    soff = 0;
    for (int i=0; i<n_stable; i++) {
        int n = stable_counts[i];
        std::vector<double> spx(n), spy(n);
        for (int j=0; j<n; j++) { spx[j]=stable_pts[soff+j*2]; spy[j]=stable_pts[soff+j*2+1]; }
        mark_path(spx.data(), spy.data(), n, ox, oy, cell_size, cols, rows, cong_map);
        soff += n * 2;
    }

    // ── Shared scratch arrays (reused across all A* calls) ───────────────────
    std::vector<int> g_scores(rows*cols*5, INF);
    std::vector<int> cf(rows*cols*5, -1);

    // ── Route each connection ─────────────────────────────────────────────────
    int total = 0;
    for (int i=0; i<n_conns; i++) {
        double sx=endpoints[i*4],   sy=endpoints[i*4+1];
        double ex=endpoints[i*4+2], ey=endpoints[i*4+3];
        int    rem = max_total - total;
        int    n   = astar_route_impl(cols,rows,ox,oy,cell_size,
                                      blocked_map,cong_map,
                                      sx,sy,ex,ey,
                                      turn_penalty,congestion_penalty,
                                      g_scores,cf,
                                      out_x+total, out_y+total, rem);
        out_counts[i] = n;
        if (n > 0) {
            mark_path(out_x+total, out_y+total, n,
                      ox, oy, cell_size, cols, rows, cong_map);
            total += n;
        }
    }
    return total;
}

} // extern "C"
