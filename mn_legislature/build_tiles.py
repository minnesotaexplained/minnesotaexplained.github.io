"""Build district_tiles.json: an equal-area hex tile layout for MN legislative districts.

Every district has about the same population, so each one gets one same-size
hexagon, placed roughly where it sits on the real map. Run once (or whenever
districts.json changes); mn_legislature.html reads the output.

Steps:
  1. Centroids and adjacency from districts.json (UTM meters).
  2. Equal-area radial transform: keep each district's direction from the
     population center, rescale its distance so dense areas (the metro) expand
     and sparse areas shrink; then nudge apart any overlaps.
  3. Snap onto a hex grid with an optimal assignment, then swap tiles where
     that keeps more real district borders as touching tiles.
"""
import json
import os
import math
from collections import defaultdict

HEX_H = math.sqrt(3) / 2  # row spacing for unit-spaced pointy-top hexes


def ring_centroid_area(ring):
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    a /= 2
    return (cx / (6 * a), cy / (6 * a), abs(a))


def load_districts(path="districts.json"):
    feats = json.load(open(path))["features"]
    cent, area, verts = {}, {}, {}
    for f in feats:
        d = f["properties"]["DISTRICT"]
        ring = [tuple(p[:2]) for p in f["geometry"]["coordinates"][0]]
        x, y, a = ring_centroid_area(ring)
        cent[d], area[d] = (x, y), a
        verts[d] = {(round(px), round(py)) for px, py in ring}
    # Districts that share 2+ boundary vertices are neighbors
    owners = defaultdict(set)
    for d, vs in verts.items():
        for v in vs:
            owners[v].add(d)
    shared = defaultdict(int)
    for ds in owners.values():
        ds = sorted(ds)
        for i in range(len(ds)):
            for j in range(i + 1, len(ds)):
                shared[(ds[i], ds[j])] += 1
    adj = {pair for pair, n in shared.items() if n >= 2}
    return cent, area, adj


def merge_to_senate(cent, area, adj):
    """Senate district = its two House halves; centroid is area-weighted."""
    s_cent, s_area = {}, {}
    for d, (x, y) in cent.items():
        s, a = d[:2], area[d]
        px, py = s_cent.get(s, (0, 0))
        pa = s_area.get(s, 0)
        s_cent[s] = ((px * pa + x * a) / (pa + a), (py * pa + y * a) / (pa + a))
        s_area[s] = pa + a
    s_adj = {tuple(sorted((a[:2], b[:2]))) for a, b in adj if a[:2] != b[:2]}
    return s_cent, s_area, s_adj


def spread(cent, area, adj, iters=400, sigma_deg=30, gravity=0.001):
    """Equal-area radial transform around the population center, then de-overlap.

    Every district has the same population, so the mean of the centroids is the
    population center. Each district keeps its direction from that center; its
    distance is rescaled so that, looking in any direction, the area out to the
    district matches how many districts lie that close (smoothed over nearby angles).
    """
    keys = sorted(cent)
    n = len(keys)
    cx = sum(cent[k][0] for k in keys) / n
    cy = sum(cent[k][1] for k in keys) / n
    polar = []
    for k in keys:
        dx, dy = cent[k][0] - cx, cent[k][1] - cy
        polar.append((math.hypot(dx, dy), math.atan2(dy, dx)))
    sigma = math.radians(sigma_deg)
    norm = 1 / (sigma * math.sqrt(2 * math.pi))
    pos = []
    for r_i, t_i in polar:
        n_eff = 0.0
        for r_j, t_j in polar:
            if r_j <= r_i:
                dt = (t_i - t_j + math.pi) % (2 * math.pi) - math.pi
                n_eff += norm * math.exp(-dt * dt / (2 * sigma * sigma))
        r_new = math.sqrt(2 * n_eff * HEX_H)
        pos.append([r_new * math.cos(t_i), r_new * math.sin(t_i)])
    # Push apart anything closer than one hex
    for it in range(iters):
        move = [[0.0, 0.0] for _ in range(n)]
        for i in range(n):
            xi, yi = pos[i]
            for j in range(i + 1, n):
                dx, dy = pos[j][0] - xi, pos[j][1] - yi
                if abs(dx) > 1.0 or abs(dy) > 1.0:
                    continue
                d = math.hypot(dx, dy) or 1e-9
                if d < 1.0:
                    f = 0.25 * (1.0 - d) / d
                    move[i][0] -= dx * f; move[i][1] -= dy * f
                    move[j][0] += dx * f; move[j][1] += dy * f
        for i in range(n):
            # Gentle pull toward the center closes gaps left by the transform
            pos[i][0] += move[i][0] - gravity * pos[i][0]
            pos[i][1] += move[i][1] - gravity * pos[i][1]
    return keys, pos


