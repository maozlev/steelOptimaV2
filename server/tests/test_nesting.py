from app.orders.nesting import optimize_sheets, sheet_plan_to_dict


def test_plates_share_a_sheet():
    # 4 plates of 890x185 fit comfortably on one 1500x3000 sheet
    plan = optimize_sheets([(890, 185, 4, "PLATE-16-890X185")], [(1500, 3000, 500.0)])
    assert len(plan.sheets) == 1
    assert plan.total_cost == 500.0
    assert len(plan.sheets[0].placements) == 4


def test_no_welding_oversize_plate_is_infeasible():
    # a 2000x1600 plate fits no 1500x3000 sheet in either orientation
    plan = optimize_sheets([(2000, 1600, 1, "BIG")], [(1500, 3000, 500.0)])
    assert plan.sheets == []
    assert len(plan.infeasible) == 1
    assert plan.infeasible[0].key == "BIG"


def test_rotation_saves_the_day():
    # 1400x2900 only fits 1500x3000 unrotated; 2900x1400 only rotated
    plan = optimize_sheets([(2900, 1400, 1, "ROT")], [(1500, 3000, 500.0)])
    assert len(plan.sheets) == 1
    assert plan.sheets[0].placements[0].rotated


def test_cost_beats_sheet_count():
    # two cheap small sheets (fit one plate each) beat one big expensive sheet
    plan = optimize_sheets(
        [(900, 900, 2, "P")],
        [(1000, 1000, 100.0), (1000, 2100, 350.0)],
    )
    assert plan.total_cost == 200.0
    assert len(plan.sheets) == 2


def test_mixed_thickness_group_packs_together():
    # different materials of the same thickness nest on one sheet
    plan = optimize_sheets(
        [(890, 185, 4, "PLATE-16-890X185"), (450, 174, 8, "PLATE-16-450X174")],
        [(1500, 3000, 500.0)],
    )
    assert len(plan.sheets) == 1
    keys = {p.key for p in plan.sheets[0].placements}
    assert keys == {"PLATE-16-890X185", "PLATE-16-450X174"}


def test_kerf_consumes_space():
    # two 500-wide pieces + 3mm kerf cannot share a 1000-wide sheet row,
    # but DO fit stacked as two shelves on a 1000x1010 sheet
    plan = optimize_sheets([(500, 500, 2, "K")], [(1000, 1010, 100.0)], kerf_mm=3.0)
    assert len(plan.sheets) == 1
    ys = sorted(p.y_mm for p in plan.sheets[0].placements)
    assert ys[0] != ys[1]  # stacked, not side by side


def test_dict_shape_and_placements_inside_sheet():
    plan = optimize_sheets(
        [(890, 185, 8, "A"), (450, 174, 8, "B")], [(1500, 3000, 400.0)], kerf_mm=3.0
    )
    d = sheet_plan_to_dict(plan, 3.0)
    assert d["order"] and d["total_cost"] > 0
    for sheet in d["sheets"]:
        for p in sheet["placements"]:
            assert p["x_mm"] + p["w_mm"] <= sheet["sheet_w_mm"] + 1e-6
            assert p["y_mm"] + p["h_mm"] <= sheet["sheet_h_mm"] + 1e-6
    # waste_pct is computed from exact mm²; the m² fields are rounded for display
    recomputed = 100.0 * (d["total_bought_m2"] - d["total_used_m2"]) / d["total_bought_m2"]
    assert abs(d["waste_pct"] - recomputed) < 0.1


def test_deterministic():
    args = ([(890, 185, 8, "A"), (450, 174, 8, "B")], [(1500, 3000, 400.0)], 3.0)
    a = sheet_plan_to_dict(optimize_sheets(*args), 3.0)
    b = sheet_plan_to_dict(optimize_sheets(*args), 3.0)
    assert a == b
