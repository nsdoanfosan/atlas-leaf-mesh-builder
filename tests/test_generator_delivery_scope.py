import copy
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO
    / "addons"
    / "atlas_leaf_mesh_builder"
    / "generator_delivery_scope.py"
)
FIXTURE_PATH = (
    REPO / "tests" / "fixtures" / "issue8_generator_delivery_scope_v1.json"
)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


producer = load_module("atlas_issue8_generator_delivery_scope", MODULE_PATH)


def fixture():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    intent = copy.deepcopy(payload["intent_projection"])
    intent["intent_sha256"] = producer.canonical_sha256(intent)
    return payload, intent


def consumer_repo_path():
    configured = str(os.environ.get("SPEEDTREE_BATCH_TOOLS_REPO") or "").strip()
    candidates = [
        Path(configured) if configured else None,
        (
            Path.home()
            / "Documents"
            / "CodexWorktrees"
            / "speedtree-issue-96-generator-delivery-scope"
        ),
        REPO.parent / "speedtree-batch-tools",
    ]
    return next(
        (
            candidate
            for candidate in candidates
            if candidate is not None
            and (candidate / "generator_delivery_scope.py").is_file()
        ),
        None,
    )


class GeneratorDeliveryScopeContractTests(unittest.TestCase):
    def test_sanitized_hidden_zero_node_duplicate_mesh_fixture_seals(self):
        payload, intent = fixture()
        observations = payload["runtime_observations_not_authority"]
        planned = [
            row["slot_identity"]
            for row in intent["authored_slots"]
        ]

        validated = producer.validate_planned_delivery_scope(
            intent,
            planned,
            target_spm=intent["target"]["spm"],
            material_id=4,
            provider_blend=intent["target"]["provider_blend"],
            provider_scope_id="issue8-provider-scope",
        )
        scope = producer.build_resolved_delivery_scope(
            intent,
            payload["bindings"],
            "a" * 64,
        )

        self.assertEqual(len(observations), 4)
        self.assertEqual(len(validated["required_live_slot_identities"]), 1)
        self.assertEqual(len(validated["continuity_only_slot_identities"]), 3)
        self.assertEqual(
            [row["target_mesh_id"] for row in intent["authored_slots"]][:2],
            [93, 93],
        )
        self.assertEqual(
            set(scope),
            {"kind", "schema_version", "intent", "resolved"},
        )
        self.assertEqual(
            set(scope["resolved"]),
            {
                "kind",
                "schema_version",
                "intent_sha256",
                "bindings_sha256",
                "target_spm_postwrite_sha256",
                "resolved_sha256",
            },
        )

    def test_explicit_empty_required_is_valid_and_all_continuity(self):
        _payload, intent = fixture()
        intent.pop("intent_sha256")
        intent["required_live_slot_identities"] = []
        intent["continuity_only_slots"] = [
            {
                "slot_identity": row["slot_identity"],
                "reason": "explicit recipe-owned empty-required fixture",
                "policy": producer.CONTINUITY_ONLY_POLICY,
                "provenance": {"fixture": "explicit-empty", "revision": 1},
            }
            for row in intent["authored_slots"]
        ]
        intent["intent_sha256"] = producer.canonical_sha256(intent)

        validated = producer.validate_delivery_scope_intent(intent)

        self.assertEqual(validated["required_live_slot_identities"], set())
        self.assertEqual(
            validated["continuity_only_slot_identities"],
            validated["authored_slot_identities"],
        )

    def test_partial_tampered_and_foreign_intents_fail_closed(self):
        _payload, intent = fixture()
        partial = copy.deepcopy(intent)
        partial.pop("intent_sha256")
        partial["authored_slots"].pop()
        partial["continuity_only_slots"].pop()
        partial["intent_sha256"] = producer.canonical_sha256(partial)
        with self.assertRaisesRegex(
            producer.GeneratorDeliveryScopeError,
            "exact pre-write Generator slot plan",
        ):
            producer.validate_planned_delivery_scope(
                partial,
                [row["slot_identity"] for row in intent["authored_slots"]],
            )

        tampered = copy.deepcopy(intent)
        tampered["continuity_only_slots"].pop()
        with self.assertRaisesRegex(
            producer.GeneratorDeliveryScopeError,
            "exact authored-minus-required complement|hash mismatch",
        ):
            producer.validate_delivery_scope_intent(tampered)

        with self.assertRaisesRegex(
            producer.GeneratorDeliveryScopeError,
            "another provider blend",
        ):
            producer.validate_delivery_scope_intent(
                intent,
                provider_blend="C:/sanitized/foreign-provider.blend",
            )
        with self.assertRaisesRegex(
            producer.GeneratorDeliveryScopeError,
            "another provider scope",
        ):
            producer.validate_delivery_scope_intent(
                intent,
                provider_scope_id="foreign-scope",
            )

    def test_stale_binding_or_spm_fingerprint_is_rejected(self):
        payload, intent = fixture()
        connection = {
            "bindings": copy.deepcopy(payload["bindings"]),
            "delivery_scope": producer.build_resolved_delivery_scope(
                intent,
                payload["bindings"],
                "a" * 64,
            ),
        }
        connection["bindings"][0]["target_mesh_id"] = 999
        with self.assertRaisesRegex(
            producer.GeneratorDeliveryScopeError,
            "differ from sealed authored slots",
        ):
            producer.validate_resolved_delivery_scope(
                connection,
                target_spm=intent["target"]["spm"],
                material_id=4,
                provider_blend=intent["target"]["provider_blend"],
                target_spm_postwrite_sha256="a" * 64,
            )

        connection["bindings"] = copy.deepcopy(payload["bindings"])
        with self.assertRaisesRegex(
            producer.GeneratorDeliveryScopeError,
            "target SPM hash mismatch",
        ):
            producer.validate_resolved_delivery_scope(
                connection,
                target_spm=intent["target"]["spm"],
                material_id=4,
                provider_blend=intent["target"]["provider_blend"],
                target_spm_postwrite_sha256="b" * 64,
            )

    def test_pr98_consumer_accepts_producer_fixture_without_inference(self):
        consumer_repo = consumer_repo_path()
        if consumer_repo is None:
            self.skipTest(
                "set SPEEDTREE_BATCH_TOOLS_REPO to the PR #98 worktree"
            )
        sys.path.insert(0, str(consumer_repo))
        try:
            consumer = load_module(
                "speedtree_pr98_generator_delivery_scope_contract",
                consumer_repo / "generator_delivery_scope.py",
            )
        finally:
            sys.path.remove(str(consumer_repo))

        payload, intent = fixture()
        connection = {
            "bindings": copy.deepcopy(payload["bindings"]),
            "delivery_scope": producer.build_resolved_delivery_scope(
                intent,
                payload["bindings"],
                "a" * 64,
            ),
        }
        consumer_result = consumer.validate_resolved_delivery_scope(
            connection,
            target_spm=intent["target"]["spm"],
            material_id=4,
            provider_blend=intent["target"]["provider_blend"],
            target_spm_postwrite_sha256="a" * 64,
        )

        self.assertEqual(
            consumer.canonical_authored_slots(payload["bindings"]),
            producer.canonical_authored_slots(payload["bindings"]),
        )
        self.assertEqual(
            consumer_result["intent_sha256"],
            intent["intent_sha256"],
        )
        self.assertEqual(
            consumer.RUNTIME_INACTIVE_POLICY,
            producer.RUNTIME_INACTIVE_POLICY,
        )


if __name__ == "__main__":
    unittest.main()
