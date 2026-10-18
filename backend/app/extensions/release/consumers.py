"""Outbox events and idempotent ACK handling (SDD 12.2, ``PI-F04-09``).

SDD 12.2:

    "失效和重建事件保存outbox，consumer按event_id幂等；收到ACK才标成功，
     consumer回报必须绑定目标generation和消费版本。未连接的外部系统输出NOT_CONNECTED及
     契约文件，不产生'已重建'结论。"

Everything here follows from that sentence:

* an event is ``PENDING`` until an ACK names **the same generation** it was emitted
  for — an ACK from a consumer that was still reading the previous generation does
  not mark the new one delivered;
* ACKs are idempotent by ``event_id`` + ``ack_token``: replaying the same ACK is a
  no-op success, while a *different* token for an already-ACKed event is a conflict
  (two consumers claiming one event);
* a target that is not connected produces ``NOT_CONNECTED`` and never ``ACKED``. No
  code path here fabricates a success for a consumer that does not exist.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .. import ExtensionError

STATUS_PENDING = "PENDING"
STATUS_ACKED = "ACKED"
STATUS_NOT_CONNECTED = "NOT_CONNECTED"

__all__ = [
    "STATUS_ACKED",
    "STATUS_NOT_CONNECTED",
    "STATUS_PENDING",
    "Outbox",
]


def _input_error(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_INPUT_INVALID", message, detail=detail or None)


def _conflict(message: str, **detail: Any) -> ExtensionError:
    return ExtensionError("E_VERSION_CONFLICT", message, detail=detail or None)


@dataclass
class Outbox:
    """In-memory outbox: enqueue, ACK, and report connected/unconnected targets."""

    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    acknowledged: dict[str, dict[str, Any]] = field(default_factory=dict)

    def enqueue(
        self,
        *,
        event_id: str,
        generation_id: str,
        event_type: str,
        payload: Any,
        target: str | None = None,
    ) -> dict[str, Any]:
        """Append an event; a replay of the same id returns the original."""
        for name, value in (("event_id", event_id), ("generation_id", generation_id), ("event_type", event_type)):
            if not isinstance(value, str) or not value:
                raise _input_error(f"{name} must be a non-empty string, got {value!r}")
        if target is not None and (not isinstance(target, str) or not target):
            raise _input_error(f"target must be a non-empty string or null, got {target!r}")

        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        existing = self.events.get(event_id)
        if existing is not None:
            if existing["payload_digest"] != digest or existing["generation_id"] != generation_id:
                raise _conflict(
                    "the same event_id was enqueued with different content; ids must be "
                    "stable or idempotency is meaningless",
                    event_id=event_id,
                )
            return dict(existing)

        event = {
            "event_id": event_id,
            "generation_id": generation_id,
            "event_type": event_type,
            "target": target,
            "payload_digest": digest,
            "status": STATUS_PENDING,
        }
        self.events[event_id] = event
        return dict(event)

    def ack(
        self,
        event_id: str,
        *,
        generation_id: str,
        consumer_version: str,
        ack_token: str,
    ) -> dict[str, Any]:
        """Record a consumer's ACK, refusing stale generations and token swaps."""
        event = self.events.get(event_id)
        if event is None:
            raise _input_error(f"unknown event_id {event_id!r}; an ACK cannot create an event")
        for name, value in (
            ("generation_id", generation_id),
            ("consumer_version", consumer_version),
            ("ack_token", ack_token),
        ):
            if not isinstance(value, str) or not value:
                raise _input_error(f"{name} must be a non-empty string, got {value!r}")

        if generation_id != event["generation_id"]:
            raise _conflict(
                "the ACK names a different generation than the event; the consumer is "
                "reporting on content this event does not describe",
                event_id=event_id,
                event_generation=event["generation_id"],
                ack_generation=generation_id,
            )

        previous = self.acknowledged.get(event_id)
        if previous is not None:
            if previous["ack_token"] == ack_token:
                return dict(previous)  # replay: idempotent success
            raise _conflict(
                "the event was already acknowledged with a different token; two consumers "
                "cannot own one event",
                event_id=event_id,
                previous_token=previous["ack_token"],
                new_token=ack_token,
            )

        record = {
            "event_id": event_id,
            "generation_id": generation_id,
            "consumer_version": consumer_version,
            "ack_token": ack_token,
            "status": STATUS_ACKED,
        }
        self.acknowledged[event_id] = record
        self.events[event_id]["status"] = STATUS_ACKED
        return dict(record)

    def report(self, *, connected: Iterable[str]) -> dict[str, Any]:
        """Per-target delivery status; unconnected targets are ``NOT_CONNECTED``."""
        connected_set = {str(name) for name in connected}
        rows: list[dict[str, Any]] = []
        for event in self.events.values():
            target = event.get("target")
            if target is None:
                rows.append(dict(event))
                continue
            if target not in connected_set:
                rows.append(
                    {
                        **event,
                        "status": STATUS_NOT_CONNECTED,
                        "note": (
                            "no consumer is connected for this target; nothing was delivered "
                            "and no rebuild is claimed"
                        ),
                    }
                )
            else:
                rows.append(dict(event))
        return {
            "events": rows,
            "pending": sum(1 for row in rows if row["status"] == STATUS_PENDING),
            "acked": sum(1 for row in rows if row["status"] == STATUS_ACKED),
            "not_connected": sum(1 for row in rows if row["status"] == STATUS_NOT_CONNECTED),
        }
