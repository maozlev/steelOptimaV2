"""2D sheet nesting: which stock sheets to buy so every plate is cut whole.

The plate sibling of optimizer.py's 1D cutting stock, same contract: a plate
must come WHOLE from one sheet (no welding halves), the objective is COST, and
kerf is consumed between pieces. Plates are packed by their bounding rectangle —
a shaped gusset still costs its full W×H to cut.

Method: shelf packing (FFDH with 90° rotation), guillotine-friendly by
construction — shelves are edge-to-edge cuts a shear can actually make. Seeded
shuffle restarts + a downsize pass, exactly like the 1D optimizer. Instances
are tiny (a project has a handful of plate sizes), so this is comfortably
within budget.
"""

import random
import time
from dataclasses import dataclass, field

RESTARTS = 200
TIME_BUDGET_S = 2.0
EPS = 1e-6


@dataclass
class Piece:
    w_mm: float
    h_mm: float
    key: str  # material_key, so the layout can name every rectangle


@dataclass
class Placement:
    x_mm: float
    y_mm: float
    w_mm: float  # as placed (after rotation)
    h_mm: float
    key: str
    rotated: bool


@dataclass
class Sheet:
    sheet_w_mm: float
    sheet_h_mm: float
    price: float
    placements: list[Placement] = field(default_factory=list)

    @property
    def used_area_mm2(self) -> float:
        return sum(p.w_mm * p.h_mm for p in self.placements)

    @property
    def area_mm2(self) -> float:
        return self.sheet_w_mm * self.sheet_h_mm


@dataclass
class SheetPlan:
    sheets: list[Sheet]
    infeasible: list[Piece]  # fit no stock sheet in either orientation

    @property
    def total_cost(self) -> float:
        return sum(s.price for s in self.sheets)

    def order_lines(self) -> list[dict]:
        counts: dict[tuple[float, float, float], int] = {}
        for s in self.sheets:
            k = (s.sheet_w_mm, s.sheet_h_mm, s.price)
            counts[k] = counts.get(k, 0) + 1
        return [
            {
                "sheet_w_mm": w,
                "sheet_h_mm": h,
                "count": count,
                "unit_price": price,
                "subtotal": round(count * price, 2),
            }
            for (w, h, price), count in sorted(counts.items())
        ]


@dataclass
class _Shelf:
    y_mm: float
    height_mm: float
    x_cursor_mm: float


def _orientations(piece: Piece) -> list[tuple[float, float, bool]]:
    orients = [(piece.w_mm, piece.h_mm, False)]
    if abs(piece.w_mm - piece.h_mm) > EPS:
        orients.append((piece.h_mm, piece.w_mm, True))
    return orients


def _try_place(sheet: Sheet, shelves: list[_Shelf], piece: Piece, kerf: float) -> bool:
    """Place on the shelf with least leftover height, opening a new one if room.

    Prefers the orientation that wastes the least shelf height. Kerf is charged
    after every piece in both axes (edge trims are the shop's problem)."""
    best: tuple[float, _Shelf | None, float, float, bool] | None = None  # (score, shelf, w, h, rot)
    for w, h, rot in _orientations(piece):
        for shelf in shelves:
            if h <= shelf.height_mm + EPS and shelf.x_cursor_mm + w <= sheet.sheet_w_mm + EPS:
                score = shelf.height_mm - h  # tightest height fit
                if best is None or score < best[0]:
                    best = (score, shelf, w, h, rot)
    if best is not None:
        _, shelf, w, h, rot = best
        sheet.placements.append(Placement(shelf.x_cursor_mm, shelf.y_mm, w, h, piece.key, rot))
        shelf.x_cursor_mm += w + kerf
        return True

    # no shelf takes it — open a new shelf (lowest added height first)
    used_h = (shelves[-1].y_mm + shelves[-1].height_mm + kerf) if shelves else 0.0
    for w, h, rot in sorted(_orientations(piece), key=lambda o: o[1]):
        if w <= sheet.sheet_w_mm + EPS and used_h + h <= sheet.sheet_h_mm + EPS:
            shelf = _Shelf(y_mm=used_h, height_mm=h, x_cursor_mm=0.0)
            shelves.append(shelf)
            sheet.placements.append(Placement(0.0, shelf.y_mm, w, h, piece.key, rot))
            shelf.x_cursor_mm = w + kerf
            return True
    return False


def _fits_some_sheet(piece: Piece, stock: list[tuple[float, float, float]]) -> bool:
    return any(
        (w <= sw + EPS and h <= sh + EPS)
        for sw, sh, _ in stock
        for w, h, _rot in _orientations(piece)
    )


def _pack(
    pieces: list[Piece], stock: list[tuple[float, float, float]], kerf: float
) -> list[Sheet]:
    """Fill one sheet at a time; open the cheapest sheet the next piece fits."""
    sheets: list[Sheet] = []
    shelves_of: dict[int, list[_Shelf]] = {}
    for piece in pieces:
        placed = False
        for i, sheet in enumerate(sheets):
            if _try_place(sheet, shelves_of[i], piece, kerf):
                placed = True
                break
        if placed:
            continue
        candidates = [
            (sw, sh, price)
            for sw, sh, price in stock
            if any(w <= sw + EPS and h <= sh + EPS for w, h, _ in _orientations(piece))
        ]
        sw, sh, price = min(candidates, key=lambda s: (s[2], s[2] / (s[0] * s[1])))
        sheet = Sheet(sheet_w_mm=sw, sheet_h_mm=sh, price=price)
        sheets.append(sheet)
        shelves_of[len(sheets) - 1] = []
        _try_place(sheet, shelves_of[len(sheets) - 1], piece, kerf)
    return sheets


