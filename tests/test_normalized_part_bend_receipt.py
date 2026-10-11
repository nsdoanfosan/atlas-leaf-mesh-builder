"""Test the actual publisher projection without Blender, files, or app calls."""

import ast
import copy
import json
import math
from pathlib import Path
import re
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "addons/atlas_leaf_mesh_builder/speedtree.py"
FUNCTIONS = {"_checked_normalized_bounds", "_validated_normalized_bend_receipt_fields",
             "physical_normalization_receipt_from_scene"}
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
scope = {"copy": copy, "json": json, "math": math, "re": re}
exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                             and node.name in FUNCTIONS], type_ignores=[]), str(SOURCE), "exec"), scope)
project = scope["physical_normalization_receipt_from_scene"]


def source_report(role="cluster"):
    bounds = {"minimum": [-1.0, 0.0, 0.0], "maximum": [1.0, 8.0, 0.0],
              "size": [2.0, 8.0, 0.0], "center": [0.0, 4.0, 0.0]}
    payload = None if role is None else {
        "schema_version": 2, "role": role, "role_code": 2 if role == "leaf_side" else 1,
        "uv_name": "nanite_part_bend", "uv_index": 3,
        "blender_encoding": "U=8+clamp(Y/maxY,0,1)*sqrt(max(1,bboxY/max(bboxX,bboxZ)));V=1-role_code",
        "unreal_encoding": "U=8+mask*aspect_correction;V=role_code",
        "aspect_policy": "normalized_bbox_y_over_max_xz/sqrt_gain/v2",
        "reference_weight_exponent": 2.0, "growth_axis_blender": "+Y",
        "growth_axis_unreal": "-Y", "anchor_local": [0.0, 0.0, 0.0],
        "normalized_bounds": {key: copy.deepcopy(bounds[key]) for key in ("minimum", "maximum", "size")},
        "aspect_ratio": 4.0, "aspect_gain": 4.0, "aspect_correction": 2.0,
        "vertex_count": 3, "loop_count": 6,
        "protected_uv_sha256": {"0": "b" * 64, "1": "c" * 64, "2": "d" * 64},
        "mask_minimum": 0.0, "mask_maximum": 1.0,
        "maximum_y": 8.0, "minimum_y": 0.0,
    }
    return {
        "schema_version": 2, "workflow_mode": "PHYSICAL_DIRECT_CAPTURE",
        "source_preserved": True, "geometry_committed": True,
        "source_3d_contract": {"source_fbx": {"path": "custom.fbx", "sha256": "e" * 64}},
        "capture_role": "branch", "part_bend_role": role, "prototype_count": 1,
        "physical_capture_contract": {"contract_sha256": "a" * 64},
        "physical_capture_contract_sha256": "a" * 64,
        "prototypes": [{"prototype_index": 1, "skeletal_asset": "CustomNormalizedPart42",
                        "normalized_bounds": bounds, "vertex_count": 3, "part_bend_payload": payload}],
        "variants": [{"index": 1, "skeletal_asset": "CustomNormalizedPart42",
                      "plan": "CustomPlan42", "normalized_bounds": copy.deepcopy(bounds),
                      "object_transforms_identity": True, "plan_covers_projection": True,
                      "plan_uv_transfer": {"policy": "direct_physical_capture_projection"}}],
    }


def receipt(report):
    return project({"speedtree_cluster_normalizer_last_report": json.dumps(report)})


