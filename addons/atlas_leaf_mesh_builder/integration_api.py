import json
import sys
from pathlib import Path

import bpy

from .props import (
    add_spm_target_item,
    save_spm_target_registry,
    sync_alpha_path,
    sync_spm_target_registry,
)


INTEGRATION_API_NAME = "atlas_leaf_mesh_builder.integration"
INTEGRATION_API_VERSION = 1
INTEGRATION_API_CAPABILITIES = frozenset({
    "atomic_exact_target_slice_v1",
    "generator_adoption_reconciliation_v1",
    "structured_transaction_conflict_v1",
    "unit_probe_preserve_by_default_v1",
})


def get_integration_contract():
    """Return the stable contract implemented by this installed add-on."""
    return {
        "name": INTEGRATION_API_NAME,
        "version": INTEGRATION_API_VERSION,
        "capabilities": sorted(INTEGRATION_API_CAPABILITIES),
    }


def require_integration_contract(*, minimum_version=1, capabilities=()):
    """Fail before mutation when an external caller needs a newer contract."""
    minimum_version = int(minimum_version)
    required = {str(value) for value in capabilities}
    missing = sorted(required - INTEGRATION_API_CAPABILITIES)
    if INTEGRATION_API_VERSION < minimum_version or missing:
        raise RuntimeError(
            "Atlas integration API contract is incompatible: "
            f"installed_version={INTEGRATION_API_VERSION}, "
            f"minimum_version={minimum_version}, "
            f"missing_capabilities={missing}"
        )
    return get_integration_contract()


def _load_cluster_card_contract_reader():
    try:
        from cluster_card_pipeline import read_uv_template_contract
        return read_uv_template_contract
    except ImportError as first_error:
        repository_parent = Path(__file__).resolve().parents[3]
        tools_root = repository_parent / "speedtree-batch-tools"
        if tools_root.is_dir() and str(tools_root) not in sys.path:
            sys.path.insert(0, str(tools_root))
        try:
            from cluster_card_pipeline import read_uv_template_contract
            return read_uv_template_contract
        except ImportError as exc:
            raise RuntimeError(
                "cluster_card_pipeline is required for the camera SPM UV contract. "
                "Install speedtree-batch-tools or keep it beside the Atlas repository."
            ) from exc


def _load_cluster_card_capture_refresh():
    _load_cluster_card_contract_reader()
    try:
        from cluster_card_pipeline import ensure_camera_capture_refresh
    except ImportError as exc:
        raise RuntimeError(
            "Installed speedtree-batch-tools does not provide the camera capture "
            "request/finalize integration."
        ) from exc
    return ensure_camera_capture_refresh


def read_external_plan_uv_contract(
    camera_spm,
    tree_spm,
    material_name=None,
    material_id=None,
    *,
    camera_name="Dropped XY plane camera 2",
    output_prefix=None,
):
    """Read the public, non-mutating camera SPM UV-template contract."""
    reader = _load_cluster_card_contract_reader()
    return reader(
        camera_spm,
        tree_spm,
        material_name=material_name,
        material_id=material_id,
        camera_name=camera_name,
        output_prefix=output_prefix,
    )


def ensure_external_camera_capture_refresh(
    contract,
    camera_spm,
    manifest_path,
):
    """Require a receipt-backed SpeedTree Camera Export for the UV contract."""
    refresh = _load_cluster_card_capture_refresh()
    return refresh(
        contract,
        camera_spm,
        manifest_path,
    )


def validate_external_camera_capture_receipt(
    receipt_path,
    contract,
    camera_spm,
):
    """Read-only validation for a finalized SpeedTree Camera Export receipt."""
    _load_cluster_card_contract_reader()
    try:
        from cluster_card_pipeline import validate_camera_capture_receipt
    except ImportError as exc:
        raise RuntimeError(
            "Installed speedtree-batch-tools does not provide capture receipt validation."
        ) from exc
    return validate_camera_capture_receipt(
        receipt_path,
        contract,
        camera_spm,
    )


