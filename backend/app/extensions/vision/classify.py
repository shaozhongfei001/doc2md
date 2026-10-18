"""Image region routing (SDD 10.1, ``PI-F05-01``).

The rule this module exists for:

    "框架的image/chart类别不能直接证明FLOW"

A layout engine calling something "image" says nothing about whether it is a flow
chart. So the routing table is deliberately asymmetric:

* a framework ``table``/``chart`` label is *direct* evidence for that class;
* a framework ``image`` label only says "this is a picture" → ``UNKNOWN``;
* only an approved VLM may *propose* ``FLOW``, and even then the state stays
  ``CANDIDATE`` — a proposal is not a classification.

Two more properties are structural:

* ``DECORATIVE`` keeps the asset reference and the filter reason. Filtering is not
  deletion.
* Overlapping regions keep a parent relationship and get **exactly one** fact owner
  (the smallest area), so the same pixels cannot be counted twice. Equal areas
  leave the group without an owner, which is reported rather than broken by a
  tie-break rule nobody agreed to.

``engine_confidence`` is copied only from the engine. A VLM's self-reported
confidence is never promoted into that field: a model's "0.99" is not a measured
probability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import ExtensionError
from ..geometry.transform import polygon_area
from ..tables.ocr_alignment import intersection_area

CLASSES = ("TABLE", "FLOW", "CHART", "DECORATIVE", "UNKNOWN")

#: Below this the engine is not saying "this is a table", it is guessing. The value
#: is a starting point, **not calibrated**: it needs real Paddle/VLM output to tune,
#: and shipping it as if it were measured would be the same mistake as treating a
#: model's self-reported 0.99 as a probability.
DEFAULT_CONFIDENCE_THRESHOLD = 0.6

#: Classes that may produce facts. Only these are subject to the confidence gate:
#: DECORATIVE is filtered out and UNKNOWN already requires review, so a threshold on
#: them would add noise without adding protection.
FACT_CLASSES = ("TABLE", "FLOW", "CHART")

#: Framework labels that are direct evidence for a class.
FRAMEWORK_LABELS = {
    "table": "TABLE",
    "chart": "CHART",
    "figure": "UNKNOWN",
    "image": "UNKNOWN",
    "picture": "UNKNOWN",
}

ROUTES = {
    "TABLE": "TABLE_ADAPTER",
    "CHART": "CHART_ADAPTER",
    "FLOW": "FLOW_CANDIDATE",
    "DECORATIVE": "FILTERED",
    "UNKNOWN": "NEEDS_HUMAN",
}

__all__ = [
    "CLASSES",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "FRAMEWORK_LABELS",
    "ROUTES",
    "ClassificationResult",
    "route_regions",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


@dataclass(frozen=True)
class ClassificationResult:
    visuals: list[dict[str, Any]]
    routes: list[dict[str, Any]]
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "visuals": [dict(v) for v in self.visuals],
            "routes": [dict(r) for r in self.routes],
            "issues": [dict(i) for i in self.issues],
        }


def _polygon(value: Any, *, name: str) -> list[list[float]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 4:
        raise _input_error(f"{name} must be four corners, got {value!r}")
    points: list[list[float]] = []
    for index, point in enumerate(value):
        if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
            raise _input_error(f"{name}[{index}] must be a pair (x, y)")
        for coordinate in point:
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                raise _input_error(f"{name}[{index}] must be numeric")
        points.append([float(point[0]), float(point[1])])
    if polygon_area(points) <= 0.0:
        raise _input_error(f"{name} has zero area")
    return points


def _groups(region_ids: Sequence[str], overlap: Mapping[tuple[str, str], float]) -> list[list[str]]:
    """Connected components of the overlap graph."""
    parent = {region_id: region_id for region_id in region_ids}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for left, right in overlap:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a
    buckets: dict[str, list[str]] = {}
    for region_id in region_ids:
        buckets.setdefault(find(region_id), []).append(region_id)
    return [sorted(members) for members in buckets.values() if len(members) > 1]


def route_regions(
    regions: Sequence[Mapping[str, Any]],
    *,
    vlm_candidates: Mapping[str, Mapping[str, Any]] | None = None,
    review_decisions: Mapping[str, str] | None = None,
    decorative_reasons: Mapping[str, str] | None = None,
    #: Required, not defaulted: an uncalibrated number that callers can leave
    #: implicit is a policy nobody chose. Passing ``DEFAULT_CONFIDENCE_THRESHOLD``
    #: is the one grep-able way to say "I accept 0.6".
    confidence_threshold: float,
) -> ClassificationResult:
    """Route each image region to the branch that may produce facts for it."""
    candidates = dict(vlm_candidates or {})
    decisions = dict(review_decisions or {})
    reasons = dict(decorative_reasons or {})

    threshold = confidence_threshold
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0.0 < float(threshold) <= 1.0:
        raise _input_error(
            f"confidence_threshold must be in (0, 1], got {confidence_threshold!r}"
        )
    threshold = float(threshold)

    polygons: dict[str, list[list[float]]] = {}
    areas: dict[str, float] = {}
    order: list[str] = []
    raw_by_id: dict[str, Mapping[str, Any]] = {}

    for index, raw in enumerate(regions):
        if not isinstance(raw, Mapping):
            raise _input_error(f"regions[{index}] must be a mapping")
        region_id = raw.get("region_id")
        if not isinstance(region_id, str) or not region_id:
            raise _input_error(f"regions[{index}] has no usable region_id")
        if region_id in polygons:
            raise _input_error(f"duplicate region_id {region_id!r}")
        polygons[region_id] = _polygon(raw.get("polygon"), name=f"region {region_id}.polygon")
        areas[region_id] = polygon_area(polygons[region_id])
        order.append(region_id)
        raw_by_id[region_id] = raw

    overlap: dict[tuple[str, str], float] = {}
    for left_index, left in enumerate(order):
        for right in order[left_index + 1 :]:
            shared = intersection_area(polygons[left], polygons[right])
            if shared > 1e-9:
                overlap[(left, right)] = shared

    groups = _groups(order, overlap)
    owner: dict[str, str | None] = {region_id: region_id for region_id in order}
    #: Enclosing region per overlap group (containment), kept apart from the fact
    #: owner: "who contains this" and "who may produce facts" are two questions.
    container_of: dict[str, str] = {}
    group_by_region: dict[str, list[str]] = {}
    issues: list[dict[str, Any]] = []

    for group in groups:
        for region_id in group:
            group_by_region[region_id] = group
        smallest = min(group, key=lambda item: areas[item])
        container = max(group, key=lambda item: areas[item])
        for region_id in group:
            if region_id != container:
                container_of[region_id] = container
        ties = [item for item in group if abs(areas[item] - areas[smallest]) <= 1e-9]
        if len(ties) > 1:
            for region_id in group:
                owner[region_id] = None
            issues.append(
                {
                    "code": "OVERLAP_OWNER_UNDECIDED",
                    "message": (
                        "overlapping regions have identical area; no rule picks a fact "
                        "owner without a review decision"
                    ),
                    "detail": {"regions": group},
                }
            )
        else:
            for region_id in group:
                owner[region_id] = smallest if region_id != smallest else region_id

    visuals: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    for region_id in order:
        raw = raw_by_id[region_id]
        engine_label = raw.get("engine_label")
        if engine_label is not None and not isinstance(engine_label, str):
            raise _input_error(f"region {region_id}: engine_label must be a string or null")
        engine_confidence = raw.get("engine_confidence")
        if engine_confidence is not None:
            if (
                isinstance(engine_confidence, bool)
                or not isinstance(engine_confidence, (int, float))
                or not 0.0 <= float(engine_confidence) <= 1.0
            ):
                raise _input_error(
                    f"region {region_id}: engine_confidence must be in [0, 1] or null"
                )
            engine_confidence = float(engine_confidence)

        classification = "UNKNOWN"
        state = "NEEDS_REVIEW"
        #: Why this region is not a candidate. The ref handling below must not treat
        #: "I do not know what this is" and "I know, but the engine is unsure" alike:
        #: only the second one is something a human can settle.
        blocked_by = "UNKNOWN_CLASS"
        label_key = (engine_label or "").strip().lower()
        if label_key in FRAMEWORK_LABELS:
            classification = FRAMEWORK_LABELS[label_key]
            state = "CANDIDATE" if classification != "UNKNOWN" else "NEEDS_REVIEW"
            blocked_by = "UNKNOWN_CLASS" if classification == "UNKNOWN" else "NONE"
            if label_key in ("image", "figure", "picture"):
                issues.append(
                    {
                        "code": "FRAMEWORK_IMAGE_IS_NOT_FLOW",
                        "region_id": region_id,
                        "message": (
                            "a generic image label cannot prove a flow chart; the region "
                            "stays UNKNOWN until a VLM proposes a type"
                        ),
                    }
                )

        proposal = candidates.get(region_id)
        if proposal is not None:
            if not isinstance(proposal, Mapping):
                raise _input_error(f"vlm candidate for {region_id} must be a mapping")
            proposed = proposal.get("classification")
            if proposed not in CLASSES:
                raise _input_error(
                    f"region {region_id}: VLM classification must be one of {list(CLASSES)}, "
                    f"got {proposed!r}"
                )
            classification = proposed
            state = "CANDIDATE" if proposed != "UNKNOWN" else "NEEDS_REVIEW"

        # A low or missing confidence is not "confirmed by default". Before this gate
        # 0.05, 0.0 and None produced exactly the same CANDIDATE as 0.99 — a silent
        # pass for the one thing the router must not guess about.
        if classification in FACT_CLASSES and state == "CANDIDATE":
            if engine_confidence is None:
                state = "NEEDS_REVIEW"
                blocked_by = "CONFIDENCE_MISSING"
                issues.append(
                    {
                        "code": "CONFIDENCE_MISSING",
                        "region_id": region_id,
                        "message": (
                            "the engine gave no confidence for a fact-producing class; "
                            "an absent probability is not a high one"
                        ),
                    }
                )
            elif engine_confidence < threshold:
                state = "NEEDS_REVIEW"
                blocked_by = "CONFIDENCE_LOW"
                issues.append(
                    {
                        "code": "CONFIDENCE_BELOW_THRESHOLD",
                        "region_id": region_id,
                        "message": "the engine's confidence is below the routing threshold",
                        "detail": {
                            "engine_confidence": engine_confidence,
                            "threshold": threshold,
                            "threshold_calibrated": False,
                        },
                    }
                )

        if classification == "DECORATIVE":
            reason = reasons.get(region_id)
            asset = raw.get("asset_ref")
            if not reason:
                state = "NEEDS_REVIEW"
                blocked_by = "DECORATIVE_WITHOUT_REASON"
                issues.append(
                    {
                        "code": "DECORATIVE_WITHOUT_REASON",
                        "region_id": region_id,
                        "message": "filtering an asset needs a recorded reason",
                    }
                )
            if not asset:
                issues.append(
                    {
                        "code": "DECORATIVE_ASSET_KEPT",
                        "region_id": region_id,
                        "message": (
                            "no asset reference was supplied; DECORATIVE removes the region "
                            "from fact production, not the asset from the record"
                        ),
                    }
                )

        ref = decisions.get(region_id)
        if ref is not None:
            # Non-empty is not the same as usable: a blank-and-spaces ref passes a
            # truthiness test and, with the confidence override (L34), would confirm a
            # region while writing "  " into the audit trail. ``L31-FIND-02`` is the
            # same family (reviewer identity) and stays open for the Owner; this one is
            # plain input hygiene and does not need a policy decision.
            if not isinstance(ref, str) or not ref.strip():
                raise _input_error(
                    f"region {region_id}: review decision ref must be a non-blank string"
                )
            if state == "NEEDS_REVIEW" and blocked_by in (
                "CONFIDENCE_LOW",
                "CONFIDENCE_MISSING",
            ):
                # A human looking at the region can settle "yes, it is a table".
                # Losing that made low confidence unrecoverable: the only remaining
                # options were lowering the global threshold (disarming every region)
                # or re-running the engine.
                state = "CONFIRMED"
                issues.append(
                    {
                        "code": "CONFIDENCE_OVERRIDDEN",
                        "region_id": region_id,
                        "message": (
                            "a reviewer confirmed a class the engine was unsure about; "
                            "the original confidence finding is kept"
                        ),
                        "detail": {
                            "engine_confidence": engine_confidence,
                            "review_decision_ref": ref,
                            "blocked_by": blocked_by,
                        },
                    }
                )
            elif state == "NEEDS_REVIEW":
                issues.append(
                    {
                        "code": "REVIEW_REF_IGNORED",
                        "region_id": region_id,
                        "message": "an unresolved classification is not confirmed by a ref",
                        "detail": {"blocked_by": blocked_by},
                    }
                )
                ref = None
            else:
                state = "CONFIRMED"

        fact_owner = owner.get(region_id)
        is_owner = fact_owner == region_id
        if region_id in group_by_region and not is_owner:
            issues.append(
                {
                    "code": "OVERLAPPING_REGION",
                    "region_id": region_id,
                    "message": (
                        "region overlaps another one and is not the fact owner; it keeps its "
                        "evidence but produces no facts"
                    ),
                    "detail": {"group": group_by_region[region_id], "owner": fact_owner},
                }
            )

        visuals.append(
            {
                "visual_id": f"{region_id}-VIS",
                "region_evidence_id": raw.get("evidence_id") or region_id,
                "classification": classification,
                "classification_state": state,
                "engine_confidence": engine_confidence,
                "flow": None,
                "chart": None,
                "review_decision_ref": ref,
            }
        )
        routes.append(
            {
                "region_id": region_id,
                "route": ROUTES[classification],
                "fact_owner": is_owner,
                "parent_region_id": container_of.get(region_id),
                "asset_ref": raw.get("asset_ref"),
                "filter_reason": reasons.get(region_id) if classification == "DECORATIVE" else None,
            }
        )

    return ClassificationResult(visuals=visuals, routes=routes, issues=issues)
