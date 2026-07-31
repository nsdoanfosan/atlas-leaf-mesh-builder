"""Filesystem transaction for one multi-target SpeedTree update action.

The Blender exporter writes several files while it builds one target.  This
module keeps those writes in a private mirror until every target has built and
validated, then commits the complete fleet with a verified rollback inventory.
"""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


GLOBAL_ARTIFACTS = {
    "speedtree_import_manifest.json",
    "README_SPEEDTREE_IMPORT.md",
}
MANAGED_DIRECTORIES = {
    "meshes",
    ".atlas_leaf_speedtree_targets",
    ".atlas_leaf_speedtree_scopes",
}
READ_THROUGH_SUFFIXES = {
    ".bmp",
    ".dds",
    ".exr",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".tga",
}
STAGED_HISTORY_PATH_FIELDS = {
    "asset",
    "assembly_plan_fbx",
    "fbx",
    "spm",
    "target_manifest",
    "xml",
}
PENDING_TRANSACTION_ROOTS = []


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def _normalized_path(path):
    return Path(path).expanduser().absolute()


def _path_key(path):
    return os.path.normcase(str(_normalized_path(path))).casefold()


def _files_below(path):
    path = Path(path)
    if not path.is_dir():
        return []
    return [candidate for candidate in path.rglob("*") if candidate.is_file()]


def _managed_relpaths(root, target_names):
    root = Path(root)
    paths = set()
    for name in target_names:
        candidate = root / name
        if candidate.is_file():
            paths.add(Path(name))
    for name in GLOBAL_ARTIFACTS:
        candidate = root / name
        if candidate.is_file():
            paths.add(Path(name))
    for directory in MANAGED_DIRECTORIES:
        base = root / directory
        for candidate in _files_below(base):
            paths.add(candidate.relative_to(root))
    return paths


def _snapshot(root, relpaths):
    root = Path(root)
    result = {}
    for relative in sorted(relpaths, key=lambda value: str(value).casefold()):
        path = root / relative
        payload = path.read_bytes()
        result[relative] = {
            "bytes": payload,
            "sha256": _sha256_bytes(payload),
        }
    return result


def _copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _atomic_replace_bytes(destination, payload):
    """Replace one file without extending an already-long asset filename."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".atl-",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def cleanup_pending_transaction_roots():
    """Release Blender image handles and remove only this process' temp roots."""
    pending = list(PENDING_TRANSACTION_ROOTS)
    if not pending:
        return []
    try:
        import bpy
    except ImportError:
        bpy = None
    removed = []
    for root in pending:
        root = Path(root)
        if bpy is not None:
            for image in list(bpy.data.images):
                try:
                    image_path = Path(bpy.path.abspath(image.filepath)).absolute()
                    image_path.relative_to(root.absolute())
                except (AttributeError, OSError, RuntimeError, ValueError):
                    continue
                image.user_clear()
                bpy.data.images.remove(image)
        if root.exists():
            shutil.rmtree(root)
        PENDING_TRANSACTION_ROOTS.remove(root)
        removed.append(str(root))
    return removed


def _copy_stage_inputs(root, stage_root, target_names, managed_relpaths):
    copied = set()
    # Every sibling SPM participates in the shared external-file graph even if
    # it was not selected for mutation.
    for source in Path(root).glob("*.spm"):
        if source.is_file():
            relative = source.relative_to(root)
            _copy_file(source, Path(stage_root) / relative)
            copied.add(relative)
    for relative in managed_relpaths:
        if relative in copied:
            continue
        source = Path(root) / relative
        if source.is_file():
            _copy_file(source, Path(stage_root) / relative)
            copied.add(relative)
    # Canonical texture discovery is target-folder based.  These are read-only
    # inputs, not transaction outputs, but the staged target must see them.
    for source in Path(root).iterdir():
        if not source.is_file() or source.suffix.casefold() not in READ_THROUGH_SUFFIXES:
            continue
        relative = source.relative_to(root)
        _copy_file(source, Path(stage_root) / relative)
    for name in target_names:
        (Path(stage_root) / name).parent.mkdir(parents=True, exist_ok=True)


def _rewrite_value(value, stage_root, production_root):
    if isinstance(value, list):
        return [
            _rewrite_value(item, stage_root, production_root)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _rewrite_value(item, stage_root, production_root)
            for key, item in value.items()
        }
    if not isinstance(value, str):
        return value
    stage_text = str(Path(stage_root).absolute())
    normalized_value = value.replace("\\", "/")
    normalized_stage = stage_text.replace("\\", "/")
    if normalized_value.casefold() == normalized_stage.casefold():
        return str(Path(production_root).absolute())
    prefix = normalized_stage.rstrip("/") + "/"
    if normalized_value.casefold().startswith(prefix.casefold()):
        suffix = normalized_value[len(prefix):]
        return str(Path(production_root).absolute() / Path(suffix))
    return value


