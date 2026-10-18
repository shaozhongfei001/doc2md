"""Independent rendered outputs for the extension layer.

Everything here produces *new* artefacts under ``extension_root``. Nothing in
this package may write ``parsed/``, ``curated/`` or the legacy triple
(``parsed_text.md`` / ``parsed_metadata.json`` / ``parse_manifest.json``).
"""

from __future__ import annotations

__all__ = ["review_packet"]
