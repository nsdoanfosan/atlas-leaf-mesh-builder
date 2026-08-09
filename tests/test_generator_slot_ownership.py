import copy
import importlib.util
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO
    / "addons"
    / "atlas_leaf_mesh_builder"
    / "generator_slot_ownership.py"
)


def load_module():
    name = "atlas_generator_slot_ownership_contract"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ownership = load_module()


def binding(
    guid,
    slot,
    material_id,
    mesh_id,
    *,
    created=False,
    index=0,
):
    row = {
        "state": "changed",
        "generator_index": index,
        "generator_name": f"Generator {index}",
        "generator_guid": guid,
        "generator_type": (
            "Frond" if slot.startswith("Material:Frond") else "Leaf Mesh"
        ),
        "slot_prefix": slot,
        "source_material_id": 4,
        "source_material_name": "M_source",
        "source_mesh_id": 6,
        "source_object": "leaf_01",
        "leaf_ordinal": 1,
        "target_material_id": material_id,
        "target_material_name": "M_generated",
        "target_mesh_id": mesh_id,
        "created_slot": created,
    }
    if created:
        parent, slot_index = slot.rsplit(":", 1)
        row.update({
            "variant_parent_property": parent,
            "variant_parent_children_before": int(slot_index),
            "variant_parent_children_after": int(slot_index) + 1,
            "created_material_property": f"{slot}:Material",
            "created_mesh_property": f"{slot}:Mesh",
            "created_property_names": [
                f"{slot}:Material",
                f"{slot}:Mesh",
                f"{slot}:Weight",
            ],
        })
    return row


def manifest(rows):
    return {
        "blend_file": "C:/fixture/provider.blend",
        "source_collection": "Atlas_Cards",
        "export_scope_id": "provider-scope",
        "generator_connection": {
            "requested": True,
            "complete": True,
            "bindings": copy.deepcopy(rows),
        },
    }


class GeneratorBindingOwnershipTests(unittest.TestCase):
    def test_projection_is_exact_sorted_and_stably_fingerprinted(self):
        rows = [
            binding("ZGuid==", "Leaves:Type:100", 9, 109, index=3),
            binding("AGuid==", "Material:Frond:9", 8, 42, index=1),
        ]

        contract = ownership.build_generator_binding_ownership(rows)

        self.assertEqual(
            contract,
            {
                "contract": "atlas_generator_current_binding_ownership",
                "version": 1,
                "basis": "live_spm_material_mesh_projection",
                "binding_count": 2,
                "fingerprint": (
                    "9c117951e105e8ee7d75b1c161268e0ca1220b7d54ffd00c8891"
                    "32ca781cb0a9"
                ),
                "bindings": [
                    {
                        "generator_guid": "AGuid==",
                        "slot_prefix": "Material:Frond:9",
                        "target_material_id": 8,
                        "target_mesh_id": 42,
                    },
                    {
                        "generator_guid": "ZGuid==",
                        "slot_prefix": "Leaves:Type:100",
                        "target_material_id": 9,
                        "target_mesh_id": 109,
                    },
                ],
            },
        )
        self.assertEqual(
            ownership.validate_generator_binding_ownership(contract),
            contract["bindings"],
        )

    def test_guid_case_is_opaque_and_duplicate_slots_fail_closed(self):
        distinct = [
            binding("AbCd==", "Leaves:Type:7", 8, 20),
            binding("abcd==", "Leaves:Type:7", 9, 21),
        ]
        self.assertEqual(
            len(ownership.canonical_ownership_bindings(distinct)),
            2,
        )
        with self.assertRaisesRegex(
            ownership.GeneratorSlotOwnershipError,
            "duplicate Generator slots",
        ):
            ownership.build_generator_binding_ownership(
                [distinct[0], {**distinct[0], "target_mesh_id": 99}]
            )

    def test_explicit_empty_ownership_never_falls_back_to_authored_rows(self):
        payload = manifest(
            [binding("Guid==", "Leaves:Type:42", 8, 90)]
        )
        payload["generator_connection"]["authored_bindings"] = copy.deepcopy(
            payload["generator_connection"]["bindings"]
        )
        payload["generator_connection"]["bindings"] = []
        payload["generator_binding_ownership"] = (
            ownership.build_generator_binding_ownership([])
        )

        self.assertEqual(ownership.current_bindings(payload), [])
        self.assertEqual(len(ownership.authored_bindings(payload)), 1)

    def test_tampered_nested_contract_fails_closed(self):
        contract = ownership.build_generator_binding_ownership(
            [binding("Guid==", "Leaves:Type:0", 8, 90)]
        )
        contract["bindings"][0]["target_mesh_id"] = 999
        with self.assertRaisesRegex(
            ownership.GeneratorSlotOwnershipError,
            "fingerprint mismatch",
        ):
            ownership.validate_generator_binding_ownership(contract)

    def test_material_default_mesh_sentinel_accepts_arbitrary_slot_prefix(self):
        row = binding(
            "OpaqueGuid==",
            "Custom:Opaque:Branch:42",
            8,
            ownership.MATERIAL_DEFAULT_MESH_ID,
            created=True,
        )

        contract = ownership.build_generator_binding_ownership([row])
        creation = ownership.build_generator_slot_creation_provenance([row])

        self.assertEqual(
            contract["bindings"][0]["slot_prefix"],
            "Custom:Opaque:Branch:42",
        )
        self.assertEqual(
            contract["bindings"][0]["target_mesh_id"],
            ownership.MATERIAL_DEFAULT_MESH_ID,
        )
        self.assertEqual(
            creation["slots"][0]["initial_target_mesh_id"],
            ownership.MATERIAL_DEFAULT_MESH_ID,
        )
        plan = ownership.plan_live_binding_reconciliation(
            [{"path": "C:/fixture/scope.json", "payload": manifest([row])}],
            [row],
        )
        self.assertEqual(plan["status"], "repairable")
        self.assertEqual(
            next(iter(plan["owners"].values()))["binding"]["target_mesh_id"],
            ownership.MATERIAL_DEFAULT_MESH_ID,
        )
        for invalid in (0, -1, -9, -11):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                ownership.GeneratorSlotOwnershipError,
                "positive integer or -10",
            ):
                ownership.build_generator_binding_ownership([
                    {**row, "target_mesh_id": invalid}
                ])


