"""Extension CLI tests (L01 / T104).

The assertions fall into three groups:

1. **Exit codes** must match ``cli_contract.json`` (2 input, 3 dependency, 5 conflict).
2. **``--dry-run`` really does nothing**: no directory, no lock file, no report and
   no engine/network call. "Nothing happened" is checked against a filesystem
   snapshot, and the write guard is additionally proven to fire when a writer
   tries anyway.
3. **Honest reporting**: a command whose engine lands in a later Loop exits 3 with
   ``E_NOT_IMPLEMENTED`` instead of printing a success summary.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app.extensions import ExtensionError
from app.extensions.cli import COMMANDS, main
from app.extensions.runner import ExtensionRunner, WriteGuard
from tests.extensions.conftest import CONTENT_SHA, ParsedFixture

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _run(argv: list[str]) -> int:
    return main(argv)


def _base_argv(extension_config: Path, legacy_config: Path, *rest: str) -> list[str]:
    return [
        "--extension-config",
        str(extension_config),
        "--base-config",
        str(legacy_config),
        *rest,
    ]


def _snapshot(root: Path) -> tuple[list[str], list[str]]:
    if not root.exists():
        return ([], [])
    files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    dirs = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_dir())
    return (files, dirs)


def _locked_config(extension_config: Path, tmp_path: Path) -> Path:
    """Same config, but with profile / engine lock / patchset refs pointing at real files."""
    payload = yaml.safe_load(extension_config.read_text(encoding="utf-8"))
    refs = {}
    for name in ("profile.json", "engine_lock.json", "patchset.json"):
        path = tmp_path / "locks" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": name}), encoding="utf-8")
        refs[name] = str(path)
    payload["worker"]["profile_ref"] = refs["profile.json"]
    payload["worker"]["patchset_ref"] = refs["patchset.json"]
    payload["engines"]["structure"]["exact_environment_lock_ref"] = refs["engine_lock.json"]
    path = extension_config.parent / "doc2md_extensions_locked.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


# ------------------------------------------------------------------ input errors
def test_no_command_is_an_input_error(extension_config: Path, capsys):
    assert _run(["--extension-config", str(extension_config)]) == 2
    assert "a command is required" in capsys.readouterr().err


def test_missing_required_option_is_an_input_error(extension_config: Path):
    with pytest.raises(SystemExit) as excinfo:
        _run(["--extension-config", str(extension_config), "enhance"])
    assert excinfo.value.code == 2


def test_missing_extension_config_is_an_input_error(tmp_path: Path, capsys):
    code = _run(["--extension-config", str(tmp_path / "nope.yaml"), "--dry-run", "enhance",
                 "--content-sha", CONTENT_SHA])
    assert code == 2
    assert "E_CONFIG_INVALID" in capsys.readouterr().err


def test_unknown_config_key_is_refused(extension_config: Path, capsys):
    payload = yaml.safe_load(extension_config.read_text(encoding="utf-8"))
    payload["policy_typo"] = {"numeric_chart_estimates_enabled": True}
    extension_config.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    code = _run(["--extension-config", str(extension_config), "--dry-run", "enhance",
                 "--content-sha", CONTENT_SHA])
    assert code == 2
    assert "unknown top-level configuration keys" in capsys.readouterr().err


def test_numeric_estimate_policy_cannot_be_enabled_by_editing_config(
    extension_config: Path, capsys
):
    payload = yaml.safe_load(extension_config.read_text(encoding="utf-8"))
    payload["policy"]["numeric_chart_estimates_enabled"] = True
    extension_config.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    code = _run(["--extension-config", str(extension_config), "--dry-run", "enhance",
                 "--content-sha", CONTENT_SHA])
    assert code == 2
    assert "OI05" in capsys.readouterr().err


def test_extension_root_overlapping_parsed_is_refused(
    extension_config: Path, legacy_app_config: Path, capsys
):
    payload = yaml.safe_load(extension_config.read_text(encoding="utf-8"))
    payload["extension_root"] = "./parsed/nested"
    extension_config.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    code = _run(_base_argv(extension_config, legacy_app_config, "--dry-run", "enhance",
                           "--content-sha", CONTENT_SHA))
    assert code == 2
    assert "E_CONFIG_ROOT_FORBIDDEN" in capsys.readouterr().err


# ------------------------------------------------------------------- dry-run out
def test_dry_run_enhance_reports_blockers_and_writes_nothing(
    extension_config: Path,
    legacy_app_config: Path,
    parsed_fixture: ParsedFixture,
    extension_root: Path,
):
    before = _snapshot(extension_root)
    code = _run(_base_argv(extension_config, legacy_app_config, "--dry-run", "enhance",
                           "--content-sha", CONTENT_SHA, "--output-json"))
    assert code == 3, "unlocked engines must not be reported as a complete plan"
    after = _snapshot(extension_root)
    assert before == after == ([], [])
    assert not (extension_root / ".locks").exists()


def test_dry_run_with_locks_produces_a_job_key_and_still_writes_nothing(
    extension_config: Path,
    legacy_app_config: Path,
    parsed_fixture: ParsedFixture,
    extension_root: Path,
    capsys,
):
    locked = _locked_config(extension_config, extension_config.parent.parent)
    code = _run(_base_argv(locked, legacy_app_config, "--dry-run", "enhance",
                           "--content-sha", CONTENT_SHA, "--output-json"))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["blockers"] == []
    assert len(payload["job_key"]) == 64
    assert payload["read_set"], "the plan must show the verified read-set"
    assert payload["executed"] is False and payload["dry_run"] is True
    assert _snapshot(extension_root) == ([], [])


def test_dry_run_enhance_without_base_parse_is_a_dependency_error(
    extension_config: Path, legacy_app_config: Path, capsys
):
    code = _run(_base_argv(extension_config, legacy_app_config, "--dry-run", "enhance",
                           "--content-sha", "1" * 64, "--output-json"))
    assert code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "NEEDS_BASE_PARSE"


def test_dry_run_without_base_config_reports_the_missing_bridge(
    extension_config: Path, capsys
):
    code = _run(["--extension-config", str(extension_config), "--dry-run", "enhance",
                 "--content-sha", CONTENT_SHA, "--output-json"])
    assert code == 2
    assert "E_CONFIG_INVALID" in json.loads(capsys.readouterr().out)["error"]["code"]


def test_dry_run_source_register_validates_identity(
    extension_config: Path, legacy_app_config: Path, write_json, capsys
):
    vectors = json.loads(
        (BACKEND_ROOT.parent / "contracts/doc2md-19cap/v1.0.0/identity_vectors.json").read_text(
            encoding="utf-8"
        )
    )
    source = {
        "schema_version": "source-version/1.0",
        "source_version_id": vectors["source_version"]["expected_id"],
        "source_id": "demo_bank_policy",
        "external_object_id": "policy_demo_01",
        "version_kind": "LOCAL_SNAPSHOT",
        "source_revision": None,
        "snapshot_id": "demo_snapshot_01",
        "content_sha256": "953b60cf03a3b48258ecae1785963e5064535cb80c74efe9a7e992c28da44ef0",
        "legacy_query_ref": None,
        "processing_route": "REPROCESS",
        "source_kind": "BANK_RULE",
        "authority_class": "BANK_EFFECTIVE_RULE",
        "permission_ref": "perm-1",
        "owner_ref": "owner-1",
        "classification": "INTERNAL",
        "purpose_scopes": ["PREVISIT"],
        "license_ref": None,
        "valid_from": None,
        "valid_to": None,
        "source_file_ref": {
            "root": "RAW_VAULT",
            "relative_path": "by_hash/95/3b/original.bin",
            "sha256": "953b60cf03a3b48258ecae1785963e5064535cb80c74efe9a7e992c28da44ef0",
        },
    }
    path = write_json("source-version.json", source)
    code = _run([
        "--extension-config", str(extension_config),
        "--base-config", str(legacy_app_config),
        "--dry-run", "source-register",
        "--source-version-file", str(path), "--output-json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["identity_matches"] is True
    assert payload["computed_source_version_id"] == source["source_version_id"]


def test_source_register_identity_mismatch_is_a_version_conflict(
    extension_config: Path, legacy_app_config: Path, write_json, capsys
):
    vectors = json.loads(
        (BACKEND_ROOT.parent / "contracts/doc2md-19cap/v1.0.0/identity_vectors.json").read_text(
            encoding="utf-8"
        )
    )
    source = json.loads(
        (BACKEND_ROOT.parent
         / "contracts/doc2md-19cap/v1.0.0/examples/source-version.example.json").read_text(
            encoding="utf-8")
    )
    assert vectors  # fixture sanity: identity vectors are readable next to the examples
    source["source_version_id"] = "sv-" + "0" * 64
    path = write_json("bad-source-version.json", source)
    code = _run([
        "--extension-config", str(extension_config),
        "--base-config", str(legacy_app_config),
        "--dry-run", "source-register",
        "--source-version-file", str(path), "--output-json",
    ])
    assert code == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload["blockers"] == ["SOURCE_VERSION_ID_MISMATCH"]


def test_source_register_rejects_a_payload_violating_the_closed_contract(
    extension_config: Path, legacy_app_config: Path, write_json, capsys
):
    source = json.loads(
        (BACKEND_ROOT.parent
         / "contracts/doc2md-19cap/v1.0.0/examples/source-version.example.json").read_text(
            encoding="utf-8")
    )
    source["extra_field"] = "not allowed"
    path = write_json("invalid-source-version.json", source)
    code = _run([
        "--extension-config", str(extension_config),
        "--base-config", str(legacy_app_config),
        "--dry-run", "source-register",
        "--source-version-file", str(path), "--output-json",
    ])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_CONTRACT_INVALID"


# ------------------------------------------------------- not-implemented honesty
@pytest.mark.parametrize(
    "command,extra",
    [
        ("source-sync", ["--source-id", "s1"]),
        ("review-export", ["--generation-id", "g1", "--purpose", "REVIEW"]),
        ("patch-preview", ["--patch-file", "x.json", "--expected-current", "g1"]),
        ("patch-apply", ["--patch-file", "x.json", "--review-ref", "r1"]),
        ("release-prepare", ["--reconcile-report", "r.json"]),
        ("route-switch", ["--release-id", "r1", "--expected-current", "r0", "--scope", "s"]),
        ("route-rollback", ["--previous-release", "r1", "--expected-current", "r2",
                            "--scope", "s"]),
        ("candidate-get", ["--candidate-id", "c1", "--generation-id", "g1", "--purpose", "P"]),
        ("outbox-dispatch", ["--consumer-id", "c1"]),
    ],
)
def test_unimplemented_engine_exits_three_in_dry_run(
    command: str, extra: list[str], extension_config: Path, legacy_app_config: Path,
    extension_root: Path, capsys
):
    code = _run(_base_argv(extension_config, legacy_app_config, "--dry-run", command, "--output-json",
                           *extra))
    payload = json.loads(capsys.readouterr().out)
    assert code == 3
    assert payload["error"]["code"] == "E_NOT_IMPLEMENTED"
    assert payload["error"]["detail"]["implemented_in"] == COMMANDS[command][0]
    assert _snapshot(extension_root) == ([], [])


def test_real_execution_of_enhance_is_refused(
    extension_config: Path, legacy_app_config: Path, parsed_fixture: ParsedFixture,
    extension_root: Path, capsys
):
    code = _run(_base_argv(extension_config, legacy_app_config, "enhance",
                           "--content-sha", CONTENT_SHA, "--output-json"))
    payload = json.loads(capsys.readouterr().out)
    assert code == 3
    assert payload["error"]["code"] == "E_NOT_IMPLEMENTED"
    assert _snapshot(extension_root) == ([], [])


# ------------------------------------------------------------------ write guard
def test_write_guard_blocks_store_writes_under_dry_run(
    extension_config: Path, legacy_app_config: Path
):
    runner = ExtensionRunner(
        extension_config_path=extension_config,
        base_config_path=legacy_app_config,
        dry_run=True,
    )
    store = runner.store()
    with pytest.raises(ExtensionError) as excinfo:
        store.lock("generation_abc")
    assert excinfo.value.code == "E_DRY_RUN_WRITE_BLOCKED"
    with pytest.raises(ExtensionError) as excinfo:
        store.write_staged("attempt-1", "x.json", b"{}")
    assert excinfo.value.code == "E_DRY_RUN_WRITE_BLOCKED"
    assert not (runner.roots.extension / ".locks").exists()


def test_write_guard_is_not_armed_outside_dry_run(
    extension_config: Path, legacy_app_config: Path
):
    runner = ExtensionRunner(
        extension_config_path=extension_config,
        base_config_path=legacy_app_config,
        dry_run=False,
    )
    store = runner.store()
    staged = store.write_staged("attempt-1", "x.json", b"{}")
    assert staged.size_bytes == 2


def test_write_guard_records_attempts_for_evidence():
    guard = WriteGuard(dry_run=True)
    with pytest.raises(ExtensionError):
        guard.check("objects/by_hash/aa")
    assert guard.attempts == ["objects/by_hash/aa"]


# -------------------------------------------------------------- module entrypoint
def test_module_entrypoint_runs_a_dry_run(
    extension_config: Path, legacy_app_config: Path, parsed_fixture: ParsedFixture
):
    completed = subprocess.run(
        [
            sys.executable, "-m", "app.extensions.cli",
            "--extension-config", str(extension_config),
            "--base-config", str(legacy_app_config),
            "--dry-run", "enhance", "--content-sha", CONTENT_SHA, "--output-json",
        ],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(BACKEND_ROOT), "PATH": "/usr/bin:/bin"},
    )
    assert completed.returncode == 3, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["command"] == "enhance"
    assert completed.stderr == "", "stdout/stderr must stay separable for piping"


# ------------------------------------- narrow storage loader (DB review finding 3)
def _app_yaml(tmp_path: Path, *, with_mysql: bool, secret: str = "s3cr3t-marker") -> Path:
    workspace = tmp_path / "ws"
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in ("raw_vault", "parsed", "curated", "registry", "quarantine", "reports"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    body = (
        "app:\n  pipeline_version: fixture\n"
        "storage:\n"
        "  source_registry_root: ./registry\n"
        "  raw_vault_root: ./raw_vault\n"
        "  parsed_root: ./parsed\n"
        "  curated_root: ./curated\n"
        "  quarantine_root: ./quarantine\n"
        "  reports_root: ./reports\n"
    )
    if with_mysql:
        body += (
            "mysql:\n"
            "  host: 127.0.0.1\n  port: 3306\n  database: fixture\n"
            "  username: fixture\n"
            f"  password: {secret}\n  charset: utf8mb4\n"
        )
    path = config_dir / "app.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_storage_loader_reads_paths_from_the_legacy_config(tmp_path: Path):
    from app.extensions.io.config import load_storage_roots

    path = _app_yaml(tmp_path, with_mysql=True)
    roots = load_storage_roots(path)
    assert roots.parsed_root == (path.parent.parent / "parsed").resolve()
    assert roots.raw_vault_root == (path.parent.parent / "raw_vault").resolve()


def test_storage_loader_never_retains_the_mysql_section(tmp_path: Path):
    """A plaintext password must not be retained or exposed by this layer.

    Precise scope: ``yaml.safe_load`` still materialises the whole document, so the
    secret transits memory during the parse. What is asserted here is the property
    that matters — it is not kept on, attached to, or serialisable from the returned
    object. The name says "retains", not "parses", on purpose.
    """
    from app.extensions.io.config import load_storage_roots

    path = _app_yaml(tmp_path, with_mysql=True, secret="UNIQUE-PASSWORD-MARKER-42")
    roots = load_storage_roots(path)
    rendered = repr(roots)
    assert "UNIQUE-PASSWORD-MARKER-42" not in rendered
    assert not hasattr(roots, "password")
    assert not hasattr(roots, "mysql")
    # No field of the returned dataclass may carry the mysql subtree either.
    for name, value in vars(roots).items():
        assert "UNIQUE-PASSWORD-MARKER-42" not in repr(value), name
        assert not isinstance(value, dict) or "mysql" not in value, name


def test_storage_loader_works_without_a_mysql_section(tmp_path: Path):
    """The legacy loader raises KeyError here; the extension layer must not need MySQL."""
    from app.extensions.io.config import load_storage_roots

    roots = load_storage_roots(_app_yaml(tmp_path, with_mysql=False))
    assert roots.parsed_root is not None


def test_storage_loader_rejects_a_config_without_storage(tmp_path: Path):
    from app.extensions.io.config import load_storage_roots

    path = tmp_path / "config" / "app.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("app:\n  pipeline_version: x\n", encoding="utf-8")
    with pytest.raises(ExtensionError) as excinfo:
        load_storage_roots(path)
    assert excinfo.value.code == "E_CONFIG_INVALID"
    assert "storage" in excinfo.value.message


def test_extension_layer_no_longer_imports_legacy_config():
    """The narrow loader removed the last legacy import from this namespace."""
    import app.extensions.runner as runner_module

    source = Path(runner_module.__file__).read_text(encoding="utf-8")
    import_lines = [
        line for line in source.splitlines() if line.strip().startswith(("import ", "from "))
    ]
    assert not any("app.core.config" in line for line in import_lines)
    assert not any("app.core" in line for line in import_lines)
