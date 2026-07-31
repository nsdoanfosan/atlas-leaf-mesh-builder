"""Authoritative source-image index for the currently open Blender file.

The index is content-addressed.  File timestamps are observed only to detect a
file changing while it is hashed; they are never accepted as source identity.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path


SOURCE_INDEX_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BlendSourceIndexError(RuntimeError):
    """Raised when Blender cannot prove an exact, saved blend source index."""


def _path_key(value) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value))).casefold()


def file_sha256(path) -> str:
    """Hash exact file bytes and reject an in-flight content change."""
    source = Path(path)
    try:
        before = source.stat()
    except OSError as exc:
        raise BlendSourceIndexError(f"blend file is unavailable: {source}: {exc}") from exc
    hasher = hashlib.sha256()
    try:
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        after = source.stat()
    except OSError as exc:
        raise BlendSourceIndexError(f"blend file could not be hashed: {source}: {exc}") from exc
    # These fields are mutation detectors only.  The returned SHA-256 is the
    # identity consumed by callers; neither mtime nor size can authorize reuse.
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or getattr(before, "st_ino", None) != getattr(after, "st_ino", None)
    ):
        raise BlendSourceIndexError(f"blend changed while it was being hashed: {source}")
    return hasher.hexdigest()


def _resolved_image_path(bpy_module, image) -> str:
    raw = str(getattr(image, "filepath", "") or "")
    if not raw:
        return ""
    try:
        library = getattr(image, "library", None)
        if library is None:
            resolved = bpy_module.path.abspath(raw)
        else:
            resolved = bpy_module.path.abspath(raw, library=library)
    except TypeError:
        resolved = bpy_module.path.abspath(raw)
    except Exception as exc:
        raise BlendSourceIndexError(
            f"could not resolve Blender image path {raw!r}: {exc}"
        ) from exc
    return os.path.abspath(str(resolved)) if resolved else ""


def current_blend_source_index(
    *, expected_blend_path=None, expected_sha256="", bpy_module=None
) -> dict:
    """Return a SHA-bound image index for Blender's current saved main file."""
    if bpy_module is None:
        import bpy as bpy_module  # type: ignore

    raw_filepath = str(getattr(bpy_module.data, "filepath", "") or "")
    if not raw_filepath:
        raise BlendSourceIndexError("Blender has no current main-file path")
    blend_path = Path(raw_filepath)
    if not blend_path.is_file():
        raise BlendSourceIndexError(f"current Blender main file is missing: {blend_path}")
    if expected_blend_path is not None and _path_key(blend_path) != _path_key(
        expected_blend_path
    ):
        raise BlendSourceIndexError(
            "Blender current-file identity mismatch: "
            f"expected {expected_blend_path}, opened {blend_path}"
        )
    if bool(getattr(bpy_module.data, "is_dirty", False)):
        raise BlendSourceIndexError(
            f"Blender main file has unsaved changes: {blend_path}"
        )

    digest = file_sha256(blend_path)
    expected_sha256 = str(expected_sha256 or "").strip().casefold()
    if expected_sha256:
        if not _SHA256_RE.fullmatch(expected_sha256):
            raise BlendSourceIndexError("expected blend SHA-256 is malformed")
        if digest != expected_sha256:
            raise BlendSourceIndexError(
                f"blend content changed before Blender indexing: {blend_path}"
            )

    images = []
    seen = set()
    for image in getattr(bpy_module.data, "images", ()):
        name = str(getattr(image, "name", "") or "")
        filepath_raw = str(getattr(image, "filepath", "") or "")
        filepath = _resolved_image_path(bpy_module, image) if filepath_raw else ""
        if not name and not filepath_raw:
            continue
        key = (name.casefold(), filepath_raw.casefold(), filepath.casefold())
        if key in seen:
            continue
        seen.add(key)
        images.append(
            {
                "name": name,
                "filepath_raw": filepath_raw,
                "filepath": filepath,
                "packed": getattr(image, "packed_file", None) is not None,
            }
        )
    images.sort(
        key=lambda row: (
            row["name"].casefold(),
            row["filepath"].casefold(),
            row["filepath_raw"].casefold(),
        )
    )
    return {
        "schema_version": SOURCE_INDEX_SCHEMA_VERSION,
        "status": "ok",
        "indexed_by_blender": True,
        "blend": str(blend_path.resolve()),
        "blend_sha256": digest,
        "image_count": len(images),
        "images": images,
    }
