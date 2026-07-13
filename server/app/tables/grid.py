"""Deterministic table-grid recovery from vector ruling lines.

The sample CAD exports draw their text as strokes — get_text()/find_tables() see
nothing — but the table rules themselves are ordinary line segments. A table's rows
share left/right extents, so instead of clustering rules by connectivity (which would
fuse a BOM table with the sheet frame and title block it touches), horizontal rules
are grouped into families by x-extent similarity, families are split on large y gaps,
and vertical rules spanning the family's band become the column edges.

Everything here works in ROTATED (display) coordinates: get_drawings() returns
unrotated points, so every point is mapped through page.rotation_matrix immediately.
"""

from dataclasses import dataclass

import fitz

MIN_RULE_PT = 15.0  # shorter axis-aligned strokes are glyph ink, ticks, hatching
AXIS_TOL_PT = 0.6  # |dx| or |dy| below this counts as axis-aligned
SNAP_PT = 1.2  # rules within this distance are the same line
MERGE_GAP_PT = 3.0  # collinear segments closer than this are one stroked rule
EXTENT_IOU = 0.75  # H rules with interval-IoU above this belong to one table
V_COVERAGE = 0.55  # a column rule must span this fraction of the table band
MIN_CELL_PT = 3.5  # degenerate columns/rows below this are snapping artifacts
NESTED_CONTAINMENT = 0.85  # candidate mostly inside another is its sub-grid


@dataclass
class TableGrid:
    """A recovered ruled grid, in rotated page points."""

    bbox: tuple[float, float, float, float]
    col_edges: list[float]  # ascending x, len = n_cols + 1
    row_edges: list[float]  # ascending y, len = n_rows + 1

    @property
    def n_rows(self) -> int:
        return len(self.row_edges) - 1

    @property
    def n_cols(self) -> int:
        return len(self.col_edges) - 1

    def cell_rect(self, r: int, c: int) -> tuple[float, float, float, float]:
        return (
            self.col_edges[c],
            self.row_edges[r],
            self.col_edges[c + 1],
            self.row_edges[r + 1],
        )


