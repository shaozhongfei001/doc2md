"""T403 / ``PI-F04-03``: unit-explicit, invertible mapping back to the page.

Every test either proves a numeric round-trip or proves a refusal. There is no
"approximately right" case: a coordinate that is silently off by a crop offset
looks perfectly valid downstream, which is why the refusals matter more than the
happy paths.
"""

from __future__ import annotations

import math

import pytest

from app.extensions import ExtensionError
from app.extensions.geometry.transform import (
    PageGeometry,
    apply,
    compose,
    determinant,
    identity,
    inverse,
    map_or_page_only,
    map_to_original,
    page_only,
    polygon_area,
    rotate,
    scale,
    translate,
)

DIGEST = "a" * 64


def geo(**overrides) -> dict:
    base = {
        "geometry_id": "G1",
        "source_sha256": DIGEST,
        "page_number": 1,
        "original_width": 1000,
        "original_height": 800,
        "processed_width": 1000,
        "processed_height": 800,
        "crop_origin": (0.0, 0.0),
        "matrix": identity(),
        "unit": "PX",
        "unit_verified": True,
    }
    base.update(overrides)
    return base


SQUARE = [(200.0, 200.0), (400.0, 200.0), (400.0, 400.0), (200.0, 400.0)]


# ------------------------------------------------------------- round-trips
def test_identity_mapping_keeps_the_region_and_normalises_it():
    result = map_to_original(geo(), SQUARE)
    assert result.evidence_class == "REGION_VERIFIED"
    assert result.polygon == [[x, y] for x, y in SQUARE]
    assert result.normalized_polygon == [[0.2, 0.25], [0.4, 0.25], [0.4, 0.5], [0.2, 0.5]]
    assert result.area == pytest.approx(40000.0)
    assert result.page_number == 1 and result.source_sha256 == DIGEST


def test_scale_is_undone_by_the_inverse():
    # matrix maps original→processed, so a 2x upscale must be halved on the way back.
    result = map_to_original(geo(matrix=scale(2.0)), SQUARE)
    assert result.polygon[0] == pytest.approx([100.0, 100.0])
    assert result.polygon[2] == pytest.approx([200.0, 200.0])