def hungarian(cost):
    """Min-cost assignment of n rows to m >= n columns. Returns col index per row."""
    n, m = len(cost), len(cost[0])
    INF = float("inf")
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [INF] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    out = [0] * n
    for j in range(1, m + 1):
        if p[j]:
            out[p[j] - 1] = j - 1
    return out


def hex_neighbors(c, r):
    offs = [(1, 0), (-1, 0), (0, -1), (0, 1), (1, -1), (1, 1)] if r % 2 else \
           [(1, 0), (-1, 0), (-1, -1), (-1, 1), (0, -1), (0, 1)]
    return [(c + dc, r + dr) for dc, dr in offs]


def snap_to_hex(keys, pos, adj, border_weight=1.5, passes=6):
    """Assign each point a hex cell (odd-r offset coords).

    1. Optimal assignment minimizing total squared displacement.
    2. Local swaps (and moves into empty cells) that keep more real borders as
       touching tiles, traded off against displacement.
    """
    xs = [p[0] for p in pos]; ys = [p[1] for p in pos]
    x0, y1 = min(xs) - 2, max(ys) + 2
    cols = int(max(xs) - x0) + 4
    rows = int((y1 - min(ys)) / HEX_H) + 4
    cells = [(c, r, x0 + c + (0.5 if r % 2 else 0), y1 - r * HEX_H)
             for r in range(rows) for c in range(cols)]
    cost = [[(px - cx) ** 2 + (py - cy) ** 2 for (_, _, cx, cy) in cells] for px, py in pos]
    assign = hungarian(cost)  # district index -> cell index

    idx = {k: i for i, k in enumerate(keys)}
    nb = [[] for _ in keys]
    for a, b in adj:
        nb[idx[a]].append(idx[b]); nb[idx[b]].append(idx[a])
    cell_of = {(c, r): ci for ci, (c, r, _, _) in enumerate(cells)}
    near = [[cell_of[n] for n in hex_neighbors(c, r) if n in cell_of] for (c, r, _, _) in cells]

    def local(i, ci, occupant):
        """Cost of district i sitting in cell ci, given who occupies cells."""
        touching = sum(1 for j in nb[i] if occupant.get(assign[j]) == j and assign[j] in near[ci])
        return cost[i][ci] - border_weight * touching

    for _ in range(passes):
        improved = 0
        occupant = {ci: i for i, ci in enumerate(assign)}
        for i in range(len(keys)):
            for ci in set(near[assign[i]]) | {assign[nj] for nj in nb[i]}:
                j = occupant.get(ci)
                if j == i:
                    continue
                a_old, b_old = assign[i], ci
                before = local(i, a_old, occupant) + (local(j, b_old, occupant) if j is not None else 0)
                # try the swap
                assign[i] = b_old
                if j is not None:
                    assign[j] = a_old
                occ2 = dict(occupant); occ2[b_old] = i
                if j is not None:
                    occ2[a_old] = j
                else:
                    occ2.pop(a_old, None)
                after = local(i, b_old, occ2) + (local(j, a_old, occ2) if j is not None else 0)
                if after < before - 1e-9:
                    occupant = occ2
                    improved += 1
                else:
                    assign[i] = a_old
                    if j is not None:
                        assign[j] = b_old
        if not improved:
            break

    out = {k: cells[assign[i]][:2] for i, k in enumerate(keys)}
    # Shift so the layout starts at row 0 / col 0, keeping row parity
    min_r = min(r for c, r in out.values())
    min_r -= min_r % 2
    min_c = min(c for c, r in out.values())
    return {k: [c - min_c, r - min_r] for k, (c, r) in out.items()}


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))  # data files live next to this script
    cent, area, adj = load_districts()
    print(f"house: {len(cent)} districts, {len(adj)} borders")
    house = snap_to_hex(*spread(cent, area, adj), adj)
    s_cent, s_area, s_adj = merge_to_senate(cent, area, adj)
    print(f"senate: {len(s_cent)} districts, {len(s_adj)} borders")
    senate = snap_to_hex(*spread(s_cent, s_area, s_adj), s_adj)
    with open("district_tiles.json", "w") as f:
        json.dump({"layout": "odd-r pointy-top hex, [col, row], row 0 = north",
                   "house": house, "senate": senate}, f, indent=1)
    print("wrote district_tiles.json")


if __name__ == "__main__":
    main()
