import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_speedtree_xml import speedtree


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO
    / "addons"
    / "atlas_leaf_mesh_builder"
    / "speedtree_transaction.py"
)
SPEC = importlib.util.spec_from_file_location(
    "atlas_leaf_speedtree_transaction_test",
    MODULE_PATH,
)
transaction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transaction)


def inventory(root):
    return {
        path.relative_to(root): path.read_bytes()
        for path in Path(root).rglob("*")
        if path.is_file()
    }


def write_managed_fixture(root, targets=("tree_01.spm",)):
    root = Path(root)
    for target in targets:
        (root / target).write_bytes(f"original:{target}".encode("utf-8"))
    (root / "meshes").mkdir()
    (root / "meshes" / "shared.fbx").write_bytes(b"shared-v1")
    (root / ".atlas_leaf_speedtree_targets").mkdir()
    (root / ".atlas_leaf_speedtree_scopes").mkdir()
    (root / "speedtree_import_manifest.json").write_text(
        json.dumps({"version": 1}),
        encoding="utf-8",
    )
    (root / ".atlas_leaf_speedtree_targets" / "tree_01.json").write_text(
        json.dumps({"target": "tree_01"}),
        encoding="utf-8",
    )
    (root / ".atlas_leaf_speedtree_scopes" / "scope-a.json").write_text(
        json.dumps({"scope": "a"}),
        encoding="utf-8",
    )


