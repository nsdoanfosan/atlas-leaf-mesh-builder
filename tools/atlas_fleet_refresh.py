#!/usr/bin/env python3
"""Plan, apply, verify, or roll back one Atlas-to-SpeedTree fleet refresh.

This controller never imports Blender.  ``apply`` and ``verify`` launch the
companion Blender worker once per authoritative target registry.  A persistent
byte-for-byte backup is completed before the first worker can write production.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET


PLAN_KIND = "atlas_leaf_fleet_refresh_plan"
PLAN_VERSION = 1
BACKUP_KIND = "atlas_leaf_fleet_refresh_backup"
BACKUP_VERSION = 1
REGISTRY_SUFFIX = ".atlas_leaf_targets.json"
GLOBAL_ARTIFACTS = {
    "README_SPEEDTREE_IMPORT.md",
    "speedtree_import_manifest.json",
}
MANAGED_DIRECTORIES = {
    ".atlas_leaf_speedtree_scopes",
    ".atlas_leaf_speedtree_targets",
    "meshes",
}
STAGING_COPY_DIRECTORIES = MANAGED_DIRECTORIES | {"texture", "textures"}
STAGING_COPY_SUFFIXES = {
    ".bmp",
    ".dds",
    ".exr",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".png",
    ".tga",
    ".tif",
    ".tiff",
}
EXCLUDED_PARTS = {
    "backup",
    "backups",
    "cache",
    "caches",
    "report",
    "reports",
    "staging",
    "temp",
    "tests",
    "tmp",
    "work",
}


class FleetRefreshError(RuntimeError):
    pass


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _absolute(path):
    return Path(path).expanduser().resolve(strict=False)


def _path_key(path):
    return os.path.normcase(str(_absolute(path))).casefold()


def _is_below(path, root):
    try:
        _absolute(path).relative_to(_absolute(root))
        return True
    except ValueError:
        return False


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path):
    path = _absolute(path)
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256(path),
    }


def _read_json(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FleetRefreshError(f"JSON is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FleetRefreshError(f"JSON root must be an object: {path}")
    return payload


def _write_json_atomic(path, payload):
    path = _absolute(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(encoded)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _rebase_path_text(value, source_root, staging_root):
    if not isinstance(value, str) or not value.strip():
        return value
    normalized = value.replace("\\", "/")
    source = str(_absolute(source_root)).replace("\\", "/").rstrip("/")
    if normalized.casefold() == source.casefold():
        return str(_absolute(staging_root))
    prefix = source + "/"
    if not normalized.casefold().startswith(prefix.casefold()):
        return value
    suffix = normalized[len(prefix) :]
    return str(_absolute(staging_root) / Path(suffix))


def _rebase_json_paths(value, source_root, staging_root):
    if isinstance(value, dict):
        return {
            key: _rebase_json_paths(item, source_root, staging_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _rebase_json_paths(item, source_root, staging_root)
            for item in value
        ]
    return _rebase_path_text(value, source_root, staging_root)


def _copy_into_staging(source, source_root, staging_root, copied):
    source = _absolute(source)
    source_root = _absolute(source_root)
    staging_root = _absolute(staging_root)
    if not _is_below(source, source_root):
        raise FleetRefreshError(f"Staging source escapes source root: {source}")
    relative = source.relative_to(source_root)
    destination = staging_root / relative
    key = _path_key(source)
    if key in copied:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    copied[key] = {
        "source": _identity(source),
        "staged_path": str(destination),
        "staged_sha256": _sha256(destination),
    }
    return destination


def _spm_external_paths(spm_path):
    """Stream a large SPM and yield Mesh Filename values without retaining XML."""
    values = []
    try:
        with gzip.open(spm_path, "rb") as stream:
            for _event, node in ET.iterparse(stream, events=("end",)):
                if node.tag.casefold() == "filename":
                    value = str(node.text or "").strip()
                    if value:
                        values.append(value)
                node.clear()
    except (OSError, EOFError, ET.ParseError) as exc:
        raise FleetRefreshError(f"SPM is unreadable during staging: {spm_path}: {exc}") from exc
    return values


def create_staging_clone(source_root, staging_root, registry_paths, include_paths=None):
    """Clone exact Atlas inputs and rebase JSON receipts, never source bytes."""
    source_root = _absolute(source_root)
    staging_root = _absolute(staging_root)
    if staging_root.exists() and any(staging_root.iterdir()):
        raise FleetRefreshError(f"Staging directory is not empty: {staging_root}")
    registries = discover_registries(source_root, registry_paths)
    copied = {}
    seed_files = []
    target_paths = []
    for row in registries:
        seed_files.extend(
            [row["registry"]["path"], row["blend"]["path"]]
        )
        target_paths.extend(target["path"] for target in row["targets"])
    for value in include_paths or []:
        path = _absolute(value)
        if not path.is_file() or not _is_below(path, source_root):
            raise FleetRefreshError(f"Explicit staging include is invalid: {path}")
        seed_files.append(path)
        if path.suffix.casefold() == ".spm":
            target_paths.append(str(path))
    seed_files.extend(target_paths)

    external_mesh_sources = []
    # Preflight every SPM before creating the destination. Absolute production
    # filenames would escape staging, so fail with no partial clone.
    for value in sorted({_path_key(path): _absolute(path) for path in target_paths}.values(), key=_path_key):
        for filename in _spm_external_paths(value):
            candidate = Path(filename)
            if candidate.is_absolute():
                if _is_below(candidate, source_root):
                    raise FleetRefreshError(
                        f"SPM has an absolute production Mesh Filename that requires explicit migration: {value}: {filename}"
                    )
                continue
            source = _absolute(value.parent / candidate)
            if source.is_file() and _is_below(source, source_root):
                external_mesh_sources.append(source)

    staging_root.mkdir(parents=True, exist_ok=True)

    owner_roots = {_absolute(path).parent for path in target_paths}
    for owner in owner_roots:
        for candidate in owner.iterdir():
            if candidate.is_file() and (
                candidate.name in GLOBAL_ARTIFACTS
                or candidate.name.startswith("speedtree_import_manifest")
                or candidate.suffix.casefold() in STAGING_COPY_SUFFIXES
            ):
                seed_files.append(candidate)
        for directory in STAGING_COPY_DIRECTORIES:
            seed_files.extend(_files_below(owner / directory))

    for source in seed_files:
        _copy_into_staging(source, source_root, staging_root, copied)

    # Copy exact external Mesh files referenced by each selected SPM.
    for source in external_mesh_sources:
        _copy_into_staging(source, source_root, staging_root, copied)

    # Rebase every copied JSON receipt. Referenced files discovered from JSON
    # are copied iteratively before that receipt is rewritten.
    pending = [
        _absolute(row["source"]["path"])
        for row in copied.values()
        if Path(row["source"]["path"]).suffix.casefold() == ".json"
    ]
    visited = set()
    while pending:
        source_json = pending.pop()
        key = _path_key(source_json)
        if key in visited:
            continue
        visited.add(key)
        try:
            payload = _read_json(source_json)
        except FleetRefreshError:
            continue

        def referenced_strings(value):
            if isinstance(value, dict):
                for item in value.values():
                    yield from referenced_strings(item)
            elif isinstance(value, list):
                for item in value:
                    yield from referenced_strings(item)
            elif isinstance(value, str):
                yield value

        for text in referenced_strings(payload):
            candidate = Path(text)
            if not candidate.is_absolute() or not candidate.is_file() or not _is_below(candidate, source_root):
                continue
            destination = _copy_into_staging(candidate, source_root, staging_root, copied)
            if candidate.suffix.casefold() == ".json" and _path_key(candidate) not in visited:
                pending.append(candidate)
        rebased = _rebase_json_paths(payload, source_root, staging_root)
        destination = staging_root / source_json.relative_to(source_root)
        if source_json.name.endswith(REGISTRY_SUFFIX):
            rebased["staging_contract"] = {
                "source_root": str(source_root),
                "staging_root": str(staging_root),
                "source_registry": str(source_json),
                "source_registry_sha256": _sha256(source_json),
            }
        _write_json_atomic(destination, rebased)

    records = []
    for row in sorted(copied.values(), key=lambda item: _path_key(item["source"]["path"])):
        staged = _absolute(row["staged_path"])
        record = dict(row)
        record["final_staged_sha256"] = _sha256(staged)
        record["rebased"] = record["final_staged_sha256"] != record["staged_sha256"]
        records.append(record)
    receipt = {
        "kind": "atlas_leaf_fleet_staging_clone",
        "version": 1,
        "created_at_utc": _utc_now(),
        "source_root": str(source_root),
        "staging_root": str(staging_root),
        "registries": [
            str(staging_root / _absolute(row["registry"]["path"]).relative_to(source_root))
            for row in registries
        ],
        "files": records,
    }
    _write_json_atomic(staging_root / "staging_clone_receipt.json", receipt)
    return receipt


def _excluded(path, root):
    relative = _absolute(path).relative_to(_absolute(root))
    return any(part.casefold() in EXCLUDED_PARTS for part in relative.parts[:-1])


def _registry_candidates(production_root, explicit_paths=None):
    production_root = _absolute(production_root)
    if explicit_paths:
        candidates = [_absolute(path) for path in explicit_paths]
    else:
        candidates = sorted(
            production_root.rglob(f"*{REGISTRY_SUFFIX}"),
            key=lambda value: _path_key(value),
        )
    unique = []
    seen = set()
    for path in candidates:
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        if not _is_below(path, production_root):
            raise FleetRefreshError(f"Registry is outside production root: {path}")
        if _excluded(path, production_root):
            continue
        if not path.is_file():
            raise FleetRefreshError(f"Registry does not exist: {path}")
        unique.append(path)
    return unique


def discover_registries(production_root, explicit_paths=None, *, collect_blockers=False):
    production_root = _absolute(production_root)
    registries = []
    blockers = []
    blend_owners = {}
    for registry_path in _registry_candidates(production_root, explicit_paths):
        try:
            payload = _read_json(registry_path)
            if payload.get("kind") != "atlas_leaf_spm_targets" or payload.get("version") != 1:
                raise FleetRefreshError(f"Unsupported Atlas target registry: {registry_path}")
            blend_text = str(payload.get("atlas_blend") or "").strip()
            target_values = payload.get("target_spms")
            if not blend_text or not isinstance(target_values, list) or not target_values:
                raise FleetRefreshError(f"Registry has no blend or target list: {registry_path}")
            blend = _absolute(blend_text)
            expected_registry = blend.with_suffix(REGISTRY_SUFFIX)
            if _path_key(expected_registry) != _path_key(registry_path):
                raise FleetRefreshError(
                    f"Registry path does not match its exact Atlas blend: {registry_path} -> {blend}"
                )
            if not _is_below(blend, production_root) or blend.suffix.casefold() != ".blend":
                raise FleetRefreshError(f"Atlas blend is outside the production contract: {blend}")
            if not blend.is_file():
                raise FleetRefreshError(f"Atlas blend is missing: {blend}")
            blend_key = _path_key(blend)
            previous = blend_owners.get(blend_key)
            if previous is not None and _path_key(previous) != _path_key(registry_path):
                raise FleetRefreshError(f"Atlas blend has duplicate registry ownership: {blend}")
            blend_owners[blend_key] = registry_path

            targets = []
            target_keys = set()
            for value in target_values:
                target = _absolute(str(value))
                key = _path_key(target)
                if key in target_keys:
                    raise FleetRefreshError(f"Registry repeats target SPM: {registry_path}: {target}")
                target_keys.add(key)
                if not _is_below(target, production_root) or target.suffix.casefold() != ".spm":
                    raise FleetRefreshError(f"Target SPM is outside the production contract: {target}")
                if not target.is_file():
                    raise FleetRefreshError(f"Target SPM is missing: {target}")
                targets.append(target)
            registries.append(
                {
                    "registry": _identity(registry_path),
                    "blend": _identity(blend),
                    "targets": [_identity(target) for target in targets],
                }
            )
        except FleetRefreshError as exc:
            if not collect_blockers:
                raise
            blockers.append(
                {
                    "registry": str(registry_path),
                    "error": str(exc),
                }
            )
    if not registries and not blockers:
        raise FleetRefreshError(f"No Atlas target registries found under {production_root}")
    if collect_blockers:
        return registries, blockers
    return registries


def _files_below(path):
    path = Path(path)
    if not path.is_dir():
        return []
    return [candidate for candidate in path.rglob("*") if candidate.is_file()]


def _managed_artifacts(root, target_names):
    root = _absolute(root)
    paths = set()
    for name in target_names:
        candidate = root / name
        if candidate.is_file():
            paths.add(candidate)
    for name in GLOBAL_ARTIFACTS:
        candidate = root / name
        if candidate.is_file():
            paths.add(candidate)
    for directory in MANAGED_DIRECTORIES:
        paths.update(_files_below(root / directory))
    return sorted(paths, key=_path_key)


def build_plan(production_root, explicit_paths=None):
    production_root = _absolute(production_root)
    if not production_root.is_dir():
        raise FleetRefreshError(f"Production root does not exist: {production_root}")
    registries, blockers = discover_registries(
        production_root,
        explicit_paths,
        collect_blockers=True,
    )
    roots = {}
    for registry in registries:
        for target in registry["targets"]:
            path = _absolute(target["path"])
            row = roots.setdefault(
                _path_key(path.parent),
                {"production_root": path.parent, "target_names": set()},
            )
            row["target_names"].add(path.name)
    artifact_roots = []
    for row in sorted(roots.values(), key=lambda value: _path_key(value["production_root"])):
        names = sorted(row["target_names"], key=str.casefold)
        artifact_roots.append(
            {
                "production_root": str(row["production_root"]),
                "target_names": names,
                "artifacts": [
                    _identity(path)
                    for path in _managed_artifacts(row["production_root"], names)
                ],
            }
        )
    payload = {
        "kind": PLAN_KIND,
        "version": PLAN_VERSION,
        "created_at_utc": _utc_now(),
        "production_root": str(production_root),
        "registries": registries,
        "blockers": blockers,
        "artifact_roots": artifact_roots,
    }
    digest_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["plan_sha256"] = hashlib.sha256(digest_payload).hexdigest()
    return payload


def _validate_plan(payload):
    if payload.get("kind") != PLAN_KIND or payload.get("version") != PLAN_VERSION:
        raise FleetRefreshError("Unsupported Atlas fleet refresh plan")
    recorded = str(payload.get("plan_sha256") or "")
    unsigned = dict(payload)
    unsigned.pop("plan_sha256", None)
    encoded = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    current = hashlib.sha256(encoded).hexdigest()
    if not recorded or recorded.casefold() != current.casefold():
        raise FleetRefreshError("Atlas fleet refresh plan digest mismatch")
    return payload


def _assert_identity(record):
    path = _absolute(record["path"])
    if not path.is_file():
        raise FleetRefreshError(f"Planned file is missing: {path}")
    current = _identity(path)
    for field in ("size", "mtime_ns", "sha256"):
        if current[field] != record[field]:
            raise FleetRefreshError(f"Planned file drifted ({field}): {path}")


def assert_plan_unchanged(plan):
    if plan.get("blockers"):
        raise FleetRefreshError(
            f"Atlas fleet refresh plan has {len(plan['blockers'])} blocker(s)"
        )
    for registry in plan["registries"]:
        _assert_identity(registry["registry"])
        _assert_identity(registry["blend"])
        for target in registry["targets"]:
            _assert_identity(target)
    for root in plan["artifact_roots"]:
        planned = {_path_key(row["path"]): row for row in root["artifacts"]}
        current = {
            _path_key(path): path
            for path in _managed_artifacts(root["production_root"], root["target_names"])
        }
        if set(planned) != set(current):
            raise FleetRefreshError(
                f"Planned mutable inventory drifted: {root['production_root']}"
            )
        for row in planned.values():
            _assert_identity(row)


def _backup_relative(path):
    path = _absolute(path)
    drive = path.drive.rstrip(":\\/") or "root"
    parts = [part for part in path.parts if part not in {path.anchor, "\\", "/"}]
    return Path("files") / drive / Path(*parts)


def create_backup(plan, backup_root):
    backup_root = _absolute(backup_root)
    if backup_root.exists() and any(backup_root.iterdir()):
        raise FleetRefreshError(f"Backup directory is not empty: {backup_root}")
    backup_root.mkdir(parents=True, exist_ok=True)
    records = []
    for root in plan["artifact_roots"]:
        for artifact in root["artifacts"]:
            source = _absolute(artifact["path"])
            relative = _backup_relative(source)
            destination = backup_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied = _identity(destination)
            if copied["sha256"] != artifact["sha256"]:
                raise FleetRefreshError(f"Backup hash mismatch: {source}")
            records.append(
                {
                    "original": str(source),
                    "backup": str(relative),
                    "sha256": artifact["sha256"],
                    "size": artifact["size"],
                }
            )
    manifest = {
        "kind": BACKUP_KIND,
        "version": BACKUP_VERSION,
        "created_at_utc": _utc_now(),
        "plan_sha256": plan["plan_sha256"],
        "production_root": plan["production_root"],
        "artifact_roots": plan["artifact_roots"],
        "files": records,
    }
    _write_json_atomic(backup_root / "plan.json", plan)
    _write_json_atomic(backup_root / "backup_manifest.json", manifest)
    return manifest


def _restore_bytes(source, destination):
    destination = _absolute(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".atl-",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def rollback_backup(backup_root):
    backup_root = _absolute(backup_root)
    manifest = _read_json(backup_root / "backup_manifest.json")
    if manifest.get("kind") != BACKUP_KIND or manifest.get("version") != BACKUP_VERSION:
        raise FleetRefreshError("Unsupported Atlas fleet backup manifest")
    production_root = _absolute(manifest["production_root"])
    recorded = {_path_key(row["original"]): row for row in manifest["files"]}
    for root in manifest["artifact_roots"]:
        root_path = _absolute(root["production_root"])
        if not _is_below(root_path, production_root):
            raise FleetRefreshError(f"Rollback root escapes production root: {root_path}")
        for current in _managed_artifacts(root_path, root["target_names"]):
            if _path_key(current) not in recorded:
                current.unlink()
    for row in manifest["files"]:
        original = _absolute(row["original"])
        if not _is_below(original, production_root):
            raise FleetRefreshError(f"Rollback file escapes production root: {original}")
        backup = backup_root / row["backup"]
        if not backup.is_file() or _sha256(backup) != row["sha256"]:
            raise FleetRefreshError(f"Rollback backup is missing or corrupt: {backup}")
        _restore_bytes(backup, original)
        if _sha256(original) != row["sha256"]:
            raise FleetRefreshError(f"Rollback verification failed: {original}")
    return {"status": "rolled_back", "restored": len(manifest["files"])}


def verify_staging_sources(receipt_path):
    receipt = _read_json(receipt_path)
    if (
        receipt.get("kind") != "atlas_leaf_fleet_staging_clone"
        or receipt.get("version") != 1
    ):
        raise FleetRefreshError("Unsupported Atlas staging clone receipt")
    for row in receipt.get("files") or []:
        source = row.get("source") or {}
        _assert_identity(source)
    return {
        "status": "source_unchanged",
        "source_root": receipt.get("source_root"),
        "checked": len(receipt.get("files") or []),
    }


def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _run_worker(blender, addon_root, registry, mode, result_path):
    worker = Path(__file__).with_name("blender_atlas_fleet_refresh_worker.py")
    command = [
        str(_absolute(blender)),
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python",
        str(worker),
        "--",
        "--mode",
        mode,
        "--addon-root",
        str(_absolute(addon_root)),
        "--registry",
        registry["registry"]["path"],
        "--result",
        str(result_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        raise FleetRefreshError(
            f"Blender {mode} worker failed for {registry['blend']['path']}\n"
            f"stdout:\n{completed.stdout[-8000:]}\n"
            f"stderr:\n{completed.stderr[-8000:]}"
        )
    if not result_path.is_file():
        raise FleetRefreshError(f"Blender worker wrote no result: {result_path}")
    result = _read_json(result_path)
    if result.get("status") != "ok":
        raise FleetRefreshError(f"Blender worker reported failure: {result}")
    return result


def run_fleet(
    plan,
    blender,
    addon_root,
    mode,
    result_dir,
    on_result=None,
    fail_after_registry=None,
):
    blender = _absolute(blender)
    addon_root = _absolute(addon_root)
    if not blender.is_file():
        raise FleetRefreshError(f"Blender executable is missing: {blender}")
    if not (addon_root / "addons" / "atlas_leaf_mesh_builder" / "__init__.py").is_file():
        raise FleetRefreshError(f"Atlas add-on root is invalid: {addon_root}")
    result_dir = _absolute(result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    if fail_after_registry is not None and not (
        1 <= fail_after_registry <= len(plan["registries"])
    ):
        raise FleetRefreshError(
            "Forced-failure registry index is outside the cohort"
        )
    results = []
    for index, registry in enumerate(plan["registries"], 1):
        result_path = result_dir / f"{index:04d}_{mode}.json"
        result = _run_worker(blender, addon_root, registry, mode, result_path)
        results.append(result)
        if on_result is not None:
            on_result(result, index, len(plan["registries"]))
        if fail_after_registry == index:
            raise FleetRefreshError(
                f"Forced cohort failure after registry {index}"
            )
    return results


def current_artifact_inventory(plan):
    """Return post-run SHA evidence using the exact planned mutable roots."""
    roots = []
    for row in plan["artifact_roots"]:
        paths = _managed_artifacts(row["production_root"], row["target_names"])
        roots.append(
            {
                "production_root": row["production_root"],
                "target_names": list(row["target_names"]),
                "artifacts": [_identity(path) for path in paths],
            }
        )
    return roots


def summarize_reference_attention(results):
    """Deduplicate target audits and classify unbound managed ownership."""
    targets = {}
    for result in results:
        source_scope = str(result.get("source_scope") or "")
        for audit in result.get("reference_audits") or []:
            target = str(_absolute(audit.get("spm") or ""))
            key = _path_key(target)
            row = targets.setdefault(
                key,
                {
                    "spm": target,
                    "audit": audit,
                    "authoritative_scopes": set(),
                },
            )
            if row["audit"] != audit:
                raise FleetRefreshError(
                    f"Conflicting reference audits were returned for shared target: {target}"
                )
            if source_scope:
                row["authoritative_scopes"].add(source_scope)

    target_rows = []
    totals = {
        "checked": 0,
        "active": 0,
        "managed_orphan": 0,
        "missing": 0,
        "orphan_missing": 0,
        "authoritative_output_unbound": 0,
        "unsupported_legacy_groupless": 0,
        "non_authoritative_scoped_orphan": 0,
    }
    for row in sorted(targets.values(), key=lambda item: _path_key(item["spm"])):
        audit = row["audit"]
        scopes = row["authoritative_scopes"]
        classification = {
            "authoritative_output_unbound": 0,
            "unsupported_legacy_groupless": 0,
            "non_authoritative_scoped_orphan": 0,
        }
        for mesh in audit.get("meshes") or []:
            if mesh.get("usage") != "managed_orphan":
                continue
            if mesh.get("groupless"):
                classification["unsupported_legacy_groupless"] += 1
            elif str(mesh.get("scope") or "") in scopes:
                classification["authoritative_output_unbound"] += 1
            else:
                classification["non_authoritative_scoped_orphan"] += 1
        target_row = {
            "spm": row["spm"],
            "authoritative_scopes": sorted(scopes),
            "checked": int(audit.get("checked") or 0),
            "active": int(audit.get("active") or 0),
            "managed_orphan": int(audit.get("managed_orphan") or 0),
            "missing": int(audit.get("missing") or 0),
            "orphan_missing": int(audit.get("orphan_missing") or 0),
            **classification,
        }
        target_rows.append(target_row)
        for name in totals:
            totals[name] += target_row[name]
    return {
        "requires_attention": totals["managed_orphan"] > 0,
        "target_count": len(target_rows),
        **totals,
        "targets": target_rows,
    }


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--production-root", required=True)
    plan_parser.add_argument("--output", required=True)
    plan_parser.add_argument("--registry", action="append", default=[])

    clone_parser = subparsers.add_parser("stage-clone")
    clone_parser.add_argument("--source-root", required=True)
    clone_parser.add_argument("--staging-root", required=True)
    clone_parser.add_argument("--registry", action="append", required=True)
    clone_parser.add_argument("--include", action="append", default=[])

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--backup-root", required=True)
    apply_parser.add_argument("--blender", required=True)
    apply_parser.add_argument("--addon-root", required=True)
    apply_parser.add_argument("--fail-after-registry", type=int)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--plan", required=True)
    verify_parser.add_argument("--blender", required=True)
    verify_parser.add_argument("--addon-root", required=True)
    verify_parser.add_argument("--result-dir", required=True)

    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--plan", required=True)
    audit_parser.add_argument("--blender", required=True)
    audit_parser.add_argument("--addon-root", required=True)
    audit_parser.add_argument("--result-dir", required=True)

    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--backup-root", required=True)

    source_parser = subparsers.add_parser("source-verify")
    source_parser.add_argument("--staging-receipt", required=True)

    check_parser = subparsers.add_parser("plan-check")
    check_parser.add_argument("--plan", required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "stage-clone":
            receipt = create_staging_clone(
                args.source_root,
                args.staging_root,
                args.registry,
                args.include,
            )
            result = {
                "status": "staged",
                "staging_root": receipt["staging_root"],
                "registries": len(receipt["registries"]),
                "files": len(receipt["files"]),
                "rebased": sum(1 for row in receipt["files"] if row["rebased"]),
            }
        elif args.command == "plan":
            plan = build_plan(args.production_root, args.registry or None)
            _write_json_atomic(args.output, plan)
            result = {
                "status": "blocked" if plan["blockers"] else "planned",
                "plan": str(_absolute(args.output)),
                "plan_sha256": plan["plan_sha256"],
                "registries": len(plan["registries"]),
                "blockers": len(plan["blockers"]),
                "targets": sum(len(row["targets"]) for row in plan["registries"]),
                "artifact_roots": len(plan["artifact_roots"]),
                "artifacts": sum(len(row["artifacts"]) for row in plan["artifact_roots"]),
            }
        elif args.command == "apply":
            plan = _validate_plan(_read_json(args.plan))
            assert_plan_unchanged(plan)
            backup = create_backup(plan, args.backup_root)
            ledger_path = _absolute(args.backup_root) / "apply_ledger.json"
            ledger = {
                "kind": "atlas_leaf_fleet_refresh_apply_ledger",
                "version": 1,
                "started_at_utc": _utc_now(),
                "status": "running",
                "plan_sha256": plan["plan_sha256"],
                "results": [],
            }
            _write_json_atomic(ledger_path, ledger)
            try:
                def record_apply(item, _index, _total):
                    ledger["results"].append(item)
                    _write_json_atomic(ledger_path, _json_safe(ledger))

                ledger["results"] = run_fleet(
                    plan,
                    args.blender,
                    args.addon_root,
                    "apply",
                    _absolute(args.backup_root) / "apply_results",
                    on_result=record_apply,
                    fail_after_registry=args.fail_after_registry,
                )
                ledger["verification"] = []

                def record_verify(item, _index, _total):
                    ledger["verification"].append(item)
                    _write_json_atomic(ledger_path, _json_safe(ledger))

                ledger["verification"] = run_fleet(
                    plan,
                    args.blender,
                    args.addon_root,
                    "verify",
                    _absolute(args.backup_root) / "verify_results",
                    on_result=record_verify,
                )
                ledger["reference_audit"] = summarize_reference_attention(
                    ledger["verification"]
                )
                ledger["after_artifacts"] = current_artifact_inventory(plan)
                ledger["status"] = (
                    "applied_and_verified_with_reference_attention"
                    if ledger["reference_audit"]["requires_attention"]
                    else "applied_and_verified"
                )
                ledger["completed_at_utc"] = _utc_now()
                _write_json_atomic(ledger_path, _json_safe(ledger))
            except Exception:
                ledger["status"] = "failed_rolling_back"
                ledger["failed_at_utc"] = _utc_now()
                _write_json_atomic(ledger_path, _json_safe(ledger))
                rollback = rollback_backup(args.backup_root)
                ledger["status"] = "failed_rolled_back"
                ledger["rollback"] = rollback
                _write_json_atomic(ledger_path, _json_safe(ledger))
                raise
            result = {
                "status": ledger["status"],
                "backup_manifest": str(_absolute(args.backup_root) / "backup_manifest.json"),
                "ledger": str(ledger_path),
                "registries": len(ledger["results"]),
                "verified": len(ledger["verification"]),
                "backed_up": len(backup["files"]),
            }
        elif args.command in {"verify", "audit"}:
            plan = _validate_plan(_read_json(args.plan))
            if plan.get("blockers"):
                raise FleetRefreshError(
                    f"Atlas fleet refresh plan has {len(plan['blockers'])} blocker(s)"
                )
            results = run_fleet(plan, args.blender, args.addon_root, args.command, args.result_dir)
            reference_audit = summarize_reference_attention(results)
            base_status = "verified" if args.command == "verify" else "audited"
            result = {
                "status": (
                    f"{base_status}_with_reference_attention"
                    if reference_audit["requires_attention"]
                    else base_status
                ),
                "registries": len(results),
                "result_dir": str(_absolute(args.result_dir)),
                "reference_audit": reference_audit,
            }
        elif args.command == "rollback":
            result = rollback_backup(args.backup_root)
        elif args.command == "source-verify":
            result = verify_staging_sources(args.staging_receipt)
        else:
            plan = _validate_plan(_read_json(args.plan))
            assert_plan_unchanged(plan)
            result = {
                "status": "plan_unchanged",
                "plan_sha256": plan["plan_sha256"],
                "artifacts": sum(
                    len(row["artifacts"])
                    for row in plan["artifact_roots"]
                ),
            }
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(_json_safe(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