class NormalizedPartBendReceiptTests(unittest.TestCase):
    def test_custom_branch_capture_keeps_cluster_bend_and_full_producer_payload(self):
        report = source_report()
        before = copy.deepcopy(report)
        actual = receipt(report)
        self.assertEqual(report, before)
        self.assertEqual(report["capture_role"], "branch")
        self.assertEqual(actual["part_bend_role"], "cluster")
        self.assertEqual(actual["prototype_count"], 1)
        self.assertEqual(actual["source_3d_contract"], report["source_3d_contract"])
        self.assertEqual(actual["prototypes"][0]["part_bend_payload"],
                         report["prototypes"][0]["part_bend_payload"])
        self.assertEqual(actual["prototypes"][0]["normalized_bounds"],
                         report["prototypes"][0]["normalized_bounds"])
        self.assertEqual(actual["prototypes"][0]["skeletal_asset"], "CustomNormalizedPart42")
        self.assertNotIn("capture_role", actual)

    def test_explicit_none_keeps_present_null_markers(self):
        actual = receipt(source_report(None))
        self.assertIn("part_bend_role", actual)
        self.assertIsNone(actual["part_bend_role"])
        self.assertIn("part_bend_payload", actual["prototypes"][0])
        self.assertIsNone(actual["prototypes"][0]["part_bend_payload"])
        self.assertEqual(actual["prototype_count"], 1)

    def test_legacy_missing_role_stays_unknown_instead_of_becoming_disabled(self):
        report = source_report(None)
        del report["part_bend_role"]
        del report["prototypes"][0]["part_bend_payload"]
        actual = receipt(report)
        self.assertNotIn("part_bend_role", actual)
        self.assertNotIn("part_bend_payload", actual["prototypes"][0])

    def test_explicit_leaf_and_side_semantics_work_with_noncanonical_names(self):
        for role, code in (("leaf", 1), ("leaf_side", 2)):
            actual = receipt(source_report(role))
            self.assertEqual(actual["part_bend_role"], role)
            self.assertEqual(actual["prototypes"][0]["part_bend_payload"]["role_code"], code)

    def test_source_and_payload_schema_role_mismatches_fail(self):
        changes = (
            lambda value: value.update(schema_version=1),
            lambda value: value.update(schema_version=2.0),
            lambda value: value.update(part_bend_role="unsupported"),
            lambda value: value["prototypes"][0]["part_bend_payload"].update(schema_version=1),
            lambda value: value["prototypes"][0]["part_bend_payload"].update(role="leaf"),
            lambda value: value["prototypes"][0]["part_bend_payload"].update(role_code=2),
            lambda value: value["prototypes"][0].pop("part_bend_payload"),
            lambda value: value.pop("part_bend_role"),
        )
        for change in changes:
            report = source_report()
            change(report)
            with self.assertRaises(RuntimeError):
                receipt(report)

    def test_disabled_source_cannot_publish_active_or_absent_payload(self):
        for payload in (source_report()["prototypes"][0]["part_bend_payload"], "absent"):
            report = source_report(None)
            if payload == "absent":
                report["prototypes"][0].pop("part_bend_payload")
            else:
                report["prototypes"][0]["part_bend_payload"] = payload
            with self.assertRaises(RuntimeError):
                receipt(report)

    def test_stale_bounds_gain_hash_and_geometry_receipts_fail(self):
        changes = (
            lambda value: value["prototypes"][0]["part_bend_payload"]["normalized_bounds"]["maximum"].__setitem__(1, 9),
            lambda value: value["prototypes"][0]["part_bend_payload"].update(aspect_correction=1),
            lambda value: value["prototypes"][0]["part_bend_payload"]["protected_uv_sha256"].pop("2"),
            lambda value: value["prototypes"][0]["part_bend_payload"].update(loop_count=0),
            lambda value: value.update(geometry_committed=False),
            lambda value: value.update(source_preserved=False),
            lambda value: value.update(prototype_count=2),
            lambda value: value.update(source_3d_contract={}),
            lambda value: value["physical_capture_contract"].update(contract_sha256="f" * 64),
        )
        for change in changes:
            report = source_report()
            change(report)
            with self.assertRaises(RuntimeError):
                receipt(report)

    def test_projection_has_no_shared_mutable_metadata(self):
        report = source_report()
        before = copy.deepcopy(report)
        actual = project({"speedtree_cluster_normalizer_last_report": report})
        actual["prototypes"][0]["part_bend_payload"]["protected_uv_sha256"]["0"] = "changed"
        actual["source_3d_contract"]["source_fbx"]["sha256"] = "changed"
        self.assertEqual(report, before)


if __name__ == "__main__":
    unittest.main()
