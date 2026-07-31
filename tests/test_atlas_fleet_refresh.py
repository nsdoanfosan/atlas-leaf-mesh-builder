import importlib.util
import gzip
import json
from pathlib import Path
from unittest import mock

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "atlas_fleet_refresh.py"
SPEC = importlib.util.spec_from_file_location("atlas_fleet_refresh", MODULE_PATH)
fleet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fleet)


def write_registry(root, name="M_leaf_oak_atlas_01", target_names=None):
    atlas = root / "atlas"
    owner = root / "oak"
    atlas.mkdir(parents=True, exist_ok=True)
    owner.mkdir(parents=True, exist_ok=True)
    blend = atlas / f"{name}.blend"
    blend.write_bytes(b"blend-v1")
    targets = []
    for target_name in target_names or ["SK_oak_01.spm", "SK_oak_02.spm"]:
        target = owner / target_name
        target.write_bytes(f"spm:{target_name}".encode("utf-8"))
        targets.append(target)
    registry = blend.with_suffix(fleet.REGISTRY_SUFFIX)
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "atlas_leaf_spm_targets",
                "atlas_blend": str(blend),
                "target_spms": [str(path) for path in targets],
            }
        ),
        encoding="utf-8",
    )
    return blend, registry, targets


def make_managed_files(root):
    owner = root / "oak"
    (owner / "speedtree_import_manifest.json").write_text("{}", encoding="utf-8")
    (owner / "README_SPEEDTREE_IMPORT.md").write_text("receipt", encoding="utf-8")
    (owner / "meshes").mkdir()
    (owner / "meshes" / "leaf.fbx").write_bytes(b"fbx-v1")
    (owner / ".atlas_leaf_speedtree_scopes").mkdir()
    (owner / ".atlas_leaf_speedtree_scopes" / "scope.json").write_text("{}", encoding="utf-8")
    (owner / ".atlas_leaf_speedtree_targets").mkdir()
    (owner / ".atlas_leaf_speedtree_targets" / "SK_oak_01.json").write_text("{}", encoding="utf-8")


def test_plan_discovers_exact_registry_and_complete_mutable_inventory(tmp_path):
    _blend, registry, targets = write_registry(tmp_path)
    make_managed_files(tmp_path)

    plan = fleet.build_plan(tmp_path)

    assert plan["kind"] == fleet.PLAN_KIND
    assert plan["blockers"] == []
    assert len(plan["registries"]) == 1
    assert plan["registries"][0]["registry"]["path"] == str(registry.resolve())
    assert [row["path"] for row in plan["registries"][0]["targets"]] == [
        str(path.resolve()) for path in targets
    ]
    artifacts = {
        Path(row["path"]).relative_to((tmp_path / "oak").resolve()).as_posix()
        for row in plan["artifact_roots"][0]["artifacts"]
    }
    assert artifacts == {
        ".atlas_leaf_speedtree_scopes/scope.json",
        ".atlas_leaf_speedtree_targets/SK_oak_01.json",
        "README_SPEEDTREE_IMPORT.md",
        "SK_oak_01.spm",
        "SK_oak_02.spm",
        "meshes/leaf.fbx",
        "speedtree_import_manifest.json",
    }
    fleet._validate_plan(plan)


def test_plan_allows_two_blends_to_share_one_spm_as_distinct_scopes(tmp_path):
    _blend, _registry, targets = write_registry(tmp_path, "M_leaf_oak_atlas_01", ["SK_oak_01.spm"])
    second_blend, second_registry, _ = write_registry(
        tmp_path,
        "M_cluster_oak_atlas_01",
        ["SK_oak_01.spm"],
    )
    second_registry.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "atlas_leaf_spm_targets",
                "atlas_blend": str(second_blend),
                "target_spms": [str(targets[0])],
            }
        ),
        encoding="utf-8",
    )

    plan = fleet.build_plan(tmp_path)

    assert len(plan["registries"]) == 2
    assert len(plan["artifact_roots"]) == 1
    assert plan["artifact_roots"][0]["target_names"] == ["SK_oak_01.spm"]


def test_plan_rejects_mismatched_registry_filename(tmp_path):
    blend, registry, targets = write_registry(tmp_path)
    wrong = registry.with_name("wrong.atlas_leaf_targets.json")
    wrong.write_bytes(registry.read_bytes())
    registry.unlink()

    with pytest.raises(fleet.FleetRefreshError, match="does not match"):
        fleet.discover_registries(tmp_path)