def ensure_external_plan_preview_material(material_name, albedo_path, opacity_path):
    """Build the real Color + Opacity viewport material used by external plans."""
    material_name = str(material_name or "").strip()
    albedo = Path(bpy.path.abspath(str(albedo_path or ""))).expanduser().absolute()
    opacity = Path(bpy.path.abspath(str(opacity_path or ""))).expanduser().absolute()
    if not material_name:
        raise ValueError("External plan preview material name cannot be empty.")
    if not albedo.is_file():
        raise FileNotFoundError(f"External plan Color map does not exist: {albedo}")
    if not opacity.is_file():
        raise FileNotFoundError(f"External plan Opacity map does not exist: {opacity}")
    from .materials import make_speedtree_material

    material = make_speedtree_material(material_name, str(albedo), str(opacity))
    return {
        "material_name": material.name,
        "albedo_path": str(albedo),
        "opacity_path": str(opacity),
        "uses_opacity": True,
    }


def configure_external_plan_target(
    props,
    collection_name,
    generated_material_name,
    source_material_name,
    albedo_path="",
    target_spm="",
    source_material_id=None,
    adopt_source_material=False,
    only_target=False,
    mesh_geometry_scale=1.0,
    mesh_asset_scale=1.0,
    generator_variant_policy=None,
    unit_probe_contract=None,
    connect_generators=True,
    generator_delivery_scope_intent=None,
    clear_unit_probe_contract=False,
):
    """Configure Atlas' existing public scene contract for an external plan collection.

    This function only prepares Atlas settings and its per-blend target registry.
    It deliberately does not call the SPM build operator.
    """
    collection_name = str(collection_name or "").strip()
    generated_material_name = str(generated_material_name or "").strip()
    source_material_name = str(source_material_name or "").strip()
    if not collection_name:
        raise ValueError("Atlas collection name cannot be empty.")
    if not generated_material_name:
        raise ValueError("Generated Atlas material name cannot be empty.")
    if not source_material_name:
        raise ValueError("Source SpeedTree material name cannot be empty.")
    adopt_source_material = bool(adopt_source_material)
    connect_generators = bool(connect_generators)
    if (
        generator_delivery_scope_intent is not None
        and not isinstance(generator_delivery_scope_intent, dict)
    ):
        raise ValueError("Generator delivery scope intent must be a JSON object.")
    if generator_delivery_scope_intent is not None and not connect_generators:
        raise ValueError(
            "Generator delivery scope intent requires Generator connection."
        )
    if (
        connect_generators
        and generated_material_name == source_material_name
        and not adopt_source_material
    ):
        raise ValueError(
            "Generated Atlas material must differ from the unmanaged source material "
            "unless explicit in-place adoption is enabled."
        )
    if (
        connect_generators
        and adopt_source_material
        and generated_material_name != source_material_name
    ):
        raise ValueError(
            "In-place adoption requires generated and source material names to match."
        )

    props.collection_name = collection_name
    props.speedtree_atlas_asset_name = generated_material_name
    props.speedtree_anchor_export_mode = "OFF"
    props.speedtree_create_missing_spm = False
    from .unit_contract import resolve_unit_probe_contract_update

    verified_unit_contract = resolve_unit_probe_contract_update(
        getattr(props, "speedtree_unit_probe_contract_json", ""),
        unit_probe_contract,
        clear=bool(clear_unit_probe_contract),
    )
    if verified_unit_contract is not None:
        mesh_geometry_scale = verified_unit_contract["mesh_geometry_scale"]
        mesh_asset_scale = verified_unit_contract["mesh_asset_scale"]
        props.speedtree_unit_probe_contract_json = json.dumps(
            verified_unit_contract["contract"],
            ensure_ascii=False,
            sort_keys=True,
        )
    else:
        props.speedtree_unit_probe_contract_json = ""
    mesh_geometry_scale = float(mesh_geometry_scale)
    if mesh_geometry_scale <= 0.0:
        raise ValueError("Atlas mesh geometry scale must be greater than zero.")
    props.speedtree_mesh_scale = mesh_geometry_scale
    mesh_asset_scale = float(mesh_asset_scale)
    if mesh_asset_scale <= 0.0:
        raise ValueError("SpeedTree mesh asset scale must be greater than zero.")
    props.speedtree_mesh_asset_scale = mesh_asset_scale
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        raise ValueError(f"Atlas plan collection does not exist: {collection_name}")
    from .speedtree import (
        ATLAS_LEAF_COLLECTION_SCOPE_KEY,
        blend_paths_equal,
        ensure_collection_export_scope_id,
        normalize_generator_variant_policy,
        read_json_file,
        target_manifest_path,
    )
    generator_variant_policy = normalize_generator_variant_policy(
        generator_variant_policy
    )

    export_scope_id = None
    if albedo_path:
        props.albedo_path = bpy.path.abspath(albedo_path)
        sync_alpha_path(props)

    target = None
    if target_spm:
        target = Path(bpy.path.abspath(target_spm)).expanduser().absolute()
        if target.suffix.casefold() != ".spm":
            raise ValueError("Atlas target must end with .spm")
        if not target.is_file():
            raise FileNotFoundError(f"Atlas target SPM does not exist: {target}")
        if adopt_source_material:
            previous = read_json_file(target_manifest_path(target), {})
            previous_scope = str(previous.get("export_scope_id") or "")
            same_owner = bool(
                previous_scope
                and previous.get("blend_file")
                and blend_paths_equal(previous["blend_file"], bpy.data.filepath)
                and str(previous.get("source_collection") or "") == collection_name
            )
            if same_owner:
                current_scope = str(
                    collection.get(ATLAS_LEAF_COLLECTION_SCOPE_KEY) or ""
                )
                if current_scope and current_scope != previous_scope:
                    raise ValueError(
                        "Atlas plan collection scope conflicts with the existing target manifest."
                    )
                collection[ATLAS_LEAF_COLLECTION_SCOPE_KEY] = previous_scope
                export_scope_id = previous_scope
        if only_target:
            props.speedtree_spm_items.clear()
        else:
            sync_spm_target_registry(props, initialize_missing=False)
        add_spm_target_item(props, str(target))
        props.speedtree_spm_path = str(target)

        try:
            mapping = json.loads(props.speedtree_source_materials_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Generator Source Mapping is not valid JSON: {exc}") from exc
        if not isinstance(mapping, dict):
            raise ValueError("Generator Source Mapping must be a JSON object.")
        if connect_generators:
            request = {
                "source_material_names": [source_material_name],
                "generator_variant_policy": generator_variant_policy,
            }
            if source_material_id is not None:
                request["source_material_ids"] = [int(source_material_id)]
            if adopt_source_material:
                request["adopt_source_material"] = True
            if generator_delivery_scope_intent is not None:
                request["generator_delivery_scope_intent"] = (
                    generator_delivery_scope_intent
                )
            mapping[str(target)] = request
        else:
            # Configuration is authoritative for this exact target.  Merely
            # skipping a new mapping leaves a previous connection request
            # active and makes an asset-only external refresh unexpectedly
            # mutate (or reject) Generator slots.
            for mapped_target in list(mapping):
                if blend_paths_equal(mapped_target, target):
                    del mapping[mapped_target]
        props.speedtree_source_materials_json = json.dumps(mapping, ensure_ascii=False)
        save_spm_target_registry(props)

    if export_scope_id is None:
        export_scope_id = ensure_collection_export_scope_id(collection)

    return {
        "collection_name": props.collection_name,
        "albedo_path": bpy.path.abspath(props.albedo_path),
        "alpha_path": bpy.path.abspath(props.alpha_path),
        "generated_material_name": props.speedtree_atlas_asset_name,
        "source_material_name": source_material_name,
        "source_material_id": int(source_material_id) if source_material_id is not None else None,
        "target_spm": str(target) if target is not None else None,
        "target_count": len(props.speedtree_spm_items),
        "mesh_geometry_scale": float(props.speedtree_mesh_scale),
        "mesh_asset_scale": float(props.speedtree_mesh_asset_scale),
        "unit_probe_contract_sha256": (
            verified_unit_contract["contract_sha256"]
            if verified_unit_contract is not None
            else None
        ),
        "unit_scale_location": (
            verified_unit_contract["scale_location"]
            if verified_unit_contract is not None
            else None
        ),
        "adopt_source_material": adopt_source_material,
        "connect_generators": connect_generators,
        "unit_probe_update": (
            "cleared"
            if clear_unit_probe_contract
            else "replaced"
            if unit_probe_contract is not None
            else "preserved"
            if verified_unit_contract is not None
            else "absent"
        ),
        "generator_variant_policy": generator_variant_policy,
        "generator_delivery_scope_intent": (
            generator_delivery_scope_intent
        ),
        "export_scope_id": export_scope_id,
        "build_operator": "atlas_leaf.build_speedtree_spm",
    }


def execute_external_target_transaction(
    props,
    target_spms,
    *,
    adoption_targets=(),
    adoption_blend_path=None,
    preserve_explicit_material_name=False,
):
    """Publish one exact external target slice through Atlas' stable API.

    External tools must not import the exporter implementation or temporarily
    rewrite the Blender target collection themselves.  This entry point owns
    target narrowing, same-name adoption reconciliation and the atomic fleet
    transaction, then restores the scene's persistent target view.
    """
    contract = require_integration_contract(
        minimum_version=1,
        capabilities=(
            "atomic_exact_target_slice_v1",
            "structured_transaction_conflict_v1",
        ),
    )
    targets = [
        Path(bpy.path.abspath(str(value))).expanduser().absolute()
        for value in target_spms
    ]
    if not targets:
        raise ValueError("Atlas external transaction requires at least one target SPM.")
    keys = [str(path).casefold() for path in targets]
    if len(keys) != len(set(keys)):
        raise ValueError("Atlas external transaction contains duplicate target SPMs.")
    for target in targets:
        if target.suffix.casefold() != ".spm":
            raise ValueError(f"Atlas external target must end with .spm: {target}")
        if not target.is_file():
            raise FileNotFoundError(f"Atlas external target does not exist: {target}")

    from .speedtree import (
        export_or_update_speedtree_spm_targets,
        extend_source_material_adoptions_for_targets,
        normalized_target_key,
    )

    requested_keys = {normalized_target_key(path) for path in targets}
    adoption_paths = [
        Path(bpy.path.abspath(str(value))).expanduser().absolute()
        for value in adoption_targets
    ]
    if not {
        normalized_target_key(path) for path in adoption_paths
    }.issubset(requested_keys):
        raise ValueError(
            "Atlas adoption targets must be part of the exact transaction slice."
        )
    if adoption_paths:
        mapping_update = extend_source_material_adoptions_for_targets(
            props,
            adoption_paths,
            blend_path=adoption_blend_path,
        )
    else:
        mapping_update = {
            "material_name": str(
                getattr(props, "speedtree_atlas_asset_name", "") or ""
            ),
            "added": [],
            "reconciled": [],
            "preserved": [],
        }

    previous_targets = [
        str(item.path)
        for item in props.speedtree_spm_items
        if str(item.path or "").strip()
    ]
    props.speedtree_spm_items.clear()
    for target in targets:
        add_spm_target_item(props, str(target))
    try:
        results = export_or_update_speedtree_spm_targets(
            props,
            preserve_explicit_material_name=preserve_explicit_material_name,
        )
    finally:
        props.speedtree_spm_items.clear()
        for target in previous_targets:
            add_spm_target_item(props, target)

    completed = {
        normalized_target_key(result.get("spm_path"))
        for result in results
    }
    if completed != requested_keys:
        missing = sorted(requested_keys - completed)
        unexpected = sorted(completed - requested_keys)
        raise RuntimeError(
            "Atlas external transaction returned a different target slice: "
            f"missing={missing}, unexpected={unexpected}"
        )
    return {
        "integration_contract": contract,
        "transaction": {
            "status": "committed",
            "target_count": len(targets),
            "targets": [str(path) for path in targets],
        },
        "mapping_update": mapping_update,
        "results": results,
    }
