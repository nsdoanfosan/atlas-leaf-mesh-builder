"""Blender-side worker for :mod:`atlas_fleet_refresh`.

Run only through Blender's ``--python`` option.  It intentionally adds no UI.
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import bpy


def _absolute(path):
    return Path(path).expanduser().resolve(strict=False)


def _path_key(path):
    return os.path.normcase(str(_absolute(path))).casefold()


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
    return str(_absolute(staging_root) / Path(normalized[len(prefix) :]))


def _rebase_value(value, source_root, staging_root):
    if isinstance(value, dict):
        return {key: _rebase_value(item, source_root, staging_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebase_value(item, source_root, staging_root) for item in value]
    return _rebase_path_text(value, source_root, staging_root)


def _read_json(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return payload


def _write_json(path, payload):
    path = _absolute(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(str(item) for item in value)
    return value


def _arguments():
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("apply", "verify", "audit"), required=True)
    parser.add_argument("--addon-root", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--result", required=True)
    return parser.parse_args(values)


def _states_for_targets(targets):
    roots = {}
    for target in targets:
        root = target.parent
        row = roots.setdefault(_path_key(root), {"production_root": root, "stage_root": root, "target_names": []})
        row["target_names"].append(target.name)
    return list(roots.values())


def _receipt_groups(manifest):
    rows = []
    for group in manifest.get("material_groups") or []:
        if not isinstance(group, dict):
            continue
        meshes = group.get("meshes") or []
        rows.append(
            {
                "collection": group.get("collection"),
                "material": group.get("material"),
                "mesh_count": len(meshes),
                "source_objects": sorted(
                    str(item.get("source_object") or "")
                    for item in meshes
                    if isinstance(item, dict)
                    and str(item.get("source_object") or "").strip()
                ),
            }
        )
    return rows


def _current_groups(groups):
    rows = []
    for group in groups:
        objects = sorted(obj.name for obj in group["objects"])
        rows.append(
            {
                "collection": group["collection"],
                "material": group["material"],
                "mesh_count": len(objects),
                "source_objects": objects,
            }
        )
    return rows


def _source_operation_is_current(
    blend,
    collection,
    groups,
    targets,
    target_scope_manifests_for_blend,
    source_refresh_receipt_is_current,
    validate_targets,
):
    if collection is None:
        return None
    current_scope = str(collection.get("atlas_leaf_speedtree_scope_id") or "")
    current_groups = _current_groups(groups)
    matches = []
    for target in targets:
        candidates = []
        for manifest in target_scope_manifests_for_blend(target, blend):
            lifecycle = manifest.get("atlas_scope_lifecycle") or {}
            if (
                str(manifest.get("export_scope_id") or "") != current_scope
                or lifecycle.get("status") == "retired"
                or _receipt_groups(manifest) != current_groups
                or not source_refresh_receipt_is_current(
                    manifest.get("source_refresh_receipt"),
                    blend,
                )
            ):
                continue
            mesh_paths = [
                Path(item.get("asset") or item.get("fbx") or "")
                for group in manifest.get("material_groups") or []
                if isinstance(group, dict)
                for item in group.get("meshes") or []
                if isinstance(item, dict)
            ]
            if mesh_paths and all(path.is_file() for path in mesh_paths):
                candidates.append(manifest)
        if len(candidates) != 1:
            return None
        matches.append(candidates[0].get("_scope_manifest_path"))
    validation = validate_targets(targets, _states_for_targets(targets))
    return {
        "status": "already_current",
        "scope": current_scope,
        "groups": current_groups,
        "receipts": matches,
        "validation": validation,
    }


def main():
    args = _arguments()
    result_path = _absolute(args.result)
    try:
        addon_root = _absolute(args.addon_root)
        addons_path = addon_root / "addons"
        if str(addons_path) not in sys.path:
            sys.path.insert(0, str(addons_path))
        import atlas_leaf_mesh_builder as addon

        addon.register()
        registry_path = _absolute(args.registry)
        registry = _read_json(registry_path)
        blend = _absolute(registry.get("atlas_blend") or "")
        expected_targets = [_absolute(value) for value in registry.get("target_spms") or []]
        bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False, use_scripts=False)

        from atlas_leaf_mesh_builder.props import sync_spm_target_registry
        from atlas_leaf_mesh_builder.speedtree import (
            ATLAS_LEAF_COLLECTION_SCOPE_KEY,
            _validate_staged_speedtree_targets,
            export_or_update_speedtree_spm_targets,
            grouped_source_objects,
            spm_managed_reference_audit,
            source_refresh_receipt_is_current,
            target_scope_manifests_for_blend,
            speedtree_spm_targets,
        )

        props = bpy.context.scene.atlas_leaf_builder
        staging = registry.get("staging_contract") or {}
        source_root = staging.get("source_root")
        staging_root = staging.get("staging_root")
        if source_root and staging_root:
            mapping = json.loads(props.speedtree_source_materials_json or "{}")
            props.speedtree_source_materials_json = json.dumps(
                _rebase_value(mapping, source_root, staging_root),
                ensure_ascii=False,
                sort_keys=True,
            )
            for attribute in (
                "albedo_path",
                "alpha_path",
                "output_dir",
                "speedtree_spm_path",
            ):
                if not hasattr(props, attribute):
                    continue
                current = str(getattr(props, attribute) or "")
                rebased = _rebase_path_text(current, source_root, staging_root)
                if rebased != current and Path(rebased).exists():
                    setattr(props, attribute, rebased)
        sync_spm_target_registry(props, initialize_missing=False)
        actual_targets = speedtree_spm_targets(props)
        if [_path_key(path) for path in actual_targets] != [_path_key(path) for path in expected_targets]:
            raise RuntimeError(
                "Loaded Blender target list does not match its authoritative registry: "
                f"expected={expected_targets}, actual={actual_targets}"
            )
        if args.mode == "apply":
            collection = bpy.data.collections.get(props.collection_name)
            groups = grouped_source_objects(
                collection,
                str(props.speedtree_atlas_asset_name or "").strip() or None,
            )
            details = _source_operation_is_current(
                blend,
                collection,
                groups,
                expected_targets,
                target_scope_manifests_for_blend,
                source_refresh_receipt_is_current,
                _validate_staged_speedtree_targets,
            )
            if details is None:
                details = export_or_update_speedtree_spm_targets(props)
        elif args.mode == "verify":
            details = _validate_staged_speedtree_targets(
                expected_targets,
                _states_for_targets(expected_targets),
            )
        else:
            collection = bpy.data.collections.get(props.collection_name)
            if collection is None:
                raise RuntimeError(
                    f"Configured Atlas collection is missing: {props.collection_name}"
                )
            groups = grouped_source_objects(
                collection,
                str(props.speedtree_atlas_asset_name or "").strip() or None,
            )
            group_rows = _current_groups(groups)
            target_rows = []
            current_scope = str(collection.get(ATLAS_LEAF_COLLECTION_SCOPE_KEY) or "")
            for target in expected_targets:
                manifests = target_scope_manifests_for_blend(target, blend)
                receipt_rows = []
                current_matches = []
                for manifest in manifests:
                    receipt_groups = _receipt_groups(manifest)
                    receipt = {
                        "path": manifest.get("_scope_manifest_path"),
                        "scope": str(manifest.get("export_scope_id") or ""),
                        "source_collection": manifest.get("source_collection"),
                        "lifecycle": manifest.get("atlas_scope_lifecycle"),
                        "groups": receipt_groups,
                    }
                    receipt_rows.append(receipt)
                    if receipt["scope"] == current_scope and receipt_groups == group_rows:
                        current_matches.append(receipt["path"])
                target_rows.append(
                    {
                        "target": str(target),
                        "receipts": receipt_rows,
                        "current_collection_receipt_matches": current_matches,
                        "collection_receipt_mismatch": len(current_matches) != 1,
                    }
                )
            details = {
                "collection": collection.name,
                "scope": current_scope,
                "groups": group_rows,
                "targets": target_rows,
                "mismatch_count": sum(
                    1 for row in target_rows if row["collection_receipt_mismatch"]
                ),
            }
        source_collection = bpy.data.collections.get(props.collection_name)
        source_scope = (
            str(source_collection.get(ATLAS_LEAF_COLLECTION_SCOPE_KEY) or "")
            if source_collection is not None
            else ""
        )
        result = {
            "status": "ok",
            "mode": args.mode,
            "blend": str(blend),
            "registry": str(registry_path),
            "targets": [str(path) for path in expected_targets],
            "source_scope": source_scope,
            "reference_audits": [
                spm_managed_reference_audit(path)
                for path in expected_targets
            ],
            "details": _json_safe(details),
        }
    except Exception as exc:
        result = {
            "status": "error",
            "mode": getattr(args, "mode", None),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        _write_json(result_path, result)
        raise
    _write_json(result_path, result)


if __name__ == "__main__":
    main()
