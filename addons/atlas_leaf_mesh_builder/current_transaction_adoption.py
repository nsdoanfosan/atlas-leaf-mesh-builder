"""Authority policy for explicit external Cluster adoption transactions.

The Batch tool already chooses the exact target slice and passes
``adoption_targets`` into the Atlas atomic transaction API. That explicit
request is the authority for the current run. Historical Atlas scopes,
receipts, mesh IDs and collection identities are lineage data; they must not
veto an explicit current adoption before the atomic transaction reconciles and
publishes the new state.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path


def _raw_mapping(props):
    raw_text = str(
        getattr(props, "speedtree_source_materials_json", "") or ""
    ).strip()
    try:
        payload = json.loads(raw_text) if raw_text else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Source material mapping JSON is invalid: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            "Source material mapping JSON must be an object keyed by target SPM path."
        )
    return payload


def _matching_materials(assets, material_name):
    if assets is None:
        return []
    return [
        node
        for node in assets.findall("Material_v8")
        if node.attrib.get("Name") == material_name
    ]


def _select_current_material(speedtree, matches, requested_material_ids):
    requested_id = None
    values = list(requested_material_ids or [])
    if len(values) == 1:
        requested_id = speedtree.positive_int(values[0])
    if requested_id is not None:
        exact = [
            node
            for node in matches
            if speedtree.positive_int(node.attrib.get("ID")) == requested_id
        ]
        if exact:
            return exact[0]
    return matches[0] if matches else None


def _capture_live_adoption_baseline(
    speedtree,
    spm_path,
    manifest,
    material_name,
    material_id=None,
):
    """Snapshot the live target state at the start of this explicit transaction.

    Historical Atlas ownership is intentionally irrelevant here. The atomic
    transaction already owns the exact target slice, so the rollback baseline is
    simply the state that exists when this transaction starts.
    """
    root = speedtree.read_spm_xml(spm_path)
    assets = root.find("Assets")
    if assets is None:
        raise RuntimeError(
            "Target SPM has no Assets node for source-material adoption."
        )

    requested_id = speedtree.positive_int(material_id)
    matches = _matching_materials(assets, material_name)
    material = _select_current_material(speedtree, matches, [requested_id])
    if material is None:
        raise RuntimeError(
            f"Source material adoption could not find Material_v8 "
            f"'{material_name}'."
        )

    existing_id = speedtree.positive_int(material.attrib.get("ID"))
    if existing_id is None:
        raise RuntimeError(
            f"Source material '{material_name}' has an invalid ID."
        )

    original_mesh_ids = speedtree.spm_material_mesh_ids(material)
    if not original_mesh_ids:
        raise RuntimeError(
            f"Source material '{material_name}' has no cutout meshes to adopt."
        )

    mesh_nodes_by_id = {
        speedtree.positive_int(node.attrib.get("ID")): node
        for node in assets.findall("Mesh")
        if speedtree.positive_int(node.attrib.get("ID")) is not None
    }
    missing = [
        mesh_id
        for mesh_id in original_mesh_ids
        if mesh_id not in mesh_nodes_by_id
    ]
    if missing:
        raise RuntimeError(
            f"Source material adoption is missing Mesh assets {missing}."
        )

    return {
        "version": speedtree.SOURCE_MATERIAL_ADOPTION_VERSION,
        "scope": speedtree.spm_export_scope(manifest),
        "material_name": material_name,
        "material_id": existing_id,
        "original_mesh_ids": list(original_mesh_ids),
        "original_material_snapshot": speedtree.encode_spm_node_snapshot(
            material
        ),
        "original_mesh_snapshots": [
            {
                "mesh_id": mesh_id,
                "snapshot": speedtree.encode_spm_node_snapshot(
                    mesh_nodes_by_id[mesh_id]
                ),
            }
            for mesh_id in original_mesh_ids
        ],
        "generated_mesh_ids": [],
        "removed_original_mesh_ids": [],
        "reused_original_snapshot": False,
        "baseline_kind": "explicit_current_transaction_live_baseline",
    }


def enable_current_transaction_adoption_authority(speedtree):
    """Make explicit Cluster adoption use live state when history cannot help.

    The normal Atlas helper is still used when it can reuse a valid historical
    original snapshot. If that helper rejects only because historical ownership
    metadata is incomplete/mismatched, this external transaction falls back to
    the current live Material/Mesh state instead of failing.
    """
    current = speedtree.prepare_source_material_adoption
    if getattr(current, "_explicit_current_transaction_authority", False):
        return current

    def authoritative_prepare(
        spm_path,
        manifest,
        material_name,
        material_id=None,
        previous_manifest=None,
    ):
        try:
            return current(
                spm_path,
                manifest,
                material_name,
                material_id,
                previous_manifest,
            )
        except RuntimeError as exc:
            message = str(exc)
            historical_gate = (
                message.startswith(
                    "Previous source-material adoption manifest is incomplete or mismatched."
                )
                or message.startswith("Cannot newly adopt '")
            )
            if not historical_gate:
                raise
            return _capture_live_adoption_baseline(
                speedtree,
                spm_path,
                manifest,
                material_name,
                material_id,
            )

    authoritative_prepare._explicit_current_transaction_authority = True
    authoritative_prepare._wrapped_prepare = current
    speedtree.prepare_source_material_adoption = authoritative_prepare
    return authoritative_prepare


def extend_current_transaction_source_material_adoptions(
    props,
    target_spms,
    *,
    blend_path=None,
):
    """Apply current explicit adoption intent to every requested target.

    ``target_spms`` is already the exact adoption slice selected by the Batch
    transaction. Therefore this function does not ask historical receipts or
    Atlas scope markers for permission again. It resolves the live target
    material, installs the current-transaction adoption authority, and writes
    the request using that target-local Material ID.
    """
    del blend_path  # Kept for API compatibility; history is not an authority.

    from . import speedtree

    enable_current_transaction_adoption_authority(speedtree)

    raw_mapping = _raw_mapping(props)
    mapping = speedtree.speedtree_source_material_mapping(props)
    material_name = str(
        getattr(props, "speedtree_atlas_asset_name", "") or ""
    ).strip()
    if not material_name:
        raise RuntimeError(
            "Cluster relationship ON requires an exact atlas material name."
        )

    templates = [
        row
        for row in mapping.values()
        if row.get("source_material_names") == [material_name]
        and row.get("adopt_source_material") is True
    ]
    if not templates:
        raise RuntimeError(
            f"Cluster relationship ON has no source-material adoption template "
            f"for '{material_name}'."
        )

    template = templates[0]
    generator_variant_policy = speedtree.normalize_generator_variant_policy(
        template.get("generator_variant_policy")
    )

    added = []
    reconciled = []
    preserved = []

    for target_value in target_spms:
        target = Path(target_value).expanduser().absolute()
        target_key = speedtree.normalized_target_key(target)
        existing_request = mapping.get(target_key)

        if not target.is_file():
            raise RuntimeError(
                f"Cluster relationship target does not exist: {target}"
            )

        root = speedtree.read_spm_xml(target)
        assets = root.find("Assets")
        matches = _matching_materials(assets, material_name)
        if not matches:
            raise RuntimeError(
                f"Cluster relationship ON could not find Material_v8 "
                f"'{material_name}' in {target.name}."
            )

        same_current_request = bool(
            existing_request
            and existing_request.get("source_material_names") == [material_name]
            and existing_request.get("adopt_source_material") is True
        )
        requested_ids = (
            existing_request.get("source_material_ids")
            if same_current_request
            else None
        )
        material = _select_current_material(
            speedtree,
            matches,
            requested_ids,
        )
        material_id = speedtree.positive_int(material.attrib.get("ID"))
        if material_id is None:
            raise RuntimeError(
                f"Cluster relationship material '{material_name}' has an "
                f"invalid ID in {target.name}."
            )
        mesh_ids = speedtree.spm_material_mesh_ids(material)
        if not mesh_ids:
            raise RuntimeError(
                f"Cluster relationship material '{material_name}' has no "
                f"cutout meshes in {target.name}."
            )

        source_row = existing_request if same_current_request else template
        request = {
            "source_material_names": [material_name],
            "source_material_ids": [material_id],
            "adopt_source_material": True,
            "generator_variant_policy": generator_variant_policy,
            "source_binding_repairs": copy.deepcopy(
                (source_row or {}).get("source_binding_repairs") or []
            ),
        }
        delivery_intent = (source_row or {}).get(
            "generator_delivery_scope_intent"
        )
        if delivery_intent is not None:
            request["generator_delivery_scope_intent"] = copy.deepcopy(
                delivery_intent
            )

        raw_mapping[str(target)] = request
        row = {
            "target_spm": str(target),
            "material_name": material_name,
            "material_id": material_id,
            "source_mesh_ids": mesh_ids,
            "reused_existing_scope": False,
            "ownership_authority": "explicit_current_transaction",
        }
        if existing_request is None:
            added.append(row)
        elif same_current_request and existing_request.get(
            "source_material_ids"
        ) == [material_id]:
            preserved.append(str(target))
        else:
            reconciled.append(row)

    props.speedtree_source_materials_json = json.dumps(
        raw_mapping,
        ensure_ascii=False,
        sort_keys=True,
    )

    return {
        "material_name": material_name,
        "generator_variant_policy": generator_variant_policy,
        "authority_policy": "explicit_current_transaction_v3_live_baseline",
        "added": added,
        "reconciled": reconciled,
        "preserved": preserved,
    }
