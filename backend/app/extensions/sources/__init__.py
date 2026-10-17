"""Source version, authority and resumable synchronisation (Loop L02).

Modules
-------
``base``            ``SourceAdapter`` protocol, capability probing, ``EventPage``
``local_manifest``  local manifest connector (the only adapter usable without a bank interface)
``versioning``      source version registration: native revision vs local snapshot, conflicts
``authority``       source kind / authority class / licence / purpose / validity rules
``access``          access ledger: revocation, leases, tombstones
``sync``            page commit with durable cursor and anti-resurrection
``reconcile``       inventory reconciliation and shadow routing

Boundary
--------
Nothing here talks to a bank system. A source without an interface is reported as
``NOT_CONNECTED`` / ``UNSUPPORTED`` — never as an empty successful sync.
"""

from __future__ import annotations

__all__ = [
    "base",
    "local_manifest",
    "versioning",
    "authority",
    "access",
    "sync",
    "reconcile",
]
