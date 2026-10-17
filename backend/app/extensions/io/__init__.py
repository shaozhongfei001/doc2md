"""I/O layer of the DOC2MD-19CAP extension.

``config``         extension configuration loading (fail-closed, no silent defaults)
``paths``          restricted ``extension_root``, traversal/symlink guards, FileRef mapping
``read_bridge``    read-only access to the legacy triple artifacts + read-set snapshot
``artifact_store`` single-writer lock, staging, durable commit, pointer CAS, recovery

Nothing in this package may write outside ``extension_root``.
"""

from __future__ import annotations

__all__ = ["config", "paths", "read_bridge", "artifact_store"]