class AtomicTargetTransactionTests(unittest.TestCase):
    def test_stage_inventory_excludes_manual_copies_and_backup_snapshots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "production"
            stage = Path(folder) / "stage"
            root.mkdir()
            stage.mkdir()
            selected = root / "SK_tree_01.spm"
            sibling = root / "SK_tree_02.spm"
            manual_ko = root / "SK_tree_02 - \uBCF5\uC0AC\uBCF8.spm"
            manual_en = root / "SK_tree_02 - Copy (2).spm"
            backup = root / "SK_tree_02.skbatch_backup_20260806_110204.spm"
            for path in (selected, sibling, manual_ko, manual_en, backup):
                path.write_bytes(path.name.encode("utf-8"))

            managed = transaction._managed_relpaths(root, [selected.name])
            transaction._copy_stage_inputs(
                root,
                stage,
                [selected.name],
                managed,
            )

            self.assertTrue((stage / selected.name).is_file())
            self.assertTrue((stage / sibling.name).is_file())
            self.assertFalse((stage / manual_ko.name).exists())
            self.assertFalse((stage / manual_en.name).exists())
            self.assertFalse((stage / backup.name).exists())

    def test_atomic_temp_name_does_not_extend_long_asset_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder) / ("nested_" + "x" * 80)
            parent.mkdir()
            destination = parent / ("m_leaf_" + "y" * 120 + ".fbx")
            calls = []
            real_replace = transaction.os.replace

            def record_replace(source, target):
                calls.append((Path(source), Path(target)))
                return real_replace(source, target)

            with mock.patch.object(
                transaction.os,
                "replace",
                side_effect=record_replace,
            ):
                transaction._atomic_replace_bytes(destination, b"payload")

            self.assertEqual(destination.read_bytes(), b"payload")
            self.assertEqual(calls[0][1], destination)
            self.assertTrue(calls[0][0].name.startswith(".atl-"))
            self.assertLess(len(calls[0][0].name), 32)

    def test_single_target_commits_spm_shared_output_and_all_manifest_classes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_managed_fixture(root)
            target = root / "tree_01.spm"

            def build(staged, production):
                staged.write_bytes(b"spm-v2")
                (staged.parent / "meshes" / "shared.fbx").write_bytes(b"shared-v2")
                (staged.parent / "speedtree_import_manifest.json").write_text(
                    json.dumps({"spm": str(staged)}), encoding="utf-8"
                )
                target_manifest = (
                    staged.parent / ".atlas_leaf_speedtree_targets" / "tree_01.json"
                )
                target_manifest.write_text(
                    json.dumps({"spm": str(staged)}), encoding="utf-8"
                )
                scope_manifest = (
                    staged.parent / ".atlas_leaf_speedtree_scopes" / "scope-a.json"
                )
                scope_manifest.write_text(
                    json.dumps({"spm": str(staged)}), encoding="utf-8"
                )
                return staged

            result = transaction.execute_atomic_target_update(
                [target], build, lambda staged, states: {}
            )

            self.assertEqual(result, [target])
            self.assertEqual(target.read_bytes(), b"spm-v2")
            self.assertEqual((root / "meshes" / "shared.fbx").read_bytes(), b"shared-v2")
            for manifest in (
                root / "speedtree_import_manifest.json",
                root / ".atlas_leaf_speedtree_targets" / "tree_01.json",
                root / ".atlas_leaf_speedtree_scopes" / "scope-a.json",
            ):
                self.assertEqual(json.loads(manifest.read_text())["spm"], str(target))

    def test_forced_failure_at_target_n_leaves_every_file_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            targets = [root / "tree_01.spm", root / "tree_02.spm"]
            write_managed_fixture(root, [path.name for path in targets])
            before = inventory(root)
            calls = []

            def build(staged, production):
                calls.append(production)
                staged.write_bytes(f"changed:{production.name}".encode("utf-8"))
                (staged.parent / "meshes" / "shared.fbx").write_bytes(b"changed")
                (staged.parent / "speedtree_import_manifest.json").write_bytes(b"changed")
                if len(calls) == 2:
                    raise RuntimeError("forced target N failure")
                return staged

            with self.assertRaisesRegex(RuntimeError, "forced target N failure"):
                transaction.execute_atomic_target_update(
                    targets, build, lambda staged, states: {}
                )

            self.assertEqual(inventory(root), before)

    def test_two_scopes_can_commit_through_one_shared_mesh_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            targets = [root / "tree_01.spm", root / "tree_02.spm"]
            write_managed_fixture(root, [path.name for path in targets])

            def build(staged, production):
                staged.write_bytes(f"updated:{production.name}".encode("utf-8"))
                mesh = staged.parent / "meshes" / f"{production.stem}.fbx"
                mesh.write_bytes(production.name.encode("utf-8"))
                scope = staged.parent / ".atlas_leaf_speedtree_scopes" / f"{production.stem}.json"
                scope.write_text(json.dumps({"spm": str(staged)}), encoding="utf-8")
                return staged

            transaction.execute_atomic_target_update(
                targets, build, lambda staged, states: {}
            )

            for target in targets:
                self.assertEqual(target.read_bytes(), f"updated:{target.name}".encode("utf-8"))
                self.assertTrue((root / "meshes" / f"{target.stem}.fbx").is_file())
                scope = root / ".atlas_leaf_speedtree_scopes" / f"{target.stem}.json"
                self.assertEqual(json.loads(scope.read_text())["spm"], str(target))

    def test_idempotent_update_performs_no_production_replace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_managed_fixture(root)
            target = root / "tree_01.spm"

            def build(staged, production):
                return staged

            with mock.patch.object(transaction.os, "replace") as replace:
                transaction.execute_atomic_target_update(
                    [target], build, lambda staged, states: {}
                )

            replace.assert_not_called()

    def test_semantically_identical_spm_rewrite_is_rebased_before_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "tree_01.spm"
            target.write_bytes(b"<SpeedTreeRaw><Value>1</Value></SpeedTreeRaw>")
            observed_states = []

            def build(staged, production):
                staged.write_bytes(
                    b"<SpeedTreeRaw><Value>2</Value></SpeedTreeRaw>"
                )
                production.write_bytes(
                    b"<SpeedTreeRaw>\n  <Value>1</Value>\n</SpeedTreeRaw>\n"
                )
                return staged

            def validate(_staged, states):
                observed_states.extend(states)
                return {}

            transaction.execute_atomic_target_update(
                [target], build, validate
            )

            self.assertEqual(
                target.read_bytes(),
                b"<SpeedTreeRaw><Value>2</Value></SpeedTreeRaw>",
            )
            self.assertEqual(
                observed_states[0]["semantic_rebases"][0]["path"],
                str(target),
            )

    def test_semantic_spm_change_reports_conflict_and_preserves_external_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "tree_01.spm"
            target.write_bytes(b"<SpeedTreeRaw><Value>1</Value></SpeedTreeRaw>")
            external = b"<SpeedTreeRaw><Value>9</Value></SpeedTreeRaw>"

            def build(staged, production):
                staged.write_bytes(
                    b"<SpeedTreeRaw><Value>2</Value></SpeedTreeRaw>"
                )
                production.write_bytes(external)
                return staged

            with self.assertRaises(
                transaction.AtlasTransactionConflictError
            ) as caught:
                transaction.execute_atomic_target_update(
                    [target], build, lambda staged, states: {}
                )

            self.assertEqual(target.read_bytes(), external)
            self.assertEqual(
                caught.exception.failure_contract["reason"],
                "production_changed_while_staging",
            )
            self.assertTrue(
                caught.exception.failure_contract[
                    "preserve_external_changes"
                ]
            )

    def test_shared_file_delete_is_rejected_when_graph_still_references_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_managed_fixture(root)
            target = root / "tree_01.spm"
            before = inventory(root)

            def build(staged, production):
                (staged.parent / "meshes" / "shared.fbx").unlink()
                return staged

            root_key = transaction._path_key(root)
            shared_key = transaction._path_key(root / "meshes" / "shared.fbx")
            with self.assertRaisesRegex(RuntimeError, "still referenced"):
                transaction.execute_atomic_target_update(
                    [target],
                    build,
                    lambda staged, states: {root_key: {shared_key}},
                )

            self.assertEqual(inventory(root), before)

    def test_mid_commit_failure_restores_all_targets_and_shared_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            targets = [root / "tree_01.spm", root / "tree_02.spm"]
            write_managed_fixture(root, [path.name for path in targets])
            before = inventory(root)

            def build(staged, production):
                staged.write_bytes(f"changed:{production.name}".encode("utf-8"))
                (staged.parent / "meshes" / "shared.fbx").write_bytes(b"changed")
                return staged

            real_replace = transaction.os.replace
            calls = []

            def fail_second_replace(source, destination):
                calls.append((source, destination))
                if len(calls) == 2:
                    raise OSError("forced mid-commit failure")
                return real_replace(source, destination)

            with mock.patch.object(
                transaction.os,
                "replace",
                side_effect=fail_second_replace,
            ):
                with self.assertRaisesRegex(OSError, "forced mid-commit failure"):
                    transaction.execute_atomic_target_update(
                        targets,
                        build,
                        lambda staged, states: {},
                    )

            self.assertEqual(inventory(root), before)


