"""Independent thin CLI for the DOC2MD-19CAP extension layer.

Run with ``python -m app.extensions.cli <command> [options]``. The 19 legacy
commands in ``app.cli.main`` are untouched (``cli_contract.json`` declares
``old_cli_modified: false``).

Contract surface
----------------
Common flags: ``--base-config``, ``--extension-config``, ``--dry-run``,
``--output-json``.
Operations: ``source-register``, ``source-sync``, ``enhance``, ``review-export``,
``patch-preview``, ``patch-apply``, ``release-prepare``, ``route-switch``,
``route-rollback``, ``candidate-get``, ``outbox-dispatch``.

Exit codes (``cli_contract.json`` → ``error_exit_codes``):
``0`` request completed · ``2`` input error · ``3`` dependency/environment missing ·
``4`` needs review / requested release state not reached · ``5`` version conflict ·
``6`` failed item or batch · ``7`` source or permission denied.

Honesty rules
-------------
* ``--dry-run`` never creates a directory, lock file or report and never contacts an
  engine or the network; the write guard in ``runner`` enforces this.
* A command whose engine belongs to a later Loop exits ``3`` with
  ``E_NOT_IMPLEMENTED``. It never prints a success summary for work it did not do.
* ``--output-json`` writes to stdout only; stdout stays free of log noise so a
  caller can pipe it safely.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from . import ExtensionError, WORKFLOW_ID
from .contracts import ContractError
from .identity import IdentityError
from .runner import ExtensionRunner

__all__ = ["main", "build_parser", "COMMANDS"]

#: command → (stage that implements it, short description of the pending engine)
COMMANDS: dict[str, tuple[str, str]] = {
    "source-register": ("L02", "source version persistence and permission bookkeeping"),
    "source-sync": ("L02", "event page commit, cursor durability and revoke tombstones"),
    "enhance": ("L03+", "native Word / scanned table / geometry engines"),
    "review-export": ("L05/L07", "review packet rendering"),
    "patch-preview": ("L07", "RFC6902 preview and dependency impact"),
    "patch-apply": ("L07", "version-bound patch application"),
    "release-prepare": ("L08", "reconcile-driven release preparation"),
    "route-switch": ("L08", "route pointer compare-and-swap"),
    "route-rollback": ("L08", "scope-limited rollback that keeps revocations"),
    "candidate-get": ("L07", "permission-filtered candidate read"),
    "outbox-dispatch": ("L07", "consumer ACK / NOT_CONNECTED dispatch"),
}


def _add_common_flags(parser: argparse.ArgumentParser, *, suppress_defaults: bool) -> None:
    """Register the four contract flags.

    They are declared on the top-level parser *and* on every subparser (with
    ``SUPPRESS`` defaults) so ``--dry-run`` works before or after the command
    without the subparser default clobbering the root value.
    """
    base_default = argparse.SUPPRESS if suppress_defaults else None
    flag_default = argparse.SUPPRESS if suppress_defaults else False
    parser.add_argument(
        "--base-config",
        default=base_default,
        help="legacy app.yaml used only to read storage roots (read-only)",
    )
    parser.add_argument(
        "--extension-config",
        default=base_default,
        help="extension configuration YAML (see config/doc2md_extensions.example.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=flag_default,
        help="plan only: no directory, lock, report, engine or network side effect",
    )
    parser.add_argument(
        "--output-json",
        action="store_true",
        default=flag_default,
        help="emit a single JSON document on stdout",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.extensions.cli",
        description=(
            f"DOC2MD-19CAP extension CLI ({WORKFLOW_ID}). "
            "The legacy 19 commands remain available through app.cli.main."
        ),
    )
    _add_common_flags(parser, suppress_defaults=False)

    subparsers = parser.add_subparsers(dest="command")

    def _sub(name: str, help_text: str) -> argparse.ArgumentParser:
        child = subparsers.add_parser(name, help=help_text)
        _add_common_flags(child, suppress_defaults=True)
        return child

    register = _sub("source-register", "validate/register a source version")
    register.add_argument("--source-version-file", required=True)

    sync = _sub("source-sync", "commit an event page and advance the cursor")
    sync.add_argument("--source-id", required=True)
    sync.add_argument("--cursor", default=None)
    sync.add_argument("--mode", default="incremental", choices=("incremental", "full"))

    enhance = _sub("enhance", "resolve base artifacts and plan a generation")
    enhance.add_argument("--content-sha", required=True)
    enhance.add_argument("--source-version-id", action="append", default=[])

    review_export = _sub("review-export", "export a local review packet")
    review_export.add_argument("--generation-id", required=True)
    review_export.add_argument("--purpose", required=True)

    patch_preview = _sub("patch-preview", "preview a correction patch")
    patch_preview.add_argument("--patch-file", required=True)
    patch_preview.add_argument("--expected-current", required=True)

    patch_apply = _sub("patch-apply", "apply a reviewed correction patch")
    patch_apply.add_argument("--patch-file", required=True)
    patch_apply.add_argument("--review-ref", required=True)

    release_prepare = _sub("release-prepare", "prepare a release manifest")
    release_prepare.add_argument("--reconcile-report", required=True)
    release_prepare.add_argument("--required-consumer", action="append", default=[])

    route_switch = _sub("route-switch", "switch the route pointer (CAS)")
    route_switch.add_argument("--release-id", required=True)
    route_switch.add_argument("--expected-current", required=True)
    route_switch.add_argument("--scope", required=True)

    route_rollback = _sub("route-rollback", "roll back a scope-limited route")
    route_rollback.add_argument("--previous-release", required=True)
    route_rollback.add_argument("--expected-current", required=True)
    route_rollback.add_argument("--scope", required=True)

    candidate_get = _sub("candidate-get", "read a permission-filtered candidate")
    candidate_get.add_argument("--candidate-id", required=True)
    candidate_get.add_argument("--generation-id", required=True)
    candidate_get.add_argument("--purpose", required=True)

    outbox = _sub("outbox-dispatch", "dispatch outbox events to a consumer")
    outbox.add_argument("--consumer-id", required=True)

    return parser


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for key in ("command", "executed", "dry_run", "exit_code", "plan"):
        if key in payload:
            print(f"{key}: {payload[key]}")
    blockers = payload.get("blockers") or []
    if blockers:
        print("blockers:")
        for blocker in blockers:
            print(f"  - {blocker}")
    note = payload.get("note")
    if note:
        print(f"note: {note}")


def _fail(error: ExtensionError, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(
            {
                "schema_version": "doc2md-19cap-cli-error/1.0",
                "ok": False,
                "error": error.to_dict(),
                "exit_code": error.exit_code,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
    else:
        print(f"error[{error.code}]: {error.message}", file=sys.stderr)
    return error.exit_code


def _dispatch(runner: ExtensionRunner, args: argparse.Namespace):
    command = args.command
    if command == "source-register":
        return runner.plan_source_register(args.source_version_file)
    if command == "enhance":
        return runner.plan_enhance(
            args.content_sha, source_version_ids=args.source_version_id or []
        )
    stage, reason = COMMANDS[command]
    runner.plan_not_implemented(command, stage=stage, reason=reason)
    raise AssertionError("unreachable")  # pragma: no cover


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_usage(sys.stderr)
        print("error[E_INPUT_INVALID]: a command is required", file=sys.stderr)
        return 2

    as_json = bool(args.output_json)
    if not args.extension_config:
        message = "--extension-config is required"
        if as_json:
            print(json.dumps(
                {"ok": False, "error": {"code": "E_CONFIG_INVALID", "message": message},
                 "exit_code": 2}, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"error[E_CONFIG_INVALID]: {message}", file=sys.stderr)
        return 2
    try:
        runner = ExtensionRunner(
            extension_config_path=args.extension_config,
            base_config_path=args.base_config,
            dry_run=bool(args.dry_run),
        )
        if not args.dry_run:
            stage, reason = COMMANDS[args.command]
            raise ExtensionError(
                "E_NOT_IMPLEMENTED",
                f"{args.command} requires a real execution engine that lands in {stage}; "
                "re-run with --dry-run for the read-only plan",
                detail={"command": args.command, "implemented_in": stage, "pending": reason},
            )
        plan = _dispatch(runner, args)
        result: dict[str, Any] = {
            "schema_version": "doc2md-19cap-cli-result/1.0",
            "ok": plan.exit_code == 0,
            "command": plan.command,
            "executed": plan.executed,
            "dry_run": plan.dry_run,
            "exit_code": plan.exit_code,
            **plan.payload,
        }
        _emit(result, as_json=as_json)
        return plan.exit_code
    except ExtensionError as error:
        return _fail(error, as_json=as_json)
    except (IdentityError, ContractError) as error:  # defensive: both carry codes
        wrapped = ExtensionError(getattr(error, "code", "E_INPUT_INVALID"), str(error))
        return _fail(wrapped, as_json=as_json)


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    sys.exit(main())
