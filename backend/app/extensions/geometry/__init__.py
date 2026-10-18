"""Page geometry: units, homogeneous transforms and mapping back to the page.

The contract sentence (SDD 8.2) is: coordinates are measured in the **decoded
original page image pixel space**, origin top-left, x right, y down. Anything
that cannot be verified in that space degrades to ``PAGE_ONLY`` instead of being
guessed.
"""

from __future__ import annotations

from .transform import (
    EPSILON,
    UNIT_PT,
    UNIT_PX,
    PageGeometry,
    RegionMapping,
    apply,
    compose,
    identity,
    inverse,
    map_or_page_only,
    map_to_original,
    page_only,
    rotate,
    scale,
    translate,
)

__all__ = [
    "EPSILON",
    "UNIT_PT",
    "UNIT_PX",
    "PageGeometry",
    "RegionMapping",
    "apply",
    "compose",
    "identity",
    "inverse",
    "map_or_page_only",
    "map_to_original",
    "page_only",
    "rotate",
    "scale",
    "translate",
]
