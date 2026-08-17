"""Current-transaction authority for external Cluster material adoption.

Historical Atlas receipts prove lineage and rollback provenance.  They do not
freeze the live Material_v8 cutout mesh list forever: a later rebuild from the
same logical provider may legitimately replace those mesh IDs before Cluster
relationship ON is extended to another target.  This adapter is intentionally
used only by the external atomic transaction API.  Direct/legacy Atlas callers
keep the stricter legacy reconciliation path in ``speedtree.py``.
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


def _current_provider_manifest(props, blend_path):
    return {
        "blend_file": str(Path(blend_path).expanduser().absolute()),
        "source_collection": str(
            getattr(props, "collection_name", "") or ""
        ).strip(),
    }


def _same_source_adoption_predecessors(
    speedtree,
    props,
    target,
    material,
    material_name,
    material_id,
    marker,
    blend_path,
):
    """Return exact same-provider receipts without comparing stale mesh IDs."""
    if not blend_path or not isinstance(marker, dict):
        return []
    if marker.get("kind") != "material" or not marker.get("scope"):
        return []

    current_provider = _current_provider_manifest(props, blend_path)
    matches = []
    for previous in speedtree.target_scope_manifests_for_blend(
        target,
        blend_path,
    ):
        previous_scope = speedtree.spm_export_scope(previous)
        adoption = previous.get("source_material_adoption") or {}
        adoption_scope = str(adoption.get("scope") or "")
        valid_adoption = bool(
            adoption.get("version")
            == speedtree.SOURCE_MATERIAL_ADOPTION_VERSION
            and adoption.get("material_name") == material_name
            and speedtree.positive_int(adoption.get("material_id"))
            == material_id
            and adoption.get("original_material_snapshot")
            and adoption.get("original_mesh_snapshots")
            and (
                not adoption_scope
                or adoption_scope == previous_scope
            )
        )
        if not valid_adoption:
            continue
        if (
            marker.get("scope") != previous_scope
            or not speedtree.manifests_share_source_identity(
                current_provider,
                previous,
                material_name,
            )
        ):
            continue
        matches.append(previous)
    return matches


def _snapshot_key(speedtree, manifest, material_name, material_id):
    adoption = manifest.get("source_material_adoption") or {}
    if not (
        adoption.get("version") == speedtree.SOURCE_MATERIAL_ADOPTION_VERSION
        and adoption.get("material_name") == material_name
        and speedtree.positive_int(adoption.get("material_id")) == material_id
        and adoption.get("original_material_snapshot")
        and adoption.get("original_mesh_snapshots")
    ):
        return None
    return (
        adoption["original_material_snapshot"],
        json.dumps(adoption["original_mesh_snapshots"], sort_keys=True),
    )


def extend_current_transaction_source_material_adoptions(
    props,
    target_spms,
    *,
    blend_path=None,
):
    """Extend Cluster ON mappings while letting proven current data win.

    A tagged material is accepted as a same-provider successor when the exact
    target has a non-retired receipt for the same logical provider
    (blend/source collection/material), the live material marker names that
    receipt's scope, and the receipt still contains a complete original
    adoption snapshot.  The historical receipt's *final* mesh IDs are not
    compared to the current Material_v8 mesh list; those IDs are mutable output
    state, while the original snapshots remain rollback evidence.

    Anything that cannot satisfy this narrow successor proof is delegated to
    the existing legacy helper, preserving its foreign-provider and ambiguous
    ownership protections unchanged.
    """
    from . import speedtree

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
    policies = {
        speedtree.normalize_generator_variant_policy(
            row.get("generator_variant_policy")
        )
        for row in templates
    }
    if len(policies) != 1:
        raise RuntimeError(
            f"Cluster relationship ON has conflicting Generator variant "
            f"policies for '{material_name}'."
        )
    generator_variant_policy = next(iter(policies))

    added = []
    reconciled = []
    preserved = []
    legacy_targets = []

    for target_value in target_spms:
        target = Path(target_value).expanduser().absolute()
        target_key = speedtree.normalized_target_key(target)
        existing_request = mapping.get(target_key)
        if existing_request and not (
            existing_request.get("source_material_names") == [material_name]
            and existing_request.get("adopt_source_material") is True
        ):
            preserved.append(str(target))
            continue
        if not target.is_file():
            raise RuntimeError(
                f"Cluster relationship target does not exist: {target}"
            )

        root = speedtree.read_spm_xml(target)
        assets = root.find("Assets")
        matches = (
            [
                node
                for node in assets.findall("Material_v8")
                if node.attrib.get("Name") == material_name
            ]
            if assets is not None
            else []
        )
        requested_material_ids = list(
            (existing_request or {}).get("source_material_ids") or []
        )
        requested_material_id = (
            speedtree.positive_int(requested_material_ids[0])
            if len(requested_material_ids) == 1
            else None
        )
        if requested_material_id is not None:
            exact = [
                node
                for node in matches
                if speedtree.positive_int(node.attrib.get("ID"))
                == requested_material_id
            ]
            if exact:
                matches = exact
        if not matches:
            raise RuntimeError(
                f"Cluster relationship ON could not find Material_v8 "
                f"'{material_name}' in {target.name}."
            )
        material = matches[0]
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

        marker = speedtree.parse_atlas_leaf_spm_user_data(
            material.findtext("UserData")
        )
        predecessors = _same_source_adoption_predecessors(
            speedtree,
            props,
            target,
            material,
            material_name,
            material_id,
            marker,
            blend_path,
        )
        if not predecessors:
            legacy_targets.append(target)
            continue

        snapshots = {
            snapshot
            for previous in predecessors
            for snapshot in [
                _snapshot_key(
                    speedtree,
                    previous,
                    material_name,
                    material_id,
                )
            ]
            if snapshot is not None
        }
        if len(snapshots) != 1:
            raise RuntimeError(
                f"Cannot extend Cluster relationship to {target.name}: "
                "same-provider adoption receipts disagree."
            )

        request = {
            "source_material_names": [material_name],
            "source_material_ids": [material_id],
            "adopt_source_material": True,
            "generator_variant_policy": generator_variant_policy,
            "source_binding_repairs": copy.deepcopy(
                (existing_request or {}).get("source_binding_repairs") or []
            ),
        }
        if (
            (existing_request or {}).get("generator_delivery_scope_intent")
            is not None
        ):
            request["generator_delivery_scope_intent"] = copy.deepcopy(
                existing_request["generator_delivery_scope_intent"]
            )
        raw_mapping[str(target)] = request

        row = {
            "target_spm": str(target),
            "material_name": material_name,
            "material_id": material_id,
            "source_mesh_ids": mesh_ids,
            "reused_existing_scope": True,
            "ownership_authority": "current_transaction_same_source",
            "predecessor_scopes": sorted(
                {
                    speedtree.spm_export_scope(previous)
                    for previous in predecessors
                }
            ),
        }
        if existing_request is None:
            added.append(row)
        elif existing_request.get("source_material_ids") != [material_id]:
            reconciled.append(row)
        else:
            preserved.append(str(target))

    props.speedtree_source_materials_json = json.dumps(
        raw_mapping,
        ensure_ascii=False,
        sort_keys=True,
    )

    if legacy_targets:
        legacy = speedtree.extend_source_material_adoptions_for_targets(
            props,
            legacy_targets,
            blend_path=blend_path,
        )
        added.extend(legacy.get("added") or [])
        reconciled.extend(legacy.get("reconciled") or [])
        preserved.extend(legacy.get("preserved") or [])

    return {
        "material_name": material_name,
        "generator_variant_policy": generator_variant_policy,
        "authority_policy": "current_transaction_same_source_v1",
        "added": added,
        "reconciled": reconciled,
        "preserved": preserved,
    }
