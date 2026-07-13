"""1D cutting stock: which stock bars to buy so every piece is cut whole.

NO SPLICING — a piece must come whole from one bar, so 10 poles of 13m from
15m stock cost 10 bars, full stop. The objective is COST, not bar count:
stock lengths have prices, and two cheap short bars can beat one long one.

Method (variable-sized bin packing with costs is NP-hard; instances here are
small — tens of distinct lengths, hundreds of pieces):
  1. Best-Fit Decreasing over seeded shuffles of the piece list.
  2. A downsize pass: re-fit every bar's pieces into the cheapest stock length
     that still holds them — greedy opens pessimistically large bars.
Deterministic: fixed seed, fixed tie-breaks. Kerf is consumed BETWEEN pieces
(n pieces cost (n-1) kerfs); switch to n kerfs if the shop trims bar ends.
"""

import random
import time
from dataclasses import dataclass, field

RESTARTS = 200
TIME_BUDGET_S = 2.0
EPS = 1e-6


@dataclass
class Bar:
    stock_length_mm: float
    price: float
    pieces: list[float] = field(default_factory=list)

    @property
    def used_mm(self) -> float:
        return sum(self.pieces)

    def kerf_used(self, kerf_mm: float) -> float:
        return self.used_mm + kerf_mm * max(len(self.pieces) - 1, 0)

    @property
    def waste_mm(self) -> float:
        return self.stock_length_mm - self.used_mm


@dataclass
class Plan:
    bars: list[Bar]
    infeasible: list[float]  # piece lengths no stock can hold (no splicing!)

    @property
    def total_cost(self) -> float:
        return sum(b.price for b in self.bars)

    @property
    def total_waste_mm(self) -> float:
        return sum(b.waste_mm for b in self.bars)

    def order_lines(self) -> list[dict]:
        counts: dict[tuple[float, float], int] = {}
        for b in self.bars:
            counts[(b.stock_length_mm, b.price)] = (
                counts.get((b.stock_length_mm, b.price), 0) + 1
            )
        return [
            {
                "stock_length_mm": length,
                "count": count,
                "unit_price": price,
                "subtotal": round(count * price, 2),
            }
            for (length, price), count in sorted(counts.items())
        ]


def _fits(bar: Bar, piece: float, kerf: float) -> bool:
    extra = piece + (kerf if bar.pieces else 0.0)
    return bar.kerf_used(kerf) + extra <= bar.stock_length_mm + EPS


def _pack(pieces: list[float], stock: list[tuple[float, float]], kerf: float) -> list[Bar]:
    """Best-fit: tightest open bar; else open the cheapest stock that fits."""
    bars: list[Bar] = []
    for piece in pieces:
        best: Bar | None = None
        best_slack = None
        for bar in bars:
            if _fits(bar, piece, kerf):
                slack = bar.stock_length_mm - bar.kerf_used(kerf) - piece
                if best_slack is None or slack < best_slack:
                    best, best_slack = bar, slack
        if best is None:
            candidates = [s for s in stock if s[0] + EPS >= piece]
            # cheapest bar that fits; tie-break on cheapest-per-mm, then shortest
            length, price = min(
                candidates, key=lambda s: (s[1], s[1] / s[0], s[0])
            )
            best = Bar(stock_length_mm=length, price=price)
            bars.append(best)
        best.pieces.append(piece)
    return bars


def _downsize(bars: list[Bar], stock: list[tuple[float, float]], kerf: float) -> None:
    """Re-fit each bar's pieces into the cheapest stock that still holds them."""
    for bar in bars:
        need = bar.kerf_used(kerf)
        length, price = min(
            (s for s in stock if s[0] + EPS >= need),
            key=lambda s: (s[1], s[0]),
        )
        if price < bar.price - EPS or (
            abs(price - bar.price) <= EPS and length < bar.stock_length_mm
        ):
            bar.stock_length_mm, bar.price = length, price


def optimize(
    pieces: list[tuple[float, int]],
    stock: list[tuple[float, float]],
    kerf_mm: float = 0.0,
    time_budget_s: float = TIME_BUDGET_S,
) -> Plan:
    """pieces: [(length_mm, qty)]; stock: [(length_mm, price)]."""
    stock = sorted(set(stock))
    if not stock:
        return Plan(bars=[], infeasible=sorted({p for p, _ in pieces}))
    max_len = max(s[0] for s in stock)

    flat: list[float] = []
    infeasible: list[float] = []
    for length, qty in pieces:
        if length <= 0 or qty <= 0:
            continue
        if length > max_len + EPS:
            infeasible.append(length)
            continue
        flat.extend([float(length)] * int(qty))
    if not flat:
        return Plan(bars=[], infeasible=sorted(set(infeasible)))

    rng = random.Random(0)
    deadline = time.monotonic() + time_budget_s
    orderings = [sorted(flat, reverse=True)]  # BFD first — usually the winner
    best_plan: list[Bar] | None = None
    best_cost = float("inf")
    for attempt in range(RESTARTS):
        if attempt > 0:
            if time.monotonic() > deadline:
                break
            shuffled = flat[:]
            rng.shuffle(shuffled)
            orderings = [shuffled]
        bars = _pack(orderings[0], stock, kerf_mm)
        _downsize(bars, stock, kerf_mm)
        cost = sum(b.price for b in bars)
        # tie-break on waste so equal-cost plans prefer tighter cutting
        waste = sum(b.waste_mm for b in bars)
        if cost < best_cost - EPS or (
            abs(cost - best_cost) <= EPS
            and best_plan is not None
            and waste < sum(b.waste_mm for b in best_plan) - EPS
        ):
            best_plan, best_cost = bars, cost

    for bar in best_plan or []:
        bar.pieces.sort(reverse=True)
    return Plan(bars=best_plan or [], infeasible=sorted(set(infeasible)))


def plan_to_dict(plan: Plan, kerf_mm: float) -> dict:
    used = sum(b.used_mm for b in plan.bars)
    bought = sum(b.stock_length_mm for b in plan.bars)
    return {
        "order": plan.order_lines(),
        "total_cost": round(plan.total_cost, 2),
        "bars": [
            {
                "stock_length_mm": b.stock_length_mm,
                "price": b.price,
                "cuts": b.pieces,
                "waste_mm": round(b.waste_mm, 1),
            }
            for b in sorted(
                plan.bars, key=lambda b: (-b.stock_length_mm, -b.used_mm)
            )
        ],
        "kerf_mm": kerf_mm,
        "total_bought_mm": round(bought, 1),
        "total_used_mm": round(used, 1),
        "waste_pct": round(100.0 * (bought - used) / bought, 2) if bought else 0.0,
        "infeasible_lengths_mm": plan.infeasible,
    }