class GeneratorSlotCreationProvenanceTests(unittest.TestCase):
    def test_created_binding_becomes_immutable_initial_provenance(self):
        created = binding(
            "Guid==",
            "Leaves:Type:42",
            8,
            90,
            created=True,
            index=4,
        )

        contract = ownership.build_generator_slot_creation_provenance(
            [created]
        )
        row = contract["slots"][0]

        self.assertNotIn("state", row)
        self.assertNotIn("created_slot", row)
        self.assertNotIn("target_material_id", row)
        self.assertNotIn("target_mesh_id", row)
        self.assertEqual(row["initial_target_material_id"], 8)
        self.assertEqual(row["initial_target_material_name"], "M_generated")
        self.assertEqual(row["initial_target_mesh_id"], 90)
        self.assertEqual(row["source_material_id"], 4)
        self.assertEqual(row["source_mesh_id"], 6)
        self.assertEqual(row["variant_parent_property"], "Leaves:Type")
        self.assertEqual(contract["slot_count"], 1)
        self.assertEqual(
            ownership.validate_generator_slot_creation_provenance(contract),
            contract["slots"],
        )

    def test_relinquishment_keeps_creator_and_authored_snapshots(self):
        type_zero = binding("Guid==", "Leaves:Type:0", 8, 89)
        created = binding(
            "Guid==", "Leaves:Type:42", 8, 90, created=True
        )
        payload = manifest([type_zero, created])
        relinquished = {
            **copy.deepcopy(created),
            "ownership_state": "relinquished",
            "reason": "live_spm_exact_successor_binding",
            "successor_provider": {
                "blend_file": "c:/fixture/successor.blend",
                "source_collection": "atlas_cards_2",
                "export_scope_id": "successor-scope",
            },
        }

        migrated = ownership.manifest_with_binding_contracts(
            payload,
            [type_zero],
            relinquished_rows=[relinquished],
        )

        connection = migrated["generator_connection"]
        self.assertEqual(connection["bindings"], [type_zero])
        self.assertEqual(connection["authored_bindings"], [type_zero, created])
        self.assertEqual(len(connection["relinquished_bindings"]), 1)
        self.assertEqual(
            migrated["generator_binding_ownership"]["binding_count"],
            1,
        )
        self.assertEqual(
            migrated["generator_slot_creation_provenance"]["slots"][0][
                "slot_prefix"
            ],
            "Leaves:Type:42",
        )

        repeated = ownership.manifest_with_binding_contracts(
            migrated,
            [type_zero],
            relinquished_rows=[relinquished],
        )
        self.assertEqual(repeated, migrated)

    def test_repeat_refresh_appends_new_authored_slots_without_rewriting_history(self):
        original = binding("Guid==", "Leaves:Type:0", 8, 89)
        payload = manifest([original])
        payload["generator_connection"]["authored_bindings"] = [
            copy.deepcopy(original)
        ]
        refreshed = {**copy.deepcopy(original), "target_mesh_id": 99}
        added = binding(
            "Guid==",
            "Material:Frond:100",
            10,
            110,
            created=True,
        )

        migrated = ownership.manifest_with_binding_contracts(
            payload,
            [refreshed, added],
        )

        self.assertEqual(
            migrated["generator_connection"]["authored_bindings"],
            [original, added],
        )
        self.assertEqual(
            migrated["generator_slot_creation_provenance"]["slots"][0][
                "slot_prefix"
            ],
            "Material:Frond:100",
        )