def test_crop_offset_is_undone_by_the_inverse():
    matrix = translate(-100.0, -50.0)  # processed = original - crop_origin
    result = map_to_original(geo(matrix=matrix), [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
    assert result.polygon[0] == pytest.approx([100.0, 50.0])


@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_quarter_rotations_round_trip_without_drift(degrees):
    matrix = rotate(degrees)
    original_point = (300.0, 200.0)
    processed = apply(matrix, original_point)
    back = apply(inverse(matrix), processed)
    assert back[0] == pytest.approx(original_point[0], abs=1e-9)
    assert back[1] == pytest.approx(original_point[1], abs=1e-9)


def test_compose_order_is_apply_second_first():
    # compose(A, B) applies B then A: translate by 10, then double => (0,0) -> 20.
    matrix = compose(scale(2.0), translate(10.0, 0.0))
    assert apply(matrix, (0.0, 0.0)) == pytest.approx([20.0, 0.0])
    # The other order would give 10, which is the mistake this test pins.
    reverse = compose(translate(10.0, 0.0), scale(2.0))
    assert apply(reverse, (0.0, 0.0)) == pytest.approx([10.0, 0.0])


def test_scale_then_crop_composition_maps_correctly():
    # original -> scale 2 -> crop at (100, 0)
    matrix = compose(translate(-100.0, 0.0), scale(2.0))
    result = map_to_original(geo(matrix=matrix), [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)])
    assert result.polygon[0] == pytest.approx([50.0, 0.0])
    assert result.polygon[2] == pytest.approx([51.0, 1.0])


# ----------------------------------------------------------------- refusals
def test_unverified_unit_yields_no_region_fact():
    with pytest.raises(ExtensionError) as excinfo:
        map_to_original(geo(unit_verified=False), SQUARE)
    assert excinfo.value.code == "E_GEOMETRY_UNVERIFIED"
    assert "PAGE_ONLY" in str(excinfo.value)


def test_unknown_unit_is_refused_rather_than_treated_as_pixels():
    with pytest.raises(ExtensionError) as excinfo:
        PageGeometry.from_mapping(geo(unit="EMU"))
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "unknown unit" in str(excinfo.value)


def test_singular_transform_cannot_be_inverted():
    singular = ((1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    assert determinant(singular) == pytest.approx(0.0)
    with pytest.raises(ExtensionError) as excinfo:
        map_to_original(geo(matrix=singular), SQUARE)
    assert excinfo.value.code == "E_GEOMETRY_NONINVERTIBLE"


def test_non_finite_matrix_is_rejected_as_input():
    with pytest.raises(ExtensionError) as excinfo:
        PageGeometry.from_mapping(
            geo(matrix=((1.0, 0.0, 0.0), (0.0, float("nan"), 0.0), (0.0, 0.0, 1.0)))
        )
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "non-finite" in str(excinfo.value)


def test_region_leaving_the_page_is_refused_not_clipped_away():
    # The matrix shifts right/down, so the processed region legitimately maps to
    # negative original coordinates.
    matrix = translate(100.0, 100.0)
    with pytest.raises(ExtensionError) as excinfo:
        map_to_original(geo(matrix=matrix), [(0.0, 0.0), (50.0, 0.0), (50.0, 50.0), (0.0, 50.0)])
    assert excinfo.value.code == "E_GEOMETRY_UNVERIFIED"
    assert "leaves the original page" in str(excinfo.value)


def test_region_outside_the_processed_canvas_is_an_input_error():
    # An engine that reports coordinates in a different space would otherwise map
    # back to a plausible-looking but wrong region.
    with pytest.raises(ExtensionError) as excinfo:
        map_to_original(geo(matrix=scale(2.0)), [(1500.0, 100.0), (1600.0, 100.0), (1600.0, 200.0), (1500.0, 200.0)])
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "outside the processed canvas" in str(excinfo.value)


def test_one_pixel_overhang_is_inside_epsilon_and_is_clipped():
    result = map_to_original(geo(original_width=1000), [(0.0, 0.0), (1000.5, 0.0), (1000.5, 10.0), (0.0, 10.0)], epsilon=1.0)
    assert result.polygon[1][0] == pytest.approx(1000.0)


def test_three_point_or_degenerate_regions_are_refused():
    with pytest.raises(ExtensionError) as excinfo:
        map_to_original(geo(), [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)])
    assert excinfo.value.code == "E_INPUT_INVALID"
    assert "four corners" in str(excinfo.value)

    with pytest.raises(ExtensionError) as excinfo:
        map_to_original(geo(), [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)])
    assert excinfo.value.code == "E_GEOMETRY_UNVERIFIED"
    assert "no area" in str(excinfo.value)


def test_page_number_must_be_one_based():
    with pytest.raises(ExtensionError) as excinfo:
        PageGeometry.from_mapping(geo(page_number=0))
    assert "1-based" in str(excinfo.value)


def test_rotation_must_be_a_quarter_turn_multiple():
    with pytest.raises(ExtensionError) as excinfo:
        PageGeometry.from_mapping(geo(rotation=45))
    assert "multiple of 90" in str(excinfo.value)


# ------------------------------------------------------------- PAGE_ONLY path
def test_map_or_page_only_degrades_with_the_reason_preserved():
    result = map_or_page_only(geo(unit_verified=False), SQUARE)
    assert result.evidence_class == "PAGE_ONLY"
    assert "E_GEOMETRY_UNVERIFIED" in result.reason
    assert result.polygon is None
    assert result.trace[-1] == "page_only"


def test_page_only_requires_a_reason():
    with pytest.raises(ExtensionError) as excinfo:
        page_only(geo(), reason="  ")
    assert excinfo.value.code == "E_INPUT_INVALID"


def test_polygon_area_is_absolute_shoelace():
    assert polygon_area([(0, 0), (10, 0), (10, 5), (0, 5)]) == pytest.approx(50.0)
    assert polygon_area([(0, 5), (10, 5), (10, 0), (0, 0)]) == pytest.approx(50.0)


def test_geometry_mapping_round_trips_through_from_mapping():
    mapping = geo(matrix=compose(translate(-10.0, -20.0), scale(1.5)))
    parsed = PageGeometry.from_mapping(mapping)
    assert parsed.to_mapping()["matrix"] == [list(row) for row in mapping["matrix"]]
    assert parsed.dpi is None and parsed.renderer_version is None
    assert math.isclose(parsed.crop_origin[0], 0.0)