def _axis_rules(
    page: fitz.Page, paths: list[dict] | None = None
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """(horizontal, vertical) rules as (cross, lo, hi): H = (y, x0, x1), V = (x, y0, y1)."""
    m = page.rotation_matrix
    hs: list[tuple[float, float, float]] = []
    vs: list[tuple[float, float, float]] = []

    def add(p1: fitz.Point, p2: fitz.Point) -> None:
        a, b = p1 * m, p2 * m
        dx, dy = abs(a.x - b.x), abs(a.y - b.y)
        if dy <= AXIS_TOL_PT and dx >= MIN_RULE_PT:
            hs.append(((a.y + b.y) / 2, min(a.x, b.x), max(a.x, b.x)))
        elif dx <= AXIS_TOL_PT and dy >= MIN_RULE_PT:
            vs.append(((a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y)))

    for path in paths if paths is not None else page.get_drawings():
        for item in path["items"]:
            if item[0] == "l":
                add(item[1], item[2])
            elif item[0] == "re":
                r = item[1]
                add(r.tl, r.tr)
                add(r.bl, r.br)
                add(r.tl, r.bl)
                add(r.tr, r.br)
            elif item[0] == "qu":
                q = item[1]
                add(q.ul, q.ur)
                add(q.ll, q.lr)
                add(q.ul, q.ll)
                add(q.ur, q.lr)
    return hs, vs


def _merge_collinear(
    rules: list[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    """Merge rules on the same line: CAD exporters draw one rule as many strokes."""
    if not rules:
        return []
    rules = sorted(rules)  # by cross coordinate
    merged: list[tuple[float, float, float]] = []
    # group cross-coordinates within SNAP_PT of the group's running mean
    group: list[tuple[float, float, float]] = [rules[0]]
    groups: list[list[tuple[float, float, float]]] = []
    for r in rules[1:]:
        if r[0] - group[-1][0] <= SNAP_PT:
            group.append(r)
        else:
            groups.append(group)
            group = [r]
    groups.append(group)

    for g in groups:
        cross = sum(r[0] for r in g) / len(g)
        intervals = sorted((r[1], r[2]) for r in g)
        lo, hi = intervals[0]
        for a, b in intervals[1:]:
            if a <= hi + MERGE_GAP_PT:
                hi = max(hi, b)
            else:
                merged.append((cross, lo, hi))
                lo, hi = a, b
        merged.append((cross, lo, hi))
    return merged


def _interval_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    if inter <= 0:
        return 0.0
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union


def _families(
    hs: list[tuple[float, float, float]],
) -> list[list[tuple[float, float, float]]]:
    """Union-find H rules whose x-extents mostly coincide — one table's row lines."""
    n = len(hs)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _interval_iou((hs[i][1], hs[i][2]), (hs[j][1], hs[j][2])) >= EXTENT_IOU:
                parent[find(i)] = find(j)

    by_root: dict[int, list[tuple[float, float, float]]] = {}
    for i in range(n):
        by_root.setdefault(find(i), []).append(hs[i])
    return [sorted(f) for f in by_root.values() if len(f) >= 3]


def _split_bands(
    family: list[tuple[float, float, float]],
    vs: list[tuple[float, float, float]],
) -> list[list[tuple[float, float, float]]]:
    """Split a family where no vertical rules bridge consecutive rows.

    Two stacked tables of similar width chain into one family (the Hebrew sheets
    stack their concrete table right above the pile table); a y-gap heuristic
    cannot separate them without also cutting through tall merged cells. The
    structural signal is vertical: inside one table its column rules span every
    row gap, between two tables nothing vertical crosses.
    """
    x_lo = min(r[1] for r in family)
    x_hi = max(r[2] for r in family)
    in_band = [
        v for v in vs if x_lo - SNAP_PT <= v[0] <= x_hi + SNAP_PT
    ]

    bands: list[list[tuple[float, float, float]]] = [[family[0]]]
    for a, b in zip(family, family[1:]):
        lo, hi = a[0], b[0]
        bridging = sum(
            1 for v in in_band if v[1] <= lo + SNAP_PT and v[2] >= hi - SNAP_PT
        )
        if bridging >= 2:
            bands[-1].append(b)
        else:
            bands.append([b])
    return [b for b in bands if len(b) >= 3]


def _dedupe_edges(values: list[float]) -> list[float]:
    values = sorted(values)
    out = [values[0]]
    for v in values[1:]:
        if v - out[-1] >= MIN_CELL_PT:
            out.append(v)
    return out


def _grid_from_band(
    band: list[tuple[float, float, float]],
    vs: list[tuple[float, float, float]],
) -> TableGrid | None:
    y0, y1 = band[0][0], band[-1][0]
    x0 = min(r[1] for r in band)
    x1 = max(r[2] for r in band)
    height = y1 - y0
    if height < 2 * MIN_CELL_PT:
        return None

    col_xs = [
        v[0]
        for v in vs
        if x0 - SNAP_PT <= v[0] <= x1 + SNAP_PT
        and (min(v[2], y1) - max(v[1], y0)) >= V_COVERAGE * height
    ]
    if len(col_xs) < 3:
        return None

    col_edges = _dedupe_edges(col_xs)
    row_edges = _dedupe_edges([r[0] for r in band])
    if len(col_edges) < 3 or len(row_edges) < 3:
        return None
    return TableGrid(
        bbox=(col_edges[0], row_edges[0], col_edges[-1], row_edges[-1]),
        col_edges=col_edges,
        row_edges=row_edges,
    )


def _area(g: TableGrid) -> float:
    return (g.bbox[2] - g.bbox[0]) * (g.bbox[3] - g.bbox[1])


def _containment(inner: TableGrid, outer: TableGrid) -> float:
    ix0 = max(inner.bbox[0], outer.bbox[0])
    iy0 = max(inner.bbox[1], outer.bbox[1])
    ix1 = min(inner.bbox[2], outer.bbox[2])
    iy1 = min(inner.bbox[3], outer.bbox[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return (ix1 - ix0) * (iy1 - iy0) / _area(inner)


def detect_grids(page: fitz.Page, paths: list[dict] | None = None) -> list[TableGrid]:
    """All ruled grids on the page, largest first, sub-grids dropped.

    A grid here is structure only — whether it is a materials table, a coordinate
    list or a title block is the classifier's problem, not geometry's.
    """
    hs, vs = _axis_rules(page, paths)
    hs = _merge_collinear(hs)
    vs = _merge_collinear(vs)

    candidates: list[TableGrid] = []
    for family in _families(hs):
        for band in _split_bands(family, vs):
            grid = _grid_from_band(band, vs)
            if grid:
                candidates.append(grid)

    # a candidate mostly inside a bigger one is that table's finer sub-grid
    # (merged-cell subdivisions); keep the outer table
    candidates.sort(key=_area, reverse=True)
    kept: list[TableGrid] = []
    for cand in candidates:
        if all(_containment(cand, k) < NESTED_CONTAINMENT for k in kept):
            kept.append(cand)
    return kept
