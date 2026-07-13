from app.orders.optimizer import optimize, plan_to_dict


def test_canonical_no_splicing_case():
    # 10 poles x 13m, seller sells 15m only: each 13m pole eats one whole bar.
    # (9 bars would need welding the 2m offcuts — splicing is not allowed.)
    plan = optimize([(13000, 10)], [(15000, 100.0)])
    assert len(plan.bars) == 10
    assert plan.total_cost == 1000.0
    assert plan.total_waste_mm == 10 * 2000


def test_pieces_share_bars():
    # 4 x 3m fit two per 6m bar
    plan = optimize([(3000, 4)], [(6000, 60.0)])
    assert len(plan.bars) == 2
    assert all(len(b.pieces) == 2 for b in plan.bars)


def test_kerf_prevents_impossible_fit():
    # 2 x 3000 into a 6000 bar works with kerf 0, fails with kerf 10
    assert len(optimize([(3000, 2)], [(6000, 60.0)]).bars) == 1
    plan = optimize([(3000, 2)], [(6000, 60.0)], kerf_mm=10)
    assert len(plan.bars) == 2


def test_cost_beats_bar_count():
    # 2 x 5m: one 12m bar (100) vs two 6m bars (2 x 40 = 80) — fewer bars loses
    plan = optimize([(5000, 2)], [(12000, 100.0), (6000, 40.0)])
    assert plan.total_cost == 80.0
    assert len(plan.bars) == 2


def test_downsize_pass_shrinks_bars():
    # one 2m piece: greedy could open the 12m bar, downsize must pick the 3m
    plan = optimize([(2000, 1)], [(12000, 100.0), (3000, 30.0)])
    assert plan.bars[0].stock_length_mm == 3000


def test_infeasible_pieces_reported():
    plan = optimize([(20000, 2), (5000, 1)], [(15000, 100.0)])
    assert plan.infeasible == [20000]
    assert len(plan.bars) == 1


def test_mixed_lengths_realistic():
    # NCD-style: angles of several lengths from 12m stock
    pieces = [(1052, 4), (743, 8), (2077, 4), (1466, 8), (953, 8)]
    plan = optimize(pieces, [(12000, 120.0)], kerf_mm=5)
    total_needed = sum(l * q for l, q in pieces)
    assert plan.total_cost == 120.0 * len(plan.bars)
    # sanity: no bar overfull (incl. kerf)
    for b in plan.bars:
        assert b.kerf_used(5) <= b.stock_length_mm + 1e-6
    # all pieces placed
    assert sum(len(b.pieces) for b in plan.bars) == sum(q for _, q in pieces)
    assert sum(b.used_mm for b in plan.bars) == total_needed


def test_deterministic():
    pieces = [(1052, 4), (743, 8), (2077, 4), (1466, 8)]
    stock = [(12000, 120.0), (6000, 70.0)]
    a = plan_to_dict(optimize(pieces, stock, 5), 5)
    b = plan_to_dict(optimize(pieces, stock, 5), 5)
    assert a == b


def test_plan_dict_shape():
    d = plan_to_dict(optimize([(3000, 4)], [(6000, 60.0)]), 0.0)
    assert d["order"] == [
        {"stock_length_mm": 6000, "count": 2, "unit_price": 60.0, "subtotal": 120.0}
    ]
    assert d["total_cost"] == 120.0
    assert d["waste_pct"] == 0.0
    assert d["infeasible_lengths_mm"] == []