class StagedSpeedTreeValidationTests(unittest.TestCase):
    def _spm_with_external_mesh(self, path, filename):
        root = speedtree.ET.Element("SpeedTreeRaw")
        assets = speedtree.ET.SubElement(root, "Assets")
        mesh = speedtree.ET.SubElement(assets, "Mesh", {"ID": "1"})
        speedtree.ET.SubElement(mesh, "Embedded").text = "false"
        speedtree.ET.SubElement(mesh, "Filename").text = filename
        material = speedtree.ET.SubElement(
            assets, "Material_v8", {"ID": "2", "Name": "M_leaf"}
        )
        speedtree.ET.SubElement(material, "CutoutMeshID").text = "1"
        speedtree.write_spm_xml(path, root)

    def _write_empty_generator_contract(self, path):
        manifest = speedtree.manifest_with_binding_contracts(
            {"generator_connection": {"bindings": []}},
            [],
        )
        receipt = speedtree.target_manifest_path(path)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(manifest), encoding="utf-8")

    def test_validation_rejects_absent_external_mesh_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = root / "stage"
            production = root / "production"
            stage.mkdir()
            production.mkdir()
            target = stage / "tree.spm"
            self._spm_with_external_mesh(target, "meshes/missing.fbx")
            state = {
                "stage_root": stage,
                "production_root": production,
            }

            with self.assertRaisesRegex(RuntimeError, "Filename is absent"):
                speedtree._validate_staged_speedtree_targets([target], [state])

    def test_selected_update_allows_unchanged_preexisting_missing_mesh(self):
        """Another provider's sealed missing FBX cannot block this update."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = root / "stage"
            production = root / "production"
            stage.mkdir()
            production.mkdir()
            production_target = production / "tree.spm"
            self._spm_with_external_mesh(
                production_target,
                "meshes/other_provider_missing.fbx",
            )
            target = stage / "tree.spm"
            target.write_bytes(production_target.read_bytes())
            staged_root = speedtree.read_spm_xml(target)
            assets = staged_root.find("Assets")
            embedded = speedtree.ET.SubElement(
                assets, "Mesh", {"ID": "3"}
            )
            speedtree.ET.SubElement(embedded, "Embedded").text = "true"
            material = speedtree.ET.SubElement(
                assets,
                "Material_v8",
                {"ID": "4", "Name": "M_current_provider"},
            )
            speedtree.ET.SubElement(material, "CutoutMeshID").text = "3"
            speedtree.write_spm_xml(target, staged_root)
            self._write_empty_generator_contract(target)
            state = {
                "stage_root": stage,
                "production_root": production,
                "snapshot": {
                    Path("tree.spm"): {
                        "bytes": production_target.read_bytes(),
                    }
                },
            }

            graph = speedtree._validate_staged_speedtree_targets(
                [target], [state]
            )

            self.assertEqual(
                graph[transaction._path_key(production)],
                set(),
            )

    def test_selected_update_rejects_changed_preexisting_missing_mesh(self):
        """A selected transaction cannot rewrite legacy debt to a new miss."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = root / "stage"
            production = root / "production"
            stage.mkdir()
            production.mkdir()
            production_target = production / "tree.spm"
            self._spm_with_external_mesh(
                production_target,
                "meshes/old_missing.fbx",
            )
            target = stage / "tree.spm"
            target.write_bytes(production_target.read_bytes())
            staged_root = speedtree.read_spm_xml(target)
            staged_root.find("./Assets/Mesh/Filename").text = (
                "meshes/new_missing.fbx"
            )
            speedtree.write_spm_xml(target, staged_root)
            state = {
                "stage_root": stage,
                "production_root": production,
                "snapshot": {
                    Path("tree.spm"): {
                        "bytes": production_target.read_bytes(),
                    }
                },
            }

            with self.assertRaisesRegex(RuntimeError, "Filename is absent"):
                speedtree._validate_staged_speedtree_targets(
                    [target], [state]
                )

    def test_selected_update_rejects_new_user_of_preexisting_missing_mesh(self):
        """Unchanged Filename alone cannot hide newly activated bad data."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = root / "stage"
            production = root / "production"
            stage.mkdir()
            production.mkdir()
            production_target = production / "tree.spm"
            self._spm_with_external_mesh(
                production_target,
                "meshes/old_missing.fbx",
            )
            target = stage / "tree.spm"
            target.write_bytes(production_target.read_bytes())
            staged_root = speedtree.read_spm_xml(target)
            extra = speedtree.ET.SubElement(
                staged_root.find("Assets"),
                "Material_v8",
                {"ID": "3", "Name": "M_new_user"},
            )
            speedtree.ET.SubElement(extra, "CutoutMeshID").text = "1"
            speedtree.write_spm_xml(target, staged_root)
            state = {
                "stage_root": stage,
                "production_root": production,
                "snapshot": {
                    Path("tree.spm"): {
                        "bytes": production_target.read_bytes(),
                    }
                },
            }

            with self.assertRaisesRegex(RuntimeError, "Filename is absent"):
                speedtree._validate_staged_speedtree_targets(
                    [target], [state]
                )

    def test_production_only_external_mesh_resolves_from_the_owner_folder(self):
        """A reference the stage never receives still lives in production.

        ``_copy_stage_inputs`` copies the folder's SPMs, managed relpaths and
        read-through files -- never arbitrary subdirectories. An authored
        reference such as ``mesh/<tree>/<lod>.fbx`` therefore only ever exists
        under the production root, and resolving it against the stage reported
        an on-disk file as absent.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = root / "stage"
            production = root / "production"
            stage.mkdir()
            external = production / "mesh" / "Tree_abc_8K_3d_ms"
            external.mkdir(parents=True)
            owned = external / "abc_LOD0.fbx"
            owned.write_bytes(b"external-mesh")
            target = stage / "tree.spm"
            self._spm_with_external_mesh(
                target, "mesh/Tree_abc_8K_3d_ms/abc_LOD0.fbx"
            )
            self._write_empty_generator_contract(target)
            state = {
                "stage_root": stage,
                "production_root": production,
            }

            graph = speedtree._validate_staged_speedtree_targets(
                [target], [state]
            )

            self.assertEqual(
                graph[transaction._path_key(production)],
                {transaction._path_key(owned)},
            )

    def test_staged_copy_still_wins_over_the_production_original(self):
        """A file this transaction staged remains the validated source."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = root / "stage"
            production = root / "production"
            (stage / "meshes").mkdir(parents=True)
            (production / "meshes").mkdir(parents=True)
            (stage / "meshes" / "shared.fbx").write_bytes(b"staged")
            (production / "meshes" / "shared.fbx").write_bytes(b"production")
            target = stage / "tree.spm"
            self._spm_with_external_mesh(target, "meshes/shared.fbx")
            self._write_empty_generator_contract(target)
            state = {
                "stage_root": stage,
                "production_root": production,
            }

            graph = speedtree._validate_staged_speedtree_targets(
                [target], [state]
            )

            # Staged hit maps back through stage_root, so the recorded
            # production path is the mirrored one rather than a stage path.
            self.assertEqual(
                graph[transaction._path_key(production)],
                {transaction._path_key(production / "meshes" / "shared.fbx")},
            )

    def test_unselected_sibling_missing_external_mesh_does_not_block_update(self):
        """A broken dummy reference in another SPM is outside this update."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = root / "stage"
            production = root / "production"
            (stage / "meshes").mkdir(parents=True)
            production.mkdir()
            shared = stage / "meshes" / "shared.fbx"
            shared.write_bytes(b"mesh")
            selected = stage / "selected.spm"
            sibling = stage / "unrelated.spm"
            self._spm_with_external_mesh(selected, "meshes/shared.fbx")
            self._spm_with_external_mesh(
                sibling,
                "../../../../Users/Park/OneDrive/root_set_broken_dummy.fbx",
            )
            self._write_empty_generator_contract(selected)
            state = {
                "stage_root": stage,
                "production_root": production,
            }

            graph = speedtree._validate_staged_speedtree_targets(
                [selected], [state]
            )

            self.assertEqual(
                graph[transaction._path_key(production)],
                {transaction._path_key(production / "meshes" / "shared.fbx")},
            )

    def test_validation_builds_shared_reference_graph_for_all_sibling_spms(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = root / "stage"
            production = root / "production"
            (stage / "meshes").mkdir(parents=True)
            production.mkdir()
            shared = stage / "meshes" / "shared.fbx"
            shared.write_bytes(b"mesh")
            selected = stage / "selected.spm"
            sibling = stage / "sibling.spm"
            self._spm_with_external_mesh(selected, "meshes/shared.fbx")
            self._spm_with_external_mesh(sibling, "meshes/shared.fbx")
            self._write_empty_generator_contract(selected)
            state = {
                "stage_root": stage,
                "production_root": production,
            }

            graph = speedtree._validate_staged_speedtree_targets([selected], [state])

            root_key = transaction._path_key(production)
            self.assertEqual(
                graph[root_key],
                {transaction._path_key(production / "meshes" / "shared.fbx")},
            )


if __name__ == "__main__":
    unittest.main()