def test_plan_collects_all_invalid_registries_as_apply_blockers(tmp_path):
    blend, registry, _targets = write_registry(tmp_path)
    blend.unlink()
    second = registry.with_name("foreign.atlas_leaf_targets.json")
    second.write_text(registry.read_text(encoding="utf-8"), encoding="utf-8")

    plan = fleet.build_plan(tmp_path)

    assert plan["registries"] == []
    assert len(plan["blockers"]) == 2
    assert any("blend is missing" in row["error"] for row in plan["blockers"])
    assert any("does not match" in row["error"] for row in plan["blockers"])
    with pytest.raises(fleet.FleetRefreshError, match="2 blocker"):
        fleet.assert_plan_unchanged(plan)


def test_assert_plan_unchanged_rejects_input_drift(tmp_path):
    _blend, _registry, targets = write_registry(tmp_path)
    plan = fleet.build_plan(tmp_path)
    targets[0].write_bytes(b"changed-after-plan")

    with pytest.raises(fleet.FleetRefreshError, match="drifted"):
        fleet.assert_plan_unchanged(plan)


def test_backup_and_rollback_restore_bytes_and_delete_new_managed_files(tmp_path):
    _blend, _registry, targets = write_registry(tmp_path)
    make_managed_files(tmp_path)
    plan = fleet.build_plan(tmp_path)
    fleet.assert_plan_unchanged(plan)
    backup_root = tmp_path.parent / f"{tmp_path.name}-backup"
    manifest = fleet.create_backup(plan, backup_root)

    owner = tmp_path / "oak"
    targets[0].write_bytes(b"mutated")
    (owner / "meshes" / "leaf.fbx").write_bytes(b"mutated-fbx")
    new_file = owner / "meshes" / "new-leaf.fbx"
    new_file.write_bytes(b"new")
    (owner / "speedtree_import_manifest.json").unlink()

    result = fleet.rollback_backup(backup_root)

    assert result == {"status": "rolled_back", "restored": len(manifest["files"])}
    assert targets[0].read_bytes() == b"spm:SK_oak_01.spm"
    assert (owner / "meshes" / "leaf.fbx").read_bytes() == b"fbx-v1"
    assert (owner / "speedtree_import_manifest.json").read_text(encoding="utf-8") == "{}"
    assert not new_file.exists()
    for row in manifest["files"]:
        assert fleet._sha256(row["original"]) == row["sha256"]


def test_backup_refuses_nonempty_destination(tmp_path):
    write_registry(tmp_path)
    plan = fleet.build_plan(tmp_path)
    backup_root = tmp_path.parent / f"{tmp_path.name}-backup"
    backup_root.mkdir()
    (backup_root / "unrelated.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(fleet.FleetRefreshError, match="not empty"):
        fleet.create_backup(plan, backup_root)


def test_current_inventory_records_after_hashes(tmp_path):
    _blend, _registry, targets = write_registry(tmp_path)
    plan = fleet.build_plan(tmp_path)
    before = {
        row["path"]: row["sha256"]
        for row in plan["artifact_roots"][0]["artifacts"]
    }
    targets[0].write_bytes(b"after")

    after = fleet.current_artifact_inventory(plan)

    after_rows = {row["path"]: row["sha256"] for row in after[0]["artifacts"]}
    assert after_rows[str(targets[0].resolve())] != before[str(targets[0].resolve())]


def test_run_fleet_can_force_failure_after_recorded_registry(tmp_path):
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"exe")
    addon_root = tmp_path / "addon"
    init = addon_root / "addons" / "atlas_leaf_mesh_builder" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text("", encoding="utf-8")
    plan = {
        "registries": [
            {"blend": {"path": "one.blend"}},
            {"blend": {"path": "two.blend"}},
        ]
    }
    recorded = []

    with mock.patch.object(
        fleet,
        "_run_worker",
        side_effect=[{"status": "ok", "registry": "one"}],
    ):
        with pytest.raises(fleet.FleetRefreshError, match="after registry 1"):
            fleet.run_fleet(
                plan,
                blender,
                addon_root,
                "apply",
                tmp_path / "results",
                on_result=lambda item, *_args: recorded.append(item),
                fail_after_registry=1,
            )

    assert recorded == [{"status": "ok", "registry": "one"}]