def _rewrite_staged_manifests(state):
    stage_root = state["stage_root"]
    production_root = state["production_root"]
    candidates = []
    global_manifest = stage_root / "speedtree_import_manifest.json"
    if global_manifest.is_file():
        candidates.append(global_manifest)
    for directory in (
        ".atlas_leaf_speedtree_targets",
        ".atlas_leaf_speedtree_scopes",
    ):
        candidates.extend(
            path for path in _files_below(stage_root / directory)
            if path.suffix.casefold() == ".json"
        )
    for path in candidates:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rewritten = _rewrite_value(payload, stage_root, production_root)
        if rewritten != payload:
            path.write_text(json.dumps(rewritten, indent=2), encoding="utf-8")


def _rewrite_history_paths_for_stage(value, production_root, stage_root):
    if isinstance(value, list):
        return [
            _rewrite_history_paths_for_stage(item, production_root, stage_root)
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    rewritten = {}
    for key, item in value.items():
        if key in STAGED_HISTORY_PATH_FIELDS and isinstance(item, str):
            rewritten[key] = _rewrite_value(
                item,
                production_root,
                stage_root,
            )
        else:
            rewritten[key] = _rewrite_history_paths_for_stage(
                item,
                production_root,
                stage_root,
            )
    return rewritten


def _prepare_staged_manifest_history(state):
    stage_root = state["stage_root"]
    candidates = []
    global_manifest = stage_root / "speedtree_import_manifest.json"
    if global_manifest.is_file():
        candidates.append(global_manifest)
    for directory in (
        ".atlas_leaf_speedtree_targets",
        ".atlas_leaf_speedtree_scopes",
    ):
        candidates.extend(
            path for path in _files_below(stage_root / directory)
            if path.suffix.casefold() == ".json"
        )
    for path in candidates:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rewritten = _rewrite_history_paths_for_stage(
            payload,
            state["production_root"],
            stage_root,
        )
        if rewritten != payload:
            path.write_text(json.dumps(rewritten, indent=2), encoding="utf-8")


def _map_result_paths(value, states):
    if isinstance(value, list):
        return [_map_result_paths(item, states) for item in value]
    if isinstance(value, tuple):
        return tuple(_map_result_paths(item, states) for item in value)
    if isinstance(value, dict):
        return {
            key: _map_result_paths(item, states)
            for key, item in value.items()
        }
    if isinstance(value, Path):
        text = str(value)
        mapped = _map_result_paths(text, states)
        return Path(mapped)
    if not isinstance(value, str):
        return value
    for state in states:
        mapped = _rewrite_value(
            value,
            state["stage_root"],
            state["production_root"],
        )
        if mapped != value:
            return mapped
    return value


def _verify_backup(state):
    backup_root = state["backup_root"]
    for relative, entry in state["snapshot"].items():
        backup = backup_root / relative
        _copy_file(state["production_root"] / relative, backup)
        if _sha256_bytes(backup.read_bytes()) != entry["sha256"]:
            raise RuntimeError(
                f"Atlas transaction backup hash mismatch: {state['production_root'] / relative}"
            )


def _verify_production_unchanged(state):
    current_relpaths = _managed_relpaths(
        state["production_root"],
        state["target_names"],
    )
    if current_relpaths != set(state["snapshot"]):
        raise RuntimeError(
            "Atlas transaction production inventory changed while staging: "
            f"{state['production_root']}"
        )
    for relative, entry in state["snapshot"].items():
        path = state["production_root"] / relative
        if not path.is_file() or _sha256_bytes(path.read_bytes()) != entry["sha256"]:
            raise RuntimeError(
                f"Atlas transaction production file changed while staging: {path}"
            )


def _restore_state(state, final_relpaths):
    production_root = state["production_root"]
    snapshot = state["snapshot"]
    all_relpaths = set(final_relpaths) | set(snapshot)
    for relative in sorted(all_relpaths, key=lambda value: str(value).casefold()):
        destination = production_root / relative
        entry = snapshot.get(relative)
        if entry is None:
            if destination.exists():
                destination.unlink()
            continue
        _atomic_replace_bytes(destination, entry["bytes"])


def _verify_rollback(state):
    current = _managed_relpaths(
        state["production_root"],
        state["target_names"],
    )
    if current != set(state["snapshot"]):
        raise RuntimeError(
            f"Atlas transaction rollback inventory mismatch: {state['production_root']}"
        )
    for relative, entry in state["snapshot"].items():
        path = state["production_root"] / relative
        if _sha256_bytes(path.read_bytes()) != entry["sha256"]:
            raise RuntimeError(
                f"Atlas transaction rollback hash mismatch: {path}"
            )


def _commit_sort_key(relative):
    parts = relative.parts
    if parts and parts[0].casefold() == "meshes":
        return 0, str(relative).casefold()
    if relative.suffix.casefold() == ".spm":
        return 1, str(relative).casefold()
    return 2, str(relative).casefold()


def _commit_states(states, referenced_files):
    final_by_state = {}
    for state in states:
        _verify_backup(state)
        _verify_production_unchanged(state)
        final_by_state[id(state)] = _managed_relpaths(
            state["stage_root"],
            state["target_names"],
        )

    try:
        for state in states:
            snapshot = state["snapshot"]
            final_relpaths = final_by_state[id(state)]
            replacements = []
            deletions = []
            for relative in set(snapshot) | set(final_relpaths):
                staged = state["stage_root"] / relative
                old = snapshot.get(relative)
                if not staged.is_file():
                    deletions.append(relative)
                    continue
                payload = staged.read_bytes()
                if old is None or _sha256_bytes(payload) != old["sha256"]:
                    replacements.append((relative, payload))
            for relative, payload in sorted(replacements, key=lambda item: _commit_sort_key(item[0])):
                destination = state["production_root"] / relative
                _atomic_replace_bytes(destination, payload)
            root_references = referenced_files.get(
                _path_key(state["production_root"]),
                set(),
            )
            for relative in sorted(deletions, key=_commit_sort_key, reverse=True):
                destination = state["production_root"] / relative
                if relative.parts and relative.parts[0].casefold() == "meshes":
                    if _path_key(destination) in root_references:
                        raise RuntimeError(
                            "Atlas transaction refused to delete a shared mesh file "
                            f"still referenced by a committed SPM: {destination}"
                        )
                if destination.exists():
                    destination.unlink()
    except Exception as commit_error:
        rollback_errors = []
        for state in states:
            try:
                _restore_state(state, final_by_state[id(state)])
                _verify_rollback(state)
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
        if rollback_errors:
            raise RuntimeError(
                "Atlas fleet commit failed and byte-for-byte rollback also failed: "
                + "; ".join(rollback_errors)
            ) from commit_error
        raise


def execute_atomic_target_update(
    targets,
    build_target,
    validate_staged,
    *,
    allow_create=False,
):
    """Build, validate and commit all targets under one rollback boundary.

    ``build_target`` receives ``(staged_target, production_target)``.  The
    validator receives all staged targets plus root-state dictionaries and
    returns a production-path-keyed external-file reference graph.
    """
    production_targets = [_normalized_path(path) for path in targets]
    seen = set()
    for target in production_targets:
        key = _path_key(target)
        if key in seen:
            raise RuntimeError(f"Duplicate Atlas target in transaction: {target}")
        seen.add(key)
        if target.suffix.casefold() != ".spm":
            raise RuntimeError(f"Atlas transaction target is not an SPM: {target}")
        if not allow_create and not target.is_file():
            raise RuntimeError(f"Atlas transaction target does not exist: {target}")

    roots = {}
    for target in production_targets:
        root = target.parent
        roots.setdefault(_path_key(root), {"production_root": root, "targets": []})
        roots[_path_key(root)]["targets"].append(target)

    transaction_root = None
    mapped_results = None
    with tempfile.TemporaryDirectory(
        prefix="atlas-leaf-fleet-",
        ignore_cleanup_errors=True,
    ) as temporary:
        transaction_root = Path(temporary)
        states = []
        target_to_stage = {}
        # This is the complete immutable plan/inventory.  No production file
        # is opened for writing before every root and target reaches this point.
        for index, root_plan in enumerate(roots.values()):
            production_root = root_plan["production_root"]
            target_names = [target.name for target in root_plan["targets"]]
            managed = _managed_relpaths(production_root, target_names)
            stage_root = transaction_root / f"root-{index:03d}" / "stage"
            backup_root = transaction_root / f"root-{index:03d}" / "backup"
            stage_root.mkdir(parents=True, exist_ok=True)
            state = {
                "production_root": production_root,
                "stage_root": stage_root,
                "backup_root": backup_root,
                "target_names": target_names,
                "snapshot": _snapshot(production_root, managed),
            }
            _copy_stage_inputs(
                production_root,
                stage_root,
                target_names,
                managed,
            )
            _prepare_staged_manifest_history(state)
            states.append(state)
            for target in root_plan["targets"]:
                target_to_stage[_path_key(target)] = stage_root / target.name

        staged_results = []
        staged_targets = []
        for production_target in production_targets:
            staged_target = target_to_stage[_path_key(production_target)]
            staged_results.append(build_target(staged_target, production_target))
            staged_targets.append(staged_target)

        for state in states:
            _rewrite_staged_manifests(state)
        referenced_files = validate_staged(staged_targets, states)
        _commit_states(states, referenced_files or {})
        mapped_results = _map_result_paths(staged_results, states)
    if transaction_root is not None and transaction_root.exists():
        PENDING_TRANSACTION_ROOTS.append(transaction_root)
    return mapped_results
