"""Image regions, flow graphs and charts (SDD 10–11, ``PI-F05-01``…``06``).

Module map (filled in as the Loop lands):

``classify``     region routing: TABLE / FLOW / CHART / DECORATIVE / UNKNOWN
``flow``         node and arrow *candidates* from OCR + an approved VLM
``consistency``  graph structure, control flow, and text-vs-image checks
``chart``        statistical chart values with value-type discipline
``roi``          one bounded local re-recognition pass

Two rules hold across every module here and are the reason the package exists
separately from ``tables``:

1. A model's output is a **candidate**. ``schema passes`` only proves the format;
   `classification_state` and `direction_state` carry the difference, and only a
   review decision can promote anything to ``CONFIRMED``.
2. Coordinates and confidences come from the engine or from ``geometry``. A value
   that was not returned stays ``null`` — no module in this package estimates one.
"""

from __future__ import annotations

__all__: list[str] = []