def test_staging_clone_preserves_spm_bytes_and_rebases_receipts(tmp_path):
    source_root = tmp_path / "source"
    staging_root = tmp_path / "staging"
    blend, registry, targets = write_registry(source_root, target_names=["SK_oak_01.spm"])
    mesh = source_root / "oak" / "meshes" / "leaf.fbx"
    mesh.parent.mkdir()
    mesh.write_bytes(b"fbx")
    xml = (
        '<?xml version="1.0"?><SpeedTree><Assets><Mesh ID="1">'
        '<Lod><Filename>meshes/leaf.fbx</Filename></Lod>'
        '</Mesh></Assets></SpeedTree>'
    ).encode("utf-8")
    targets[0].write_bytes(gzip.compress(xml, mtime=0))
    scope = source_root / "oak" / ".atlas_leaf_speedtree_scopes" / "scope.json"
    scope.parent.mkdir()
    scope.write_text(
        json.dumps({"spm": str(targets[0]), "blend_file": str(blend), "fbx": str(mesh)}),
        encoding="utf-8",
    )
    source_spm_sha = fleet._sha256(targets[0])

    receipt = fleet.create_staging_clone(source_root, staging_root, [registry])

    staged_spm = staging_root / targets[0].relative_to(source_root)
    staged_registry = staging_root / registry.relative_to(source_root)
    staged_scope = staging_root / scope.relative_to(source_root)
    assert fleet._sha256(staged_spm) == source_spm_sha
    assert (staging_root / mesh.relative_to(source_root)).read_bytes() == b"fbx"
    registry_payload = json.loads(staged_registry.read_text(encoding="utf-8"))
    assert registry_payload["atlas_blend"] == str(staging_root / blend.relative_to(source_root))
    assert registry_payload["staging_contract"]["source_root"] == str(source_root.resolve())
    scope_payload = json.loads(staged_scope.read_text(encoding="utf-8"))
    assert scope_payload["spm"] == str(staged_spm)
    assert scope_payload["fbx"] == str(staging_root / mesh.relative_to(source_root))
    assert len(receipt["files"]) >= 5


def test_staging_clone_refuses_absolute_production_mesh_reference(tmp_path):
    source_root = tmp_path / "source"
    staging_root = tmp_path / "staging"
    _blend, registry, targets = write_registry(source_root, target_names=["SK_oak_01.spm"])
    mesh = source_root / "oak" / "meshes" / "leaf.fbx"
    mesh.parent.mkdir()
    mesh.write_bytes(b"fbx")
    xml = (
        '<?xml version="1.0"?><SpeedTree><Assets><Mesh ID="1">'
        f'<Lod><Filename>{mesh}</Filename></Lod>'
        '</Mesh></Assets></SpeedTree>'
    ).encode("utf-8")
    targets[0].write_bytes(gzip.compress(xml, mtime=0))

    with pytest.raises(fleet.FleetRefreshError, match="absolute production Mesh"):
        fleet.create_staging_clone(source_root, staging_root, [registry])


def test_source_verify_detects_original_drift_not_staging_changes(tmp_path):
    source_root = tmp_path / "source"
    staging_root = tmp_path / "staging"
    _blend, registry, targets = write_registry(source_root, target_names=["SK_oak_01.spm"])
    xml = b'<?xml version="1.0"?><SpeedTree><Assets /></SpeedTree>'
    targets[0].write_bytes(gzip.compress(xml, mtime=0))
    fleet.create_staging_clone(source_root, staging_root, [registry])
    receipt = staging_root / "staging_clone_receipt.json"
    staged_target = staging_root / targets[0].relative_to(source_root)
    staged_target.write_bytes(b"staging mutation is allowed")

    result = fleet.verify_staging_sources(receipt)
    assert result["status"] == "source_unchanged"

    targets[0].write_bytes(b"source drift")
    with pytest.raises(fleet.FleetRefreshError, match="drifted"):
        fleet.verify_staging_sources(receipt)


def test_reference_summary_deduplicates_shared_targets_and_classifies_ownership(tmp_path):
    target = tmp_path / "SK_oak_01.spm"
    audit = {
        "spm": str(target),
        "checked": 4,
        "active": 1,
        "managed_orphan": 3,
        "missing": 0,
        "orphan_missing": 0,
        "meshes": [
            {"usage": "active", "scope": "scope-c", "groupless": False},
            {"usage": "managed_orphan", "scope": "scope-a", "groupless": False},
            {"usage": "managed_orphan", "scope": "legacy", "groupless": True},
            {"usage": "managed_orphan", "scope": "foreign", "groupless": False},
        ],
    }
    results = [
        {"source_scope": "scope-a", "reference_audits": [audit]},
        {"source_scope": "scope-c", "reference_audits": [audit]},
    ]

    summary = fleet.summarize_reference_attention(results)

    assert summary["requires_attention"] is True
    assert summary["target_count"] == 1
    assert summary["checked"] == 4
    assert summary["managed_orphan"] == 3
    assert summary["authoritative_output_unbound"] == 1
    assert summary["unsupported_legacy_groupless"] == 1
    assert summary["non_authoritative_scoped_orphan"] == 1
    assert summary["targets"][0]["authoritative_scopes"] == ["scope-a", "scope-c"]
