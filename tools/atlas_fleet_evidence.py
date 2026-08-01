"""Summarize byte and JSON-field drift from an Atlas fleet apply ledger."""

import argparse
import collections
import json
import shutil
from pathlib import Path


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _flatten(value, prefix="$"):
    if isinstance(value, dict):
        rows = {}
        for key in sorted(value):
            rows.update(_flatten(value[key], f"{prefix}.{key}"))
        return rows
    if isinstance(value, list):
        rows = {}
        for index, item in enumerate(value):
            rows.update(_flatten(item, f"{prefix}[{index}]"))
        return rows
    return {prefix: value}


def _backup_path(backup_root, manifest, original):
    for row in manifest.get("files") or []:
        if Path(row["original"]) == original:
            return backup_root / row["backup"]
    return None


def summarize(plan_path, ledger_path, backup_root, fbx_cache=None):
    plan = _read_json(plan_path)
    ledger = _read_json(ledger_path)
    backup_root = Path(backup_root).resolve()
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
    changed = []
    for path in sorted(before.keys() & after.keys(), key=lambda item: str(item).casefold()):
        sha_changed = before[path]["sha256"] != after[path]["sha256"]
        mtime_changed = before[path]["mtime_ns"] != after[path]["mtime_ns"]
        if sha_changed or mtime_changed:
            changed.append(
                {
                    "path": str(path),
                    "extension": path.suffix.casefold(),
                    "sha_changed": sha_changed,
                    "mtime_changed": mtime_changed,
                }
            )

    extensions = collections.defaultdict(lambda: {"files": 0, "sha_changed": 0, "mtime_changed": 0})
    json_diffs = []
    fbx_pairs = []
    if fbx_cache is not None:
        fbx_cache = Path(fbx_cache).resolve()
        if fbx_cache.exists() and any(fbx_cache.iterdir()):
            raise RuntimeError(f"FBX cache directory is not empty: {fbx_cache}")
        fbx_cache.mkdir(parents=True, exist_ok=True)
    for row in changed:
        bucket = extensions[row["extension"]]
        bucket["files"] += 1
        bucket["sha_changed"] += int(row["sha_changed"])
        bucket["mtime_changed"] += int(row["mtime_changed"])
        path = Path(row["path"])
        if path.suffix.casefold() == ".fbx" and row["sha_changed"] and fbx_cache is not None:
            backup = _backup_path(backup_root, backup_manifest, path)
            if backup is None:
                raise RuntimeError(f"Backup entry missing for FBX: {path}")
            index = len(fbx_pairs) + 1
            previous_cached = fbx_cache / f"{index:04d}_before.fbx"
            current_cached = fbx_cache / f"{index:04d}_after.fbx"
            shutil.copy2(backup, previous_cached)
            shutil.copy2(path, current_cached)
            fbx_pairs.append(
                {
                    "path": str(path),
                    "backup": str(backup),
                    "before_cached": str(previous_cached),
                    "after_cached": str(current_cached),
                }
            )
        if path.suffix.casefold() != ".json" or not row["sha_changed"]:
            continue
        backup = _backup_path(backup_root, backup_manifest, path)
        if backup is None:
            json_diffs.append({"path": str(path), "error": "backup entry missing"})
            continue
        old = _flatten(_read_json(backup))
        new = _flatten(_read_json(path))
        field_rows = []
        for field in sorted(old.keys() | new.keys()):
            if old.get(field) != new.get(field):
                field_rows.append(
                    {
                        "field": field,
                        "before": old.get(field),
                        "after": new.get(field),
                    }
                )
        json_diffs.append({"path": str(path), "fields": field_rows})

    report = {
        "ledger_status": ledger.get("status"),
        "before_files": len(before),
        "after_files": len(after),
        "added": sorted(str(path) for path in after.keys() - before.keys()),
        "removed": sorted(str(path) for path in before.keys() - after.keys()),
        "sha_changed": sum(row["sha_changed"] for row in changed),
        "mtime_changed": sum(row["mtime_changed"] for row in changed),
        "changed_files": len(changed),
        "extensions": dict(sorted(extensions.items())),
        "json_diffs": json_diffs,
    }
    if fbx_cache is not None:
        mapping_path = fbx_cache / "fbx_pairs.json"
        mapping_path.write_text(
            json.dumps({"pairs": fbx_pairs}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report["fbx_pairs"] = str(mapping_path)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--backup-root", required=True)
    parser.add_argument("--output")
    parser.add_argument("--fbx-cache")
    args = parser.parse_args(argv)
    report = summarize(args.plan, args.ledger, args.backup_root, args.fbx_cache)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(
            json.dumps(
                {
                    "output": str(Path(args.output).resolve()),
                    "ledger_status": report["ledger_status"],
                    "sha_changed": report["sha_changed"],
                    "mtime_changed": report["mtime_changed"],
                    "changed_files": report["changed_files"],
                    "extensions": report["extensions"],
                    "fbx_pairs": report.get("fbx_pairs"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
