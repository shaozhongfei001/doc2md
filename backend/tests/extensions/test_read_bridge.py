"""Read bridge and path-guard tests (L01 / T102 + T105).

The point of these tests is that the bridge **refuses** to build on a base it
cannot prove:

* a missing directory or member must be ``NEEDS_BASE_PARSE``, never an empty
  document;
* a digest that moved must be ``E_EVIDENCE_MISMATCH``, never a fallback to the
  previous cached value;
* reading must leave the legacy tree byte-identical;
* path resolution must reject traversal, absolute paths and symlinked escapes
  even when the symlink points *inside* the root.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extensions import ExtensionError
from app.extensions.io.paths import (
    FORBIDDEN_WRITE_ROOTS,
    Roots,
    assert_writable,
    resolve_extension_root,
    safe_join,
    validate_relative_path,
)
from app.extensions.io.read_bridge import ReadBridge, parsed_dir_for, sha256_file
from tests.extensions.conftest import CONTENT_SHA, ParsedFixture, build_parsed_tree


def _bridge(parsed_root: Path, extension_root: Path) -> ReadBridge:
    return ReadBridge(Roots(extension=extension_root, parsed=parsed_root))


def _dir_state(path: Path) -> dict[str, tuple[str, int]]:
    return {
        str(item.relative_to(path)): (sha256_file(item), item.stat().st_size)
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


# ------------------------------------------------------------------ happy path
def test_resolve_base_returns_verified_read_set(parsed_fixture: ParsedFixture, tmp_path: Path):
    bridge = _bridge(parsed_fixture.parsed_root, tmp_path / "ext")
    artifacts = bridge.resolve_base(CONTENT_SHA)

    names = {entry.relative_path.split("/")[-1] for entry in artifacts.read_set}
    assert {"parsed_text.md", "parsed_metadata.json", "parse_manifest.json"} <= names
    assert "fixture.png" in names
    assert "content_list.json" in names
    assert "document_ir.json" in names

    assert artifacts.ir_available is True
    assert artifacts.asset_count == 1
    assert artifacts.ir_source_count == 1
    assert artifacts.manifest["status"] == "SUCCESS"

    keys = [(entry.root, entry.relative_path) for entry in artifacts.read_set]
    assert keys == sorted(keys), "read-set must be ordered deterministically for job_key"


def test_read_set_is_stable_across_calls(parsed_fixture: ParsedFixture, tmp_path: Path):
    bridge = _bridge(parsed_fixture.parsed_root, tmp_path / "ext")
    first = bridge.resolve_base(CONTENT_SHA).read_set_digest_input()
    second = bridge.resolve_base(CONTENT_SHA).read_set_digest_input()
    assert first == second


def test_ir_absence_is_not_an_error(tmp_path: Path):
    fixture = build_parsed_tree(tmp_path / "parsed", with_ir=False, with_assets=False)
    bridge = _bridge(fixture.parsed_root, tmp_path / "ext")
    artifacts = bridge.resolve_base(CONTENT_SHA)
    assert artifacts.ir_available is False
    assert artifacts.ir_path is None
    assert artifacts.asset_count == 0


def test_reading_leaves_the_legacy_tree_byte_identical(
    parsed_fixture: ParsedFixture, tmp_path: Path
):
    before = _dir_state(parsed_fixture.parsed_root)
    bridge = _bridge(parsed_fixture.parsed_root, tmp_path / "ext")
    bridge.resolve_base(CONTENT_SHA)
    after = _dir_state(parsed_fixture.parsed_root)
    assert before == after


# --------------------------------------------------------------------- refusals
def test_missing_parsed_directory_is_needs_base_parse(tmp_path: Path):
    bridge = _bridge(tmp_path / "parsed", tmp_path / "ext")
    with pytest.raises(ExtensionError) as excinfo:
        bridge.resolve_base(CONTENT_SHA)
    assert excinfo.value.code == "NEEDS_BASE_PARSE"
    assert excinfo.value.exit_code == 3


def test_incomplete_triple_is_needs_base_parse(parsed_fixture: ParsedFixture, tmp_path: Path):
    (parsed_fixture.parsed_dir / "parsed_metadata.json").unlink()
    bridge = _bridge(parsed_fixture.parsed_root, tmp_path / "ext")
    with pytest.raises(ExtensionError) as excinfo:
        bridge.resolve_base(CONTENT_SHA)
    assert excinfo.value.code == "NEEDS_BASE_PARSE"
    assert "parsed_metadata.json" in str(excinfo.value.detail)


def test_non_success_manifest_is_not_a_usable_base(tmp_path: Path):
    fixture = build_parsed_tree(tmp_path / "parsed", status="FAILED")
    bridge = _bridge(fixture.parsed_root, tmp_path / "ext")
    with pytest.raises(ExtensionError) as excinfo:
        bridge.resolve_base(CONTENT_SHA)
    assert excinfo.value.code == "NEEDS_BASE_PARSE"


def test_manifest_describing_another_content_is_refused(
    parsed_fixture: ParsedFixture, tmp_path: Path
):
    manifest = json.loads(parsed_fixture.manifest_path.read_text(encoding="utf-8"))
    manifest["content_uid"] = "f" * 64
    parsed_fixture.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    bridge = _bridge(parsed_fixture.parsed_root, tmp_path / "ext")
    with pytest.raises(ExtensionError) as excinfo:
        bridge.resolve_base(CONTENT_SHA)
    assert excinfo.value.code == "E_EVIDENCE_MISMATCH"


def test_changed_parsed_text_is_an_evidence_mismatch(
    parsed_fixture: ParsedFixture, tmp_path: Path
):
    parsed_fixture.text_path.write_text("# tampered\n", encoding="utf-8")
    bridge = _bridge(parsed_fixture.parsed_root, tmp_path / "ext")
    with pytest.raises(ExtensionError) as excinfo:
        bridge.resolve_base(CONTENT_SHA)
    assert excinfo.value.code == "E_EVIDENCE_MISMATCH"
    assert "output_hash" in excinfo.value.message


def test_changed_asset_body_is_an_evidence_mismatch(parsed_fixture: ParsedFixture, tmp_path: Path):
    (parsed_fixture.parsed_dir / "images" / "fixture.png").write_bytes(b"different bytes")
    bridge = _bridge(parsed_fixture.parsed_root, tmp_path / "ext")
    with pytest.raises(ExtensionError) as excinfo:
        bridge.resolve_base(CONTENT_SHA)
    assert excinfo.value.code == "E_EVIDENCE_MISMATCH"
    assert "asset" in excinfo.value.message


def test_missing_declared_asset_is_an_evidence_mismatch(
    parsed_fixture: ParsedFixture, tmp_path: Path
):
    (parsed_fixture.parsed_dir / "images" / "fixture.png").unlink()
    bridge = _bridge(parsed_fixture.parsed_root, tmp_path / "ext")
    with pytest.raises(ExtensionError) as excinfo:
        bridge.resolve_base(CONTENT_SHA)
    assert excinfo.value.code == "E_EVIDENCE_MISMATCH"


def test_unreadable_manifest_is_needs_base_parse(parsed_fixture: ParsedFixture, tmp_path: Path):
    parsed_fixture.manifest_path.write_text("{not json", encoding="utf-8")
    bridge = _bridge(parsed_fixture.parsed_root, tmp_path / "ext")
    with pytest.raises(ExtensionError) as excinfo:
        bridge.resolve_base(CONTENT_SHA)
    assert excinfo.value.code == "NEEDS_BASE_PARSE"


def test_content_sha_must_be_hexadecimal(tmp_path: Path):
    bridge = _bridge(tmp_path / "parsed", tmp_path / "ext")
    with pytest.raises(ExtensionError) as excinfo:
        bridge.resolve_base("z" * 64)
    assert excinfo.value.code == "E_INPUT_INVALID"


def test_parsed_dir_derivation_matches_legacy_layout(tmp_path: Path):
    directory = parsed_dir_for(tmp_path, CONTENT_SHA)
    assert directory == tmp_path / "by_hash" / CONTENT_SHA[:2] / CONTENT_SHA[2:4] / CONTENT_SHA


# -------------------------------------------------------------------- snapshot
def test_snapshot_copies_read_set_inside_extension_root(
    parsed_fixture: ParsedFixture, tmp_path: Path
):
    extension_root = tmp_path / "ext"
    bridge = _bridge(parsed_fixture.parsed_root, extension_root)
    artifacts = bridge.resolve_base(CONTENT_SHA)
    destination = extension_root / "objects" / "readset"

    copied = bridge.snapshot_read_set(artifacts, destination)
    assert copied, "snapshot must return the copied entries"
    for entry in copied:
        assert entry.root == "EXTENSION"
        assert (extension_root / entry.relative_path).is_file()
    snapshot_hashes = {
        entry.relative_path: entry.sha256 for entry in copied
    }
    assert len(snapshot_hashes) == len(artifacts.read_set)


def test_snapshot_refuses_to_write_outside_the_extension_root(
    parsed_fixture: ParsedFixture, tmp_path: Path
):
    extension_root = tmp_path / "ext"
    bridge = _bridge(parsed_fixture.parsed_root, extension_root)
    artifacts = bridge.resolve_base(CONTENT_SHA)
    with pytest.raises(ExtensionError) as excinfo:
        bridge.snapshot_read_set(artifacts, tmp_path / "outside")
    assert excinfo.value.code == "E_PATH_ESCAPE"


def test_snapshot_detects_a_base_that_moved_mid_flight(
    parsed_fixture: ParsedFixture, tmp_path: Path
):
    extension_root = tmp_path / "ext"
    bridge = _bridge(parsed_fixture.parsed_root, extension_root)
    artifacts = bridge.resolve_base(CONTENT_SHA)
    parsed_fixture.text_path.write_text("# replaced after read\n", encoding="utf-8")
    with pytest.raises(ExtensionError) as excinfo:
        bridge.snapshot_read_set(artifacts, extension_root / "objects" / "readset")
    assert excinfo.value.code == "E_EVIDENCE_MISMATCH"
    assert "changed before it could be snapshotted" in excinfo.value.message


def test_snapshot_rejects_a_mismatched_expected_digest(
    parsed_fixture: ParsedFixture, tmp_path: Path
):
    bridge = _bridge(parsed_fixture.parsed_root, tmp_path / "ext")
    artifacts = bridge.resolve_base(CONTENT_SHA)
    with pytest.raises(ExtensionError) as excinfo:
        bridge.snapshot_read_set(
            artifacts, tmp_path / "ext" / "objects" / "readset", expect_sha256="0" * 64
        )
    assert excinfo.value.code == "E_EVIDENCE_MISMATCH"


# ----------------------------------------------------------------------- paths
@pytest.mark.parametrize(
    "relative",
    ["../escape", "/absolute/path", "a//b", "./a", "a/./b", "C:/windows", "a\\b"],
)
def test_relative_path_guard_rejects_unsafe_forms(relative: str):
    with pytest.raises(ExtensionError) as excinfo:
        validate_relative_path(relative)
    assert excinfo.value.code == "E_PATH_ESCAPE"


def test_safe_join_accepts_a_normal_relative_path(tmp_path: Path):
    root = tmp_path / "ext"
    root.mkdir()
    assert safe_join(root, "objects/by_hash/aa/bb/file.json") == (
        root / "objects/by_hash/aa/bb/file.json"
    )


def test_safe_join_rejects_symlink_escape(tmp_path: Path):
    root = tmp_path / "ext"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ExtensionError) as excinfo:
        safe_join(root, "link/file.json")
    assert excinfo.value.code == "E_PATH_ESCAPE"
    assert "symlink" in excinfo.value.message


def test_safe_join_rejects_a_symlink_that_points_inside_the_root(tmp_path: Path):
    root = tmp_path / "ext"
    (root / "real").mkdir(parents=True)
    (root / "alias").symlink_to(root / "real", target_is_directory=True)
    with pytest.raises(ExtensionError) as excinfo:
        safe_join(root, "alias/file.json")
    assert excinfo.value.code == "E_PATH_ESCAPE"


def test_assert_writable_refuses_targets_outside_the_root(tmp_path: Path):
    roots = Roots(extension=tmp_path / "ext")
    with pytest.raises(ExtensionError) as excinfo:
        assert_writable(tmp_path / "elsewhere" / "file.json", roots)
    assert excinfo.value.code == "E_PATH_ESCAPE"


@pytest.mark.parametrize("forbidden", FORBIDDEN_WRITE_ROOTS)
def test_extension_root_may_not_overlap_forbidden_roots(tmp_path: Path, forbidden: str):
    base = tmp_path / "repo"
    (base / forbidden).mkdir(parents=True)
    with pytest.raises(ExtensionError) as excinfo:
        resolve_extension_root(f"{forbidden}/nested", base_dir=base)
    assert excinfo.value.code == "E_CONFIG_ROOT_FORBIDDEN"


def test_extension_root_may_not_be_a_parent_of_a_forbidden_root(tmp_path: Path):
    base = tmp_path / "repo"
    (base / "parsed").mkdir(parents=True)
    with pytest.raises(ExtensionError) as excinfo:
        resolve_extension_root(".", base_dir=base)
    assert excinfo.value.code == "E_CONFIG_ROOT_FORBIDDEN"


def test_extension_root_may_not_be_the_repository_root(tmp_path: Path):
    base = tmp_path / "repo"
    base.mkdir()
    with pytest.raises(ExtensionError) as excinfo:
        resolve_extension_root(".", base_dir=base)
    assert excinfo.value.code == "E_CONFIG_ROOT_FORBIDDEN"


def test_extension_root_may_not_traverse_a_symlink(tmp_path: Path):
    base = tmp_path / "repo"
    target = tmp_path / "elsewhere"
    base.mkdir()
    target.mkdir()
    (base / "extension_data").symlink_to(target, target_is_directory=True)
    with pytest.raises(ExtensionError) as excinfo:
        resolve_extension_root("extension_data", base_dir=base)
    assert excinfo.value.code == "E_CONFIG_INVALID"


def test_extension_root_accepts_a_plain_directory(tmp_path: Path):
    base = tmp_path / "repo"
    base.mkdir()
    resolved = resolve_extension_root("./extension_data", base_dir=base)
    assert resolved == (base / "extension_data").resolve()


# ------------------------------------------- repo-anchored guard (FAIL-2026-10-18-01)
# Independent DB review found that anchoring forbidden roots only on ``base_dir`` let an
# out-of-repo --extension-config plus an absolute extension_root point back into
# <repo>/sql. These tests pin the fix: the repository root is an anchor too.
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("forbidden", ["sql", "raw_vault", "parsed", "curated"])
def test_repo_forbidden_root_is_refused_even_with_an_outside_base_dir(
    tmp_path: Path, forbidden: str
):
    with pytest.raises(ExtensionError) as excinfo:
        resolve_extension_root(
            str(REPO_ROOT / forbidden), base_dir=tmp_path, project_root=REPO_ROOT
        )
    assert excinfo.value.code == "E_CONFIG_ROOT_FORBIDDEN"


def test_repo_root_is_refused_even_with_an_outside_base_dir(tmp_path: Path):
    with pytest.raises(ExtensionError) as excinfo:
        resolve_extension_root(str(REPO_ROOT), base_dir=tmp_path, project_root=REPO_ROOT)
    assert excinfo.value.code == "E_CONFIG_ROOT_FORBIDDEN"


def test_repo_anchoring_still_allows_a_legitimate_sibling(tmp_path: Path):
    resolved = resolve_extension_root(
        str(tmp_path / "extension_data"), base_dir=tmp_path, project_root=REPO_ROOT
    )
    assert resolved == (tmp_path / "extension_data").resolve()


def test_runner_passes_the_repo_anchor_so_the_outside_config_cannot_escape(tmp_path: Path):
    """End-to-end: an out-of-repo config cannot select <repo>/sql as extension_root."""
    from app.extensions.io.config import load_extension_config

    config_dir = tmp_path / "outside" / "config"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "ext.yaml"
    config_path.write_text(
        "schema_version: doc2md-extensions-config/1.0\n"
        "enabled: true\n"
        f"extension_root: {REPO_ROOT / 'sql'}\n"
        "identity_mode: local_os_subject\n"
        "allow_external_network: false\n"
        "policy:\n"
        "  numeric_chart_estimates_enabled: false\n"
        "  allow_bank_approved_label_with_local_identity: false\n"
        "release:\n"
        "  real_bank_cutover_enabled: false\n",
        encoding="utf-8",
    )
    with pytest.raises(ExtensionError) as excinfo:
        load_extension_config(config_path, project_root=REPO_ROOT)
    assert excinfo.value.code == "E_CONFIG_ROOT_FORBIDDEN"
