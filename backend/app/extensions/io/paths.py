"""Restricted path resolution for the extension layer.

Contract basis: ``01_SDD.md`` §4.1 and ``contracts/.../IDENTITY_AND_PATHS.md``.

Hard rules enforced here (all fail-closed):

* ``extension_root`` must never be, contain, or live inside a forbidden write root
  (``sql/``, ``raw_vault/``, ``parsed/``, ``curated/``, ``.cursor/rules/``);
* relative paths reject absolute form, Windows drive/backslash form, ``..`` and
  empty segments;
* the resolved target must stay inside the configured root **after** symlink
  resolution, and a path whose intermediate components are symlinks is rejected
  outright instead of being silently followed;
* any write target must still land inside ``extension_root``.

This module deliberately knows nothing about hashes: content verification is the
read bridge's / artifact store's job. Path acceptance is never evidence of
authenticity.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .. import ExtensionError

__all__ = [
    "FORBIDDEN_WRITE_ROOTS",
    "Roots",
    "validate_relative_path",
    "resolve_extension_root",
    "safe_join",
    "assert_writable",
    "file_ref_target",
    "is_within",
    "lexical_symlinks",
]

#: Repository-relative roots the new layer must never write into.
FORBIDDEN_WRITE_ROOTS: tuple[str, ...] = (
    "sql",
    "raw_vault",
    "parsed",
    "curated",
    ".cursor/rules",
)


@dataclass(frozen=True)
class Roots:
    """Absolute roots the bridge may read from and the layer may write to."""

    extension: Path
    raw_vault: Path | None = None
    parsed: Path | None = None
    curated: Path | None = None

    def for_file_ref_root(self, name: str) -> Path:
        mapping = {
            "EXTENSION": self.extension,
            "RAW_VAULT": self.raw_vault,
            "PARSED": self.parsed,
        }
        target = mapping.get(name)
        if target is None:
            raise ExtensionError(
                "E_PATH_ESCAPE", f"file reference root {name!r} is not configured"
            )
        return target


def validate_relative_path(relative_path: str, *, field: str = "relative_path") -> str:
    """Validate a root-relative POSIX path and return it unchanged."""
    if not isinstance(relative_path, str) or not relative_path:
        raise ExtensionError("E_PATH_ESCAPE", f"{field} must be a non-empty string")
    if relative_path.startswith(("/", "\\")):
        raise ExtensionError("E_PATH_ESCAPE", f"{field} must be root-relative: {relative_path!r}")
    if "\\" in relative_path:
        raise ExtensionError(
            "E_PATH_ESCAPE", f"{field} must use '/' separators, not backslashes: {relative_path!r}"
        )
    if ":" in relative_path.split("/")[0]:
        raise ExtensionError("E_PATH_ESCAPE", f"{field} must not carry a drive letter: {relative_path!r}")
    segments = relative_path.split("/")
    for segment in segments:
        if segment in ("", ".", ".."):
            raise ExtensionError(
                "E_PATH_ESCAPE",
                f"{field} has an empty/'.'/'..' segment and is refused: {relative_path!r}",
            )
        if segment.endswith(" "):
            raise ExtensionError(
                "E_PATH_ESCAPE", f"{field} segment has trailing space: {relative_path!r}"
            )
    return relative_path


def is_within(child: Path, parent: Path) -> bool:
    """True when ``child`` equals ``parent`` or is nested under it (lexical, resolved)."""
    try:
        child_r = child.resolve(strict=False)
        parent_r = parent.resolve(strict=False)
    except OSError:  # pragma: no cover - defensive
        return False
    return child_r == parent_r or parent_r in child_r.parents


def lexical_symlinks(path: Path) -> list[str]:
    """Every symlink component on the *lexical* (unresolved) form of ``path``.

    Resolution alone is not enough: ``root/link/inner`` resolves happily to
    ``/elsewhere/inner`` and only the containment check would notice. Rejecting
    symlinked components outright removes that ambiguity and any later TOCTOU
    window between validation and use.
    """
    absolute = Path(os.path.abspath(path))
    found: list[str] = []
    current = Path(absolute.parts[0])
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                found.append(str(current))
        except OSError:  # pragma: no cover - defensive
            found.append(str(current))
    return found


def resolve_extension_root(
    value: str | os.PathLike[str],
    *,
    base_dir: Path,
    forbidden_roots: tuple[str, ...] = FORBIDDEN_WRITE_ROOTS,
    extra_forbidden: tuple[Path, ...] = (),
    project_root: Path | None = None,
) -> Path:
    """Resolve and validate ``extension_root`` from configuration.

    ``value`` is resolved relative to ``base_dir`` (the directory holding the
    configuration, e.g. ``<repo>/config``). Forbidden roots are anchored on **two**
    bases on purpose:

    * ``base_dir`` — catches a config that names a sibling of the forbidden root;
    * ``project_root`` — catches the case that defeated the first version of this
      guard: an ``--extension-config`` living outside the repository plus an
      *absolute* ``extension_root`` pointing at ``<repo>/sql`` or
      ``<repo>/raw_vault``. With only ``base_dir`` anchoring, nothing in that path
      mentions a forbidden name, so the overlap was never detected
      (defect FAIL-2026-10-18-01, found by independent DB review).

    ``project_root`` should be the repository root, derived by the caller from its
    own location rather than from the current working directory, so the answer does
    not depend on where the CLI was started.
    """
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if not isinstance(value, str) or not value.strip():
        raise ExtensionError("E_CONFIG_INVALID", "extension_root must be a non-empty path")

    raw = Path(value)
    candidate = raw if raw.is_absolute() else (Path(base_dir) / raw)

    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:  # pragma: no cover - defensive
        raise ExtensionError("E_CONFIG_INVALID", f"extension_root cannot be resolved: {exc}") from exc

    symlinks = lexical_symlinks(candidate)
    if symlinks:
        raise ExtensionError(
            "E_CONFIG_INVALID",
            f"extension_root must not contain symlinked components: {symlinks}",
        )

    anchors: list[Path] = [Path(base_dir).resolve(strict=False)]
    if project_root is not None:
        project_resolved = Path(project_root).resolve(strict=False)
        if project_resolved not in anchors:
            anchors.append(project_resolved)

    for anchor in anchors:
        for name in forbidden_roots:
            forbidden = (anchor / name).resolve(strict=False)
            if is_within(resolved, forbidden) or is_within(forbidden, resolved):
                raise ExtensionError(
                    "E_CONFIG_ROOT_FORBIDDEN",
                    f"extension_root {resolved} overlaps forbidden write root {name!r} "
                    f"(anchored at {anchor})",
                )

    for extra in extra_forbidden:
        if is_within(resolved, extra) or is_within(extra, resolved):
            raise ExtensionError(
                "E_CONFIG_ROOT_FORBIDDEN",
                f"extension_root {resolved} overlaps protected input root {extra}",
            )

    for anchor in anchors:
        if resolved == anchor:
            raise ExtensionError(
                "E_CONFIG_ROOT_FORBIDDEN",
                f"extension_root must not be the project/configuration root itself ({anchor})",
            )

    return resolved


def safe_join(root: Path, relative_path: str, *, field: str = "relative_path") -> Path:
    """Join a validated relative path onto ``root`` and prove containment."""
    validate_relative_path(relative_path, field=field)
    root_resolved = Path(root).resolve(strict=False)
    candidate = root_resolved / relative_path
    leaks = lexical_symlinks(candidate)
    if leaks:
        raise ExtensionError(
            "E_PATH_ESCAPE",
            f"{field} traverses a symlink and is refused: {relative_path!r} ({leaks})",
        )
    if not is_within(candidate, root_resolved):
        raise ExtensionError(
            "E_PATH_ESCAPE", f"{field} escapes its root after resolution: {relative_path!r}"
        )
    return candidate


def assert_writable(target: Path, roots: Roots) -> None:
    """Refuse any write that does not land inside ``extension_root``."""
    if not is_within(Path(target), roots.extension):
        raise ExtensionError(
            "E_PATH_ESCAPE",
            f"write target {target} is outside extension_root {roots.extension}",
        )
    leaks = lexical_symlinks(Path(target))
    extension_leaks = set(leaks) - set(lexical_symlinks(roots.extension))
    if extension_leaks:
        raise ExtensionError(
            "E_PATH_ESCAPE", f"write target traverses a symlink: {sorted(extension_leaks)}"
        )


def file_ref_target(ref, roots: Roots) -> Path:
    """Map a validated ``FileRef`` contract object onto an absolute path."""
    base = roots.for_file_ref_root(ref.root)
    return safe_join(base, ref.relative_path, field=f"fileRef[{ref.root}].relative_path")
