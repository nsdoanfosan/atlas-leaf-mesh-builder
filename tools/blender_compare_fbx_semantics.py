"""Compare backed-up and current FBX geometry through Blender import.

Run this script through Blender's ``--python`` option.  It is diagnostic and
does not save a blend or modify either FBX input.
"""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


def _arguments():
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--backup-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--pairs")
    return parser.parse_args(values)


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rounded(values):
    return [round(float(value), 9) for value in values]


def _mesh_payload(mesh):
    uv_layers = []
    for layer in mesh.uv_layers:
        uv_layers.append(
            {
                "name": layer.name,
                "values": [_rounded(loop.uv) for loop in layer.data],
            }
        )
    color_attributes = []
    for attribute in mesh.color_attributes:
        color_attributes.append(
            {
                "name": attribute.name,
                "domain": attribute.domain,
                "data_type": attribute.data_type,
                "values": [
                    _rounded(getattr(item, "color", ()))
                    for item in attribute.data
                ],
            }
        )
    return {
        "vertices": [_rounded(vertex.co) for vertex in mesh.vertices],
        "edges": [list(edge.vertices) for edge in mesh.edges],
        "polygons": [
            {
                "vertices": list(polygon.vertices),
                "material_index": polygon.material_index,
                "use_smooth": bool(polygon.use_smooth),
            }
            for polygon in mesh.polygons
        ],
        "uv_layers": uv_layers,
        "color_attributes": color_attributes,
        "materials": [material.name if material else None for material in mesh.materials],
    }


def _digest(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scene_signature(path):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    bpy.ops.wm.fbx_import(filepath=str(path))
    components = {
        "names": [],
        "transforms": [],
        "geometry": [],
        "uv_layers": [],
        "color_attributes": [],
        "materials": [],
    }
    totals = {"objects": 0, "vertices": 0, "edges": 0, "polygons": 0}
    for obj in sorted(bpy.context.scene.objects, key=lambda item: item.name.casefold()):
        if obj.type != "MESH":
            continue
        payload = _mesh_payload(obj.data)
        totals["objects"] += 1
        totals["vertices"] += len(obj.data.vertices)
        totals["edges"] += len(obj.data.edges)
        totals["polygons"] += len(obj.data.polygons)
        components["names"].append(obj.name)
        components["transforms"].append([_rounded(row) for row in obj.matrix_world])
        components["geometry"].append(
            {
                "vertices": payload["vertices"],
                "edges": payload["edges"],
                "polygons": payload["polygons"],
            }
        )
        components["uv_layers"].append(payload["uv_layers"])
        components["color_attributes"].append(payload["color_attributes"])
        components["materials"].append(payload["materials"])
    return {
        "sha256": {name: _digest(value) for name, value in components.items()},
        "totals": totals,
        "labels": {
            "names": components["names"],
            "materials": components["materials"],
        },
    }


def main():
    args = _arguments()
    plan = _read_json(args.plan)
    ledger = _read_json(args.ledger)
    backup_root = Path(args.backup_root).resolve()
    backup_manifest = _read_json(backup_root / "backup_manifest.json")
    before = {
        Path(item["path"]): item
        for root in plan.get("artifact_roots") or []
        for item in root.get("artifacts") or []
    }
    after = {
        Path(item["path"]): item
        for root in ledger.get("after_artifacts") or []
        for item in root.get("artifacts") or []
    }
    backups = {
        Path(row["original"]): backup_root / row["backup"]
        for row in backup_manifest.get("files") or []
    }
    candidates = [
        path
        for path in before.keys() & after.keys()
        if path.suffix.casefold() == ".fbx"
        and before[path]["sha256"] != after[path]["sha256"]
    ]
    cached_pairs = None
    if args.pairs:
        cached_pairs = _read_json(args.pairs).get("pairs") or []
        if len(cached_pairs) != len(candidates):
            raise RuntimeError(
                f"FBX pair mapping count mismatch: {len(cached_pairs)} != {len(candidates)}"
            )
    rows = []
    with tempfile.TemporaryDirectory(prefix="atl-fbx-") as temporary:
        temporary = Path(temporary)
        old_short = temporary / "old.fbx"
        new_short = temporary / "new.fbx"
        for index, current in enumerate(sorted(candidates, key=lambda item: str(item).casefold()), 1):
            previous = backups[current]
            if cached_pairs is None:
                shutil.copy2(previous, old_short)
                shutil.copy2(current, new_short)
            else:
                pair = cached_pairs[index - 1]
                if Path(pair["path"]) != current:
                    raise RuntimeError(f"FBX pair mapping order mismatch: {current}")
                old_short = Path(pair["before_cached"])
                new_short = Path(pair["after_cached"])
            old_signature = _scene_signature(old_short)
            new_signature = _scene_signature(new_short)
            equal = {
                name: digest == new_signature["sha256"][name]
                for name, digest in old_signature["sha256"].items()
            }
            rows.append(
                {
                    "path": str(current),
                    "backup": str(previous),
                    "semantic_equal": all(equal.values()),
                    "component_equal": equal,
                    "before": old_signature,
                    "after": new_signature,
                }
            )
            print(f"ATLAS_FBX_COMPARE {index}/{len(candidates)}", flush=True)
    component_changes = {
        name: sum(not row["component_equal"][name] for row in rows)
        for name in (
            "names",
            "transforms",
            "geometry",
            "uv_layers",
            "color_attributes",
            "materials",
        )
    }
    report = {
        "kind": "atlas_fbx_semantic_comparison",
        "compared": len(rows),
        "semantic_equal": sum(row["semantic_equal"] for row in rows),
        "semantic_changed": sum(not row["semantic_equal"] for row in rows),
        "component_changes": component_changes,
        "files": rows,
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "ATLAS_FBX_COMPARE_RESULT "
        + json.dumps(
            {
                "output": str(output),
                "compared": report["compared"],
                "semantic_equal": report["semantic_equal"],
                "semantic_changed": report["semantic_changed"],
                "component_changes": report["component_changes"],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