class LiveBindingReconciliationTests(unittest.TestCase):
    def record(self, name, scope, rows):
        payload = manifest(rows)
        payload["blend_file"] = f"C:/fixture/{name}.blend"
        payload["source_collection"] = name
        payload["export_scope_id"] = scope
        return {
            "path": f"C:/fixture/.atlas_leaf_speedtree_scopes/{scope}.json",
            "payload": payload,
        }

    def test_legacy_binding_without_guid_migrates_from_exact_live_slot(self):
        live = binding(
            "OpaqueLiveGuid==",
            "Leaves:Type:0",
            14,
            16,
            index=1,
        )
        legacy = copy.deepcopy(live)
        legacy.pop("generator_guid")
        record = self.record("legacy", "legacy-scope", [legacy])

        plan = ownership.plan_live_binding_reconciliation(
            [record],
            [live],
        )

        self.assertEqual(plan["status"], "repairable")
        update = next(iter(plan["provider_updates"].values()))
        migrated = update["records"][0]["payload"][
            "generator_connection"
        ]["bindings"][0]
        self.assertEqual(migrated["generator_guid"], "OpaqueLiveGuid==")
        self.assertEqual(
            migrated["legacy_generator_guid_migration"],
            "exact_live_generator_slot",
        )

    def test_ambiguous_legacy_guid_migration_fails_closed(self):
        legacy = binding("discard", "Leaves:Type:0", 14, 16)
        legacy.pop("generator_guid")
        for field in ("generator_index", "generator_name", "generator_type"):
            legacy.pop(field)
        live_a = binding("GuidA==", "Leaves:Type:0", 14, 16, index=1)
        live_b = binding("GuidB==", "Leaves:Type:0", 14, 16, index=2)

        with self.assertRaisesRegex(
            ownership.GeneratorSlotOwnershipError,
            "no unique live Generator GUID migration",
        ):
            ownership.plan_live_binding_reconciliation(
                [self.record("legacy", "legacy-scope", [legacy])],
                [live_a, live_b],
            )

    def test_live_spm_splits_one_generator_across_two_providers(self):
        guid = "OpaqueGuid=="
        provider_a_rows = [
            binding(guid, "Leaves:Type:0", 8, 89),
            binding(guid, "Leaves:Type:1", 8, 90, created=True),
            binding(guid, "Leaves:Type:2", 8, 91, created=True),
            binding(guid, "Leaves:Type:3", 8, 92, created=True),
        ]
        provider_b_rows = [
            binding(guid, "Leaves:Type:1", 10, 93),
            binding(guid, "Leaves:Type:2", 10, 94),
        ]
        live = [
            binding(guid, "Leaves:Type:0", 8, 89),
            binding(guid, "Leaves:Type:1", 10, 93),
            binding(guid, "Leaves:Type:2", 10, 94),
            binding(guid, "Leaves:Type:3", 8, 92),
        ]

        plan = ownership.plan_live_binding_reconciliation(
            [
                self.record("provider-a", "scope-a", provider_a_rows),
                self.record("provider-b", "scope-b", provider_b_rows),
            ],
            live,
        )

        self.assertEqual(plan["status"], "repairable")
        updates = {
            row["identity"]["export_scope_id"]: row
            for row in plan["provider_updates"].values()
        }
        self.assertEqual(
            [row["slot_prefix"] for row in updates["scope-a"]["current_bindings"]],
            ["Leaves:Type:0", "Leaves:Type:3"],
        )
        self.assertEqual(
            [row["slot_prefix"] for row in updates["scope-b"]["current_bindings"]],
            ["Leaves:Type:1", "Leaves:Type:2"],
        )
        self.assertEqual(
            {
                row["slot_prefix"]
                for row in updates["scope-a"]["relinquished_bindings"]
            },
            {"Leaves:Type:1", "Leaves:Type:2"},
        )

        migrated_a = ownership.manifest_with_binding_contracts(
            plan["provider_updates"][
                next(
                    key
                    for key, value in plan["provider_updates"].items()
                    if value["identity"]["export_scope_id"] == "scope-a"
                )
            ]["records"][0]["payload"],
            updates["scope-a"]["current_bindings"],
            relinquished_rows=updates["scope-a"]["relinquished_bindings"],
        )
        self.assertEqual(
            {
                row["slot_prefix"]
                for row in migrated_a[
                    "generator_slot_creation_provenance"
                ]["slots"]
            },
            {"Leaves:Type:1", "Leaves:Type:2", "Leaves:Type:3"},
        )

    def test_sparse_prefixes_three_providers_and_repeated_handoffs(self):
        guid = "OpaqueGuid=="
        a = self.record(
            "a",
            "scope-a",
            [binding(guid, "Leaves:Type:42", 8, 80)],
        )
        b = self.record(
            "b",
            "scope-b",
            [binding(guid, "Leaves:Type:42", 9, 90)],
        )
        c = self.record(
            "c",
            "scope-c",
            [binding(guid, "Material:Frond:100", 10, 100)],
        )
        first = ownership.plan_live_binding_reconciliation(
            [a, b, c],
            [
                binding(guid, "Leaves:Type:42", 9, 90),
                binding(guid, "Material:Frond:100", 10, 100),
            ],
        )
        self.assertEqual(first["status"], "repairable")

        migrated = {}
        for key, update in first["provider_updates"].items():
            migrated[key] = ownership.manifest_with_binding_contracts(
                update["records"][0]["payload"],
                update["current_bindings"],
                relinquished_rows=update["relinquished_bindings"],
            )

        # An explicitly reissued A receipt can later take the slot back.  The
        # old B claim is relinquished by the same live-authoritative rule; A's
        # earlier authored and creator history need not be rediscovered from
        # slot numbering.
        a_key = next(
            key
            for key, payload in migrated.items()
            if payload["export_scope_id"] == "scope-a"
        )
        a_reissued = ownership.manifest_with_binding_contracts(
            migrated[a_key],
            [binding(guid, "Leaves:Type:42", 8, 81)],
        )
        records = []
        for key, payload in migrated.items():
            records.append({
                "path": f"C:/fixture/{payload['export_scope_id']}.json",
                "payload": a_reissued if key == a_key else payload,
            })
        second = ownership.plan_live_binding_reconciliation(
            records,
            [
                binding(guid, "Leaves:Type:42", 8, 81),
                binding(guid, "Material:Frond:100", 10, 100),
            ],
        )
        self.assertEqual(second["status"], "repairable")
        owner = next(
            row
            for row in second["owners"].values()
            if row is not None
            and row["binding"]["slot_prefix"] == "Leaves:Type:42"
        )
        self.assertEqual(
            second["providers"][owner["provider_key"]]["identity"][
                "export_scope_id"
            ],
            "scope-a",
        )

    def test_same_live_pair_claimed_by_two_providers_is_rebased_unowned(self):
        guid = "OpaqueGuid=="
        plan = ownership.plan_live_binding_reconciliation(
            [
                self.record(
                    "a",
                    "scope-a",
                    [binding(guid, "Leaves:Type:7", 8, 90)],
                ),
                self.record(
                    "b",
                    "scope-b",
                    [binding(guid, "Leaves:Type:7", 8, 90)],
                ),
            ],
            [binding(guid, "Leaves:Type:7", 8, 90)],
        )

        self.assertEqual(plan["status"], "repairable")
        self.assertEqual(plan["blocking"], [])
        self.assertIsNone(next(iter(plan["owners"].values())))

    def test_live_pair_without_exact_provider_claim_is_rebased_unowned(self):
        guid = "OpaqueGuid=="
        plan = ownership.plan_live_binding_reconciliation(
            [
                self.record(
                    "stale",
                    "scope-stale",
                    [binding(guid, "Material:Frond:0", 8, 90)],
                ),
            ],
            [binding(guid, "Material:Frond:0", 10, -10)],
        )

        self.assertEqual(plan["status"], "repairable")
        self.assertEqual(plan["blocking"], [])
        self.assertIsNone(next(iter(plan["owners"].values())))


if __name__ == "__main__":
    unittest.main()