def _downsize(sheets: list[Sheet], stock: list[tuple[float, float, float]], kerf: float) -> None:
    """Re-fit each sheet's pieces into the cheapest sheet that still holds them."""
    for sheet in sheets:
        pieces = [Piece(p.w_mm, p.h_mm, p.key) for p in sheet.placements]
        for sw, sh, price in sorted(stock, key=lambda s: (s[2], s[0] * s[1])):
            if price > sheet.price + EPS:
                break  # sorted by price — nothing cheaper follows
            if abs(price - sheet.price) <= EPS and sw * sh >= sheet.area_mm2 - EPS:
                continue  # same price, no smaller — not an improvement
            trial = Sheet(sheet_w_mm=sw, sheet_h_mm=sh, price=price)
            shelves: list[_Shelf] = []
            if all(_try_place(trial, shelves, p, kerf) for p in pieces):
                sheet.sheet_w_mm, sheet.sheet_h_mm = sw, sh
                sheet.price = price
                sheet.placements = trial.placements
                break


def optimize_sheets(
    pieces: list[tuple[float, float, int, str]],
    stock: list[tuple[float, float, float]],
    kerf_mm: float = 0.0,
    time_budget_s: float = TIME_BUDGET_S,
) -> SheetPlan:
    """pieces: [(w_mm, h_mm, qty, key)]; stock: [(sheet_w_mm, sheet_h_mm, price)]."""
    stock = sorted({(float(w), float(h), float(p)) for w, h, p in stock})
    if not stock:
        return SheetPlan(
            sheets=[], infeasible=[Piece(w, h, k) for w, h, _q, k in pieces]
        )

    flat: list[Piece] = []
    infeasible: list[Piece] = []
    seen_infeasible: set[tuple[float, float]] = set()
    for w, h, qty, key in pieces:
        if w <= 0 or h <= 0 or qty <= 0:
            continue
        piece = Piece(float(w), float(h), key)
        if not _fits_some_sheet(piece, stock):
            if (piece.w_mm, piece.h_mm) not in seen_infeasible:
                seen_infeasible.add((piece.w_mm, piece.h_mm))
                infeasible.append(piece)
            continue
        flat.extend(Piece(float(w), float(h), key) for _ in range(int(qty)))
    if not flat:
        return SheetPlan(sheets=[], infeasible=infeasible)

    rng = random.Random(0)
    deadline = time.monotonic() + time_budget_s
    # FFDH first: tallest pieces first builds the fewest, fullest shelves
    ordering = sorted(flat, key=lambda p: (-max(p.w_mm, p.h_mm), -min(p.w_mm, p.h_mm)))
    best: list[Sheet] | None = None
    best_cost = float("inf")
    for attempt in range(RESTARTS):
        if attempt > 0:
            if time.monotonic() > deadline:
                break
            ordering = flat[:]
            rng.shuffle(ordering)
        sheets = _pack(ordering, stock, kerf_mm)
        _downsize(sheets, stock, kerf_mm)
        cost = sum(s.price for s in sheets)
        waste = sum(s.area_mm2 - s.used_area_mm2 for s in sheets)
        if cost < best_cost - EPS or (
            abs(cost - best_cost) <= EPS
            and best is not None
            and waste < sum(s.area_mm2 - s.used_area_mm2 for s in best) - EPS
        ):
            best, best_cost = sheets, cost

    return SheetPlan(sheets=best or [], infeasible=infeasible)


def sheet_plan_to_dict(plan: SheetPlan, kerf_mm: float) -> dict:
    bought = sum(s.area_mm2 for s in plan.sheets)
    used = sum(s.used_area_mm2 for s in plan.sheets)
    return {
        "order": plan.order_lines(),
        "total_cost": round(plan.total_cost, 2),
        "sheets": [
            {
                "sheet_w_mm": s.sheet_w_mm,
                "sheet_h_mm": s.sheet_h_mm,
                "price": s.price,
                "placements": [
                    {
                        "x_mm": round(p.x_mm, 1),
                        "y_mm": round(p.y_mm, 1),
                        "w_mm": p.w_mm,
                        "h_mm": p.h_mm,
                        "key": p.key,
                        "rotated": p.rotated,
                    }
                    for p in s.placements
                ],
                "used_pct": round(100.0 * s.used_area_mm2 / s.area_mm2, 1),
            }
            for s in sorted(plan.sheets, key=lambda s: (-s.area_mm2, -s.used_area_mm2))
        ],
        "kerf_mm": kerf_mm,
        "total_bought_m2": round(bought / 1e6, 3),
        "total_used_m2": round(used / 1e6, 3),
        "waste_pct": round(100.0 * (bought - used) / bought, 2) if bought else 0.0,
        "infeasible_plates": [
            {"w_mm": p.w_mm, "h_mm": p.h_mm, "key": p.key} for p in plan.infeasible
        ],
    }
