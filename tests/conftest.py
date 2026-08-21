"""CI collection guard for producer contracts carried by feature branches."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


CONTRACTS = (
    {
        "name": "multi-target atomicity and rollback",
        "marker_path": Path(
            "addons/atlas_leaf_mesh_builder/speedtree_transaction.py"
        ),
        "marker_text": None,
        "test_path": Path("tests/test_speedtree_transaction.py"),
        "required": {
            "test_forced_failure_at_target_n_leaves_every_file_byte_for_byte",
            "test_idempotent_update_performs_no_production_replace",
            "test_mid_commit_failure_restores_all_targets_and_shared_outputs",
        },
    },
    {
        "name": "deleted-mesh tombstone lifecycle",
        "marker_path": Path("addons/atlas_leaf_mesh_builder/speedtree.py"),
        "marker_text": "def retire_deleted_generator_bindings(",
        "test_path": Path("tests/test_speedtree_xml.py"),
        "required": {
            "test_completely_empty_collection_publishes_idempotent_target_tombstone",
            "test_deleted_ordinal_is_retired_when_target_id_is_reused",
            "test_middle_deleted_ordinal_restores_authored_generator_binding",
        },
    },
    {
        "name": "fleet refresh, rollback, and idempotence",
        "marker_path": Path("tools/atlas_fleet_refresh.py"),
        "marker_text": None,
        "test_path": Path("tests/test_atlas_fleet_refresh.py"),
        "required": {
            "test_backup_and_rollback_restore_bytes_and_delete_new_managed_files",
            "test_plan_discovers_exact_registry_and_complete_mutable_inventory",
            "test_run_fleet_can_force_failure_after_recorded_registry",
        },
    },
    {
        "name": "sealed Generator delivery-scope receipts",
        "marker_path": Path(
            "addons/atlas_leaf_mesh_builder/generator_delivery_scope.py"
        ),
        "marker_text": None,
        "test_path": Path("tests/test_generator_delivery_scope.py"),
        "required": {
            "test_partial_tampered_and_foreign_intents_fail_closed",
            "test_pr98_consumer_accepts_producer_fixture_without_inference",
            "test_sanitized_hidden_zero_node_duplicate_mesh_fixture_seals",
        },
    },
)


def _implementation_present(root: Path, contract: dict) -> bool:
    marker_path = root / contract["marker_path"]
    if not marker_path.is_file():
        return False
    marker_text = contract["marker_text"]
    if marker_text is None:
        return True
    return marker_text in marker_path.read_text(encoding="utf-8")


def _test_function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def pytest_sessionstart(session: pytest.Session) -> None:
    """Fail collection if a feature lands without its focused contract tests."""

    root = Path(session.config.rootpath)
    failures = []
    for contract in CONTRACTS:
        if not _implementation_present(root, contract):
            continue
        test_path = root / contract["test_path"]
        if not test_path.is_file():
            failures.append(f"{contract['name']}: missing {contract['test_path']}")
            continue
        missing = sorted(contract["required"] - _test_function_names(test_path))
        if missing:
            failures.append(
                f"{contract['name']}: missing focused tests {', '.join(missing)}"
            )

    if failures:
        raise pytest.UsageError(
            "Producer contract collection is incomplete:\n- " + "\n- ".join(failures)
        )
