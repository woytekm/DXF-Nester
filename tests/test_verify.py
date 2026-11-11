import pytest
from helpers import ALL4, cfg, item, l_part, rect_item, rect_part

from nestlib.models import Layout, Placement
from nestlib.verify import verify_layouts

C = cfg("1000x500", margin=10, fit_spacing=10)
PART = rect_part(100, 100, "sq")


def layout(*pls, index=1, c=C):
    return Layout(index, c.sheet, tuple(pls))


def pl(x, y, inst=1, rot=0.0, w=100.0, h=100.0, name="sq"):
    return Placement(name, inst, rot, x, y, w, h)


def codes(issues):
    return sorted({i.code for i in issues})


def check(layouts, items=None, c=C):
    return verify_layouts(layouts, items or [item(PART, 2)], c)


def test_valid_layout_passes():
    assert check([layout(pl(10, 10, 1), pl(120, 10, 2))]) == []


def test_overlap():
    assert "overlap" in codes(check([layout(pl(10, 10, 1), pl(60, 10, 2))]))


def test_spacing_violation_but_no_overlap():
    c = codes(check([layout(pl(10, 10, 1), pl(115, 10, 2))]))                     # 5 mm gap, 10 required
    assert c == ["spacing_violation"]


def test_exact_spacing_is_allowed():
    assert check([layout(pl(10, 10, 1), pl(120, 10, 2))]) == []


@pytest.mark.parametrize("x, y", [(5, 10), (10, 5), (905, 10), (10, 405)])
def test_outside_usable_area_margin_counts(x, y):
    assert "outside_usable_area" in codes(check([layout(pl(x, y, 1), pl(500, 200, 2))]))


def test_touching_the_usable_edge_is_fine():
    assert check([layout(pl(10, 10, 1), pl(890, 390, 2))]) == []


def test_rotation_must_be_allowed():
    it = item(PART, 2, rotations=(0.0, 180.0))
    assert "rotation_not_allowed" in codes(check([layout(pl(10, 10, 1, rot=90.0), pl(200, 10, 2))], [it]))


def test_missing_duplicated_and_unknown_pieces():
    assert "instance_mismatch" in codes(check([layout(pl(10, 10, 1))]))                          # instance 2 missing
    assert "instance_mismatch" in codes(check([layout(pl(10, 10, 1), pl(200, 10, 1))]))          # instance 1 twice
    assert "instance_mismatch" in codes(check([layout(pl(10, 10, 1), pl(200, 10, 2)),
                                               layout(pl(10, 10, 3), index=2)]))                 # 3 of qty 2
    assert "unknown_part" in codes(check([layout(pl(10, 10, 1), pl(200, 10, 2), pl(400, 10, 1, name="ghost"))]))


def test_recorded_bbox_must_match_the_outline():
    assert "bbox_mismatch" in codes(check([layout(pl(10, 10, 1, w=120.0), pl(200, 10, 2))]))
    assert "bbox_mismatch" in codes(check([layout(pl(10, 10, 1), pl(200, 10, 2, h=90.0))]))


def test_sheet_indices_must_run_from_one():
    assert "bad_sheet_index" in codes(check([layout(pl(10, 10, 1), pl(200, 10, 2), index=2)]))


def test_pieces_on_different_sheets_do_not_interact():
    assert check([layout(pl(10, 10, 1)), layout(pl(10, 10, 2), index=2)]) == []


# ----------------- true shapes, not bounding boxes: what a future nester relies on
L = l_part("L")
LC = cfg("1000x500", fit_spacing=10)


def l_pl(x, y, inst, rot):
    return Placement("L", inst, rot, x, y, 100.0, 100.0)


def test_interlocked_l_shapes_pass_although_their_boxes_overlap():
    layouts = [Layout(1, LC.sheet, (l_pl(100, 100, 1, 0.0), l_pl(155, 155, 2, 180.0)))]
    a, b = layouts[0].placements
    assert a.x < b.x + b.width and b.x < a.x + a.width and a.y < b.y + b.height and b.y < a.y + a.height  # boxes overlap
    assert verify_layouts(layouts, [item(L, 2)], LC) == []


def test_interlocked_l_shapes_too_close_are_caught():
    layouts = [Layout(1, LC.sheet, (l_pl(100, 100, 1, 0.0), l_pl(145, 145, 2, 180.0)))]   # corners 7 mm apart
    assert codes(verify_layouts(layouts, [item(L, 2)], LC)) == ["spacing_violation"]
    layouts = [Layout(1, LC.sheet, (l_pl(100, 100, 1, 0.0), l_pl(130, 130, 2, 180.0)))]   # arms collide
    assert "overlap" in codes(verify_layouts(layouts, [item(L, 2)], LC))
