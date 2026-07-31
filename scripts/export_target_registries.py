"""Export every top-level Atlas Leaf blend's saved target list to JSON.

Run with Blender:
  blender --factory-startup --background --python export_target_registries.py -- \
    --root D:/path/to/atlas --report D:/path/to/report.json
"""

import argparse
import json
import sys
from pathlib import Path

import addon_utils
import bpy


BACKUP_MARKER = ".codex_backup_before_regenerate.blend"


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def saved_target_paths(props):
    targets = []
    seen = set()
    for item in props.speedtree_spm_items:
        if not item.path:
            continue
        path = Path(bpy.path.abspath(item.path)).expanduser().absolute()
        if path.suffix.lower() != ".spm":
            continue
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        targets.append(path)
    return targets


def main():
    args = parse_args()
    root = Path(args.root).expanduser().absolute()
    report_path = Path(args.report).expanduser().absolute()
    report = {
        "root": str(root),
        "dry_run": bool(args.dry_run),
        "items": [],
        "excluded_backups": [],
        "errors": [],
    }

    enabled = addon_utils.enable(
        "atlas_leaf_mesh_builder", default_set=False, persistent=False
    )
    if enabled is None:
        raise RuntimeError("Could not enable atlas_leaf_mesh_builder")
    from atlas_leaf_mesh_builder.target_registry import (
        registry_path_for_blend,
        save_target_registry,
    )

    for blend in sorted(root.glob("*.blend"), key=lambda path: path.name.casefold()):
        if BACKUP_MARKER in blend.name.casefold():
            report["excluded_backups"].append(str(blend))
            continue
        try:
            bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False)
            props = getattr(bpy.context.scene, "atlas_leaf_builder", None)
            if props is None:
                raise RuntimeError("Atlas Leaf properties are unavailable")
            targets = saved_target_paths(props)
            registry_path = registry_path_for_blend(blend)
            if not args.dry_run:
                payload = save_target_registry(blend, targets)
                registry_path = Path(payload["registry_path"])
            report["items"].append({
                "blend": str(blend),
                "registry": str(registry_path),
                "target_count": len(targets),
                "target_spms": [str(path) for path in targets],
                "missing_target_spms": [
                    str(path) for path in targets if not path.is_file()
                ],
            })
        except Exception as exc:
            report["errors"].append({"blend": str(blend), "error": str(exc)})

    report["summary"] = {
        "blend_count": len(report["items"]),
        "registry_count": 0 if args.dry_run else len(report["items"]),
        "empty_registry_count": sum(
            item["target_count"] == 0 for item in report["items"]
        ),
        "target_count": sum(item["target_count"] for item in report["items"]),
        "missing_target_count": sum(
            len(item["missing_target_spms"]) for item in report["items"]
        ),
        "excluded_backup_count": len(report["excluded_backups"]),
        "error_count": len(report["errors"]),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("CODEX_ATLAS_REGISTRY_EXPORT=" + json.dumps(report["summary"]))
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
