import json
import re
from pathlib import Path


TEXTURE_EXTENSIONS = {".bmp", ".exr", ".jpeg", ".jpg", ".png", ".tga", ".tif", ".tiff"}
CANONICAL_OUTPUT_MANIFEST = "pcg_st9_canonical_outputs.json"
CANONICAL_OUTPUT_KIND = "pcg_st9_canonical_output_manifest"
CANONICAL_OUTPUT_SCHEMA_VERSION = 1
CANONICAL_LEAF_ROLES = (
    "color",
    "normal",
    "extra",
    "height",
    "opacity",
    "subsurface",
)
CANONICAL_OPTIONAL_ROLES = ("ao",)
CANONICAL_TEXTURE_STATUS = "canonical_pcg_output"
SOURCE_FALLBACK_STATUS = "source_fallback_needs_pcg_generation"
SOURCE_FALLBACK_REMEDIATION = (
    "PCG ST9 Texture에서 Substance 그래프 생성 및 export 실행"
)
PRODUCTION_PATH_BLOCKLIST = {
    ".sk_batch_isolated_bark",
    "_sk_batch_isolated_bark",
    ".sk_batch_temp",
    "_sk_batch_temp",
    ".sk_batch_cache",
    "_sk_batch_cache",
    ".speedtree_export_cache",
    "_speedtree_export_cache",
    ".cache",
    "_cache",
    "cache",
}
ROLE_TOKENS = {
    "albedo": ("albedo", "base_color", "basecolor", "diffuse", "color"),
    "alpha": ("opacity", "alpha", "cutout", "mask"),
    "height": ("height", "displacement", "disp"),
    "normal": ("normal", "norm", "nrm"),
    "gloss": ("gloss", "glossiness", "smoothness"),
    "roughness": ("roughness", "rough"),
    "ao": ("ambient_occlusion", "ambientocclusion", "ao", "occlusion"),
    "subsurface_amount": (
        "subsurface_amount",
        "subsurfaceamount",
        "sss_amount",
    ),
    "translucency": (
        "translucency",
        "translucent",
        "transmission",
        "subsurface",
        "subsurface_color",
        "subsurfacecolor",
        "sss",
        "transqulin",
    ),
}


def normalized_stem(path):
    text = Path(path).stem.lower()
    text = re.sub(r"[-.\s]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def role_pattern(token):
    normalized = token.lower().replace(" ", "_")
    return re.compile(rf"(?:^|_){re.escape(normalized)}(?:_|$)")


def stem_has_role(stem, tokens):
    return any(role_pattern(token).search(stem) for token in tokens)


def strip_role(stem, tokens):
    result = stem
    for token in sorted(tokens, key=len, reverse=True):
        pattern = role_pattern(token)
        while pattern.search(result):
            result = pattern.sub("_", result)
            result = re.sub(r"_+", "_", result).strip("_")
    return result


def related_texture_bases(albedo_base, candidate_base):
    if not albedo_base or not candidate_base:
        return 0
    if candidate_base == albedo_base:
        return 100
    if candidate_base.startswith(albedo_base + "_") or albedo_base.startswith(candidate_base + "_"):
        return 40
    return 0


def atlas_texture_paths(albedo_path):
    albedo = Path(albedo_path)
    paths = {"albedo": albedo}
    texture_dir = albedo.parent
    if not texture_dir.is_dir():
        return paths

    albedo_stem = normalized_stem(albedo)
    albedo_base = strip_role(albedo_stem, ROLE_TOKENS["albedo"])
    candidates = sorted(
        (
            path
            for path in texture_dir.iterdir()
            if path.is_file() and path.suffix.lower() in TEXTURE_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    )

    for role, tokens in ROLE_TOKENS.items():
        if role == "albedo":
            continue

        best = None
        for candidate in candidates:
            if candidate == albedo:
                continue
            candidate_stem = normalized_stem(candidate)
            if not stem_has_role(candidate_stem, tokens):
                continue

            candidate_base = strip_role(candidate_stem, tokens)
            relationship_score = related_texture_bases(albedo_base, candidate_base)
            if relationship_score == 0:
                continue

            score = relationship_score
            if candidate.suffix.lower() == albedo.suffix.lower():
                score += 10
            for priority, token in enumerate(tokens):
                if role_pattern(token).search(candidate_stem):
                    score += len(tokens) - priority
                    break
            if best is None or score > best[0]:
                best = (score, candidate)

        if best is not None:
            paths[role] = best[1]
    return paths


def matching_alpha_path(albedo_path):
    return atlas_texture_paths(albedo_path).get("alpha")


def _resolved_path(path):
    return Path(path).expanduser().resolve()


def _path_is_relative_to(path, parent):
    try:
        _resolved_path(path).relative_to(_resolved_path(parent))
        return True
    except (OSError, ValueError):
        return False


def _blocked_production_path(path):
    return next(
        (
            part
            for part in _resolved_path(path).parts
            if part.casefold() in PRODUCTION_PATH_BLOCKLIST
        ),
        None,
    )


def canonical_output_manifest_candidates(target_spm):
    """Return nearest asset-local PCG/SBS output manifests for one SPM."""
    target = _resolved_path(target_spm)
    candidates = []
    seen = set()
    for asset_root in (target.parent, *target.parents):
        for folder_name in ("texture", "textures"):
            candidate = asset_root / folder_name / CANONICAL_OUTPUT_MANIFEST
            key = str(candidate).casefold()
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file():
                candidates.append(candidate)
    return candidates


def expected_canonical_manifest_paths(target_spm):
    """Return the two closest conventional locations for diagnostics."""
    target = _resolved_path(target_spm)
    roots = [target.parent]
    if target.parent.parent != target.parent:
        roots.append(target.parent.parent)
    return [
        root / folder_name / CANONICAL_OUTPUT_MANIFEST
        for root in roots
        for folder_name in ("texture", "textures")
    ]


def canonical_texture_base_for_material(material_name):
    """Apply the common material/output naming contract without asset rules."""
    name = str(material_name or "").strip()
    if not name:
        raise RuntimeError(
            "Cannot report expected canonical T_* paths for an unnamed material."
        )
    if name[:2].casefold() == "m_":
        return "T_" + name[2:]
    return "T_" + name


def provisional_canonical_texture_root(target_spm):
    """Return the conventional asset texture root before a manifest exists."""
    target = _resolved_path(target_spm)
    asset_root = (
        target.parent.parent
        if target.parent.name.casefold() == "cluster"
        else target.parent
    )
    texture = asset_root / "texture"
    textures = asset_root / "textures"
    if textures.is_dir() and not texture.is_dir():
        return textures
    return texture


def expected_canonical_role_paths(target_spm, material_name):
    texture_root = provisional_canonical_texture_root(target_spm)
    texture_base = canonical_texture_base_for_material(material_name)
    return {
        role: _expected_role_path(texture_root, texture_base, role)
        for role in CANONICAL_LEAF_ROLES
    }


def validate_source_texture_fallback(source_paths, material_name):
    """Validate direct original paths for the provisional production state."""
    normalized = {}
    diagnostics = []
    for role, value in sorted((source_paths or {}).items()):
        path = _resolved_path(value)
        blocked = _blocked_production_path(path)
        generated_png = (
            path.suffix.casefold() == ".png"
            and (
                path.name.casefold().startswith("t_")
                or any(
                    token in path.stem.casefold()
                    for token in (
                        "_ao_from_height",
                        "_generated",
                        "_export",
                    )
                )
            )
        )
        if blocked:
            diagnostics.append(
                f"role={role}, path={path}, blocked_component={blocked}"
            )
        elif "_pcgtex_generated" in {
            part.casefold() for part in path.parts
        }:
            diagnostics.append(
                f"role={role}, path={path}, generated_output_not_source=true"
            )
        elif generated_png:
            diagnostics.append(
                f"role={role}, path={path}, export_generated_png=true"
            )
        elif not path.is_file():
            diagnostics.append(f"role={role}, path={path}, missing=true")
        else:
            normalized[str(role)] = path
    if "albedo" not in normalized:
        diagnostics.append(
            "role=albedo, original Atlas mesh-build source is required"
        )
    if diagnostics:
        raise RuntimeError(
            "Original source fallback is unavailable or unsafe for "
            f"material={material_name or '<unnamed>'}: "
            + " | ".join(diagnostics)
            + f". {SOURCE_FALLBACK_REMEDIATION}."
        )
    return normalized


def _manifest_path_value(value, base, label):
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"Canonical texture manifest is missing {label}.")
    path = Path(text)
    if not path.is_absolute():
        path = Path(base) / path
    return _resolved_path(path)


def _material_id(value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _target_path(asset_root, value):
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = asset_root / path
    return _resolved_path(path)


def _role_filename_matches(texture_base, role, path):
    stem = path.stem.casefold()
    expected = f"{texture_base}_{role}".casefold()
    if role == "ao":
        return stem == expected or stem.startswith(expected + "_")
    return stem == expected


def _expected_role_path(texture_root, texture_base, role):
    if role == "ao":
        return (
            texture_root
            / "_pcgtex_generated"
            / f"{texture_base}_ao_from_height.png"
        )
    return texture_root / f"{texture_base}_{role}.tga"


def _canonical_file_path(manifest_path, texture_root, texture_base, role, value):
    text = str(value or "").strip()
    if not text:
        return None
    authored = Path(text)
    if authored.is_absolute():
        raise RuntimeError(
            "Canonical texture manifest files must be manifest-relative: "
            f"role={role}, path={authored}"
        )
    path = _resolved_path(manifest_path.parent / authored)
    generated_root = texture_root / "_pcgtex_generated"
    if path.parent != texture_root and not _path_is_relative_to(path, generated_root):
        raise RuntimeError(
            "Canonical texture output is outside the asset texture folder or "
            f"its _pcgtex_generated folder: role={role}, path={path}"
        )
    if not path.name.casefold().startswith("t_"):
        raise RuntimeError(
            f"Canonical texture output must use a T_* filename: role={role}, path={path}"
        )
    if not _role_filename_matches(texture_base, role, path):
        expected = _expected_role_path(texture_root, texture_base, role)
        raise RuntimeError(
            "Canonical texture role filename does not match its manifest role: "
            f"role={role}, path={path}, expected={expected}"
        )
    return path


def load_canonical_output_manifest(
    manifest_path,
    *,
    output_predicate=None,
):
    """Load and structurally validate one PCG ST9 canonical output manifest."""
    manifest_path = _resolved_path(manifest_path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Canonical texture manifest is unreadable: {manifest_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Canonical texture manifest must contain one JSON object: {manifest_path}"
        )
    if payload.get("kind") != CANONICAL_OUTPUT_KIND:
        raise RuntimeError(
            "Canonical texture manifest kind is invalid: "
            f"{payload.get('kind')!r}; expected {CANONICAL_OUTPUT_KIND!r}"
        )
    if payload.get("schema_version") != CANONICAL_OUTPUT_SCHEMA_VERSION:
        raise RuntimeError(
            "Canonical texture manifest schema_version is invalid: "
            f"{payload.get('schema_version')!r}; expected "
            f"{CANONICAL_OUTPUT_SCHEMA_VERSION}"
        )

    asset_root = _manifest_path_value(
        payload.get("asset_root"),
        manifest_path.parent,
        "asset_root",
    )
    texture_root = _manifest_path_value(
        payload.get("texture_root"),
        asset_root,
        "texture_root",
    )
    if texture_root.name.casefold() not in {"texture", "textures"}:
        raise RuntimeError(
            "Canonical texture_root must be the asset texture/textures folder: "
            f"{texture_root}"
        )
    if texture_root.parent != asset_root:
        raise RuntimeError(
            "Canonical texture_root must be directly below asset_root: "
            f"asset_root={asset_root}, texture_root={texture_root}"
        )
    if manifest_path.parent != texture_root:
        raise RuntimeError(
            "Canonical texture manifest must be stored in its declared texture_root: "
            f"manifest={manifest_path}, texture_root={texture_root}"
        )
    blocked = _blocked_production_path(asset_root)
    if blocked:
        raise RuntimeError(
            "Canonical production asset_root cannot be an isolated/temp/cache path: "
            f"component={blocked}, asset_root={asset_root}"
        )

    raw_outputs = payload.get("outputs")
    if not isinstance(raw_outputs, list) or not raw_outputs:
        raise RuntimeError(
            f"Canonical texture manifest has no outputs: {manifest_path}"
        )
    if output_predicate is not None:
        raw_outputs = [
            raw_output
            for raw_output in raw_outputs
            if (
                isinstance(raw_output, dict)
                and output_predicate(raw_output, asset_root)
            )
        ]
    outputs = []
    for index, raw_output in enumerate(raw_outputs):
        if not isinstance(raw_output, dict):
            raise RuntimeError(
                f"Canonical texture output #{index + 1} must be an object."
            )
        texture_base = str(raw_output.get("texture_base") or "").strip()
        if not texture_base.casefold().startswith("t_"):
            raise RuntimeError(
                "Canonical texture_base must start with T_: "
                f"output #{index + 1}, value={texture_base!r}"
            )
        required_roles = raw_output.get("required_roles")
        if not isinstance(required_roles, list):
            required_roles = []
        required_roles = tuple(
            str(role).strip().casefold()
            for role in required_roles
            if str(role).strip()
        )
        missing_declared_roles = [
            role for role in CANONICAL_LEAF_ROLES if role not in required_roles
        ]
        files = raw_output.get("files")
        if not isinstance(files, dict):
            files = {}
        raw_targets = raw_output.get("material_targets")
        material_labels = sorted(
            {
                str(target.get("material_name") or "").strip()
                for target in raw_targets or []
                if isinstance(target, dict)
                and str(target.get("material_name") or "").strip()
            }
        )
        normalized_files = {}
        diagnostics = []
        for role in CANONICAL_LEAF_ROLES:
            expected = _expected_role_path(texture_root, texture_base, role)
            if role in missing_declared_roles:
                diagnostics.append(
                    f"role={role}, expected={expected} "
                    "(required_roles declaration missing)"
                )
                continue
            try:
                path = _canonical_file_path(
                    manifest_path,
                    texture_root,
                    texture_base,
                    role,
                    files.get(role),
                )
            except RuntimeError as exc:
                diagnostics.append(f"role={role}, expected={expected} ({exc})")
                continue
            if path is None or not path.is_file():
                diagnostics.append(f"role={role}, expected={path or expected}")
                continue
            normalized_files[role] = path
        for role in CANONICAL_OPTIONAL_ROLES:
            if not files.get(role):
                continue
            try:
                path = _canonical_file_path(
                    manifest_path,
                    texture_root,
                    texture_base,
                    role,
                    files.get(role),
                )
            except RuntimeError as exc:
                diagnostics.append(
                    f"role={role}, expected="
                    f"{_expected_role_path(texture_root, texture_base, role)} "
                    f"({exc})"
                )
                continue
            if not path.is_file():
                diagnostics.append(f"role={role}, expected={path}")
                continue
            normalized_files[role] = path
        if diagnostics:
            raise RuntimeError(
                "Canonical T_* outputs are incomplete for "
                f"material={','.join(material_labels) or '<undeclared>'}, "
                f"texture_base={texture_base!r}: "
                + " | ".join(diagnostics)
                + ". PCG ST9 Texture에서 생성하세요."
            )

        if not isinstance(raw_targets, list) or not raw_targets:
            raise RuntimeError(
                "Canonical texture output has no material_targets: "
                f"texture_base={texture_base!r}"
            )
        targets = []
        for target_index, raw_target in enumerate(raw_targets):
            if not isinstance(raw_target, dict):
                raise RuntimeError(
                    "Canonical texture material target must be an object: "
                    f"texture_base={texture_base!r}, target #{target_index + 1}"
                )
            spm = _target_path(asset_root, raw_target.get("spm"))
            material_name = str(
                raw_target.get("material_name") or ""
            ).strip()
            material_id = _material_id(raw_target.get("material_id"))
            if spm is None or material_id is None or not material_name:
                raise RuntimeError(
                    "Canonical texture material target is incomplete: "
                    f"texture_base={texture_base!r}, target #{target_index + 1}; "
                    "spm, positive material_id, and material_name are required."
                )
            if not _path_is_relative_to(spm, asset_root):
                raise RuntimeError(
                    "Canonical texture target SPM is outside asset_root: "
                    f"target={spm}, asset_root={asset_root}"
                )
            targets.append(
                {
                    "spm": spm,
                    "material_id": material_id,
                    "material_name": material_name,
                }
            )
        producer = raw_output.get("producer")
        if (
            not isinstance(producer, dict)
            or not str(producer.get("tool") or "").strip()
            or not str(producer.get("source") or "").strip()
        ):
            raise RuntimeError(
                "Canonical texture output producer.tool/source are required: "
                f"texture_base={texture_base!r}"
            )
        outputs.append(
            {
                "texture_base": texture_base,
                "required_roles": required_roles,
                "files": normalized_files,
                "material_targets": targets,
                "producer": dict(producer),
            }
        )
    return {
        "path": manifest_path,
        "asset_root": asset_root,
        "texture_root": texture_root,
        "outputs": outputs,
        "payload": payload,
    }


def resolve_canonical_texture_output(
    target_spm,
    material_name,
    material_id=None,
    manifest_path=None,
    *,
    allow_absent_mapping=False,
):
    """Resolve one production SPM material to one verified T_* role set."""
    target = _resolved_path(target_spm)
    material_name = str(material_name or "").strip()
    material_id = _material_id(material_id)
    blocked = _blocked_production_path(target)
    if blocked:
        raise RuntimeError(
            "Production SpeedTree SPM cannot be an isolated/temp/cache target: "
            f"component={blocked}, target={target}"
        )
    candidates = (
        [_resolved_path(manifest_path)]
        if manifest_path
        else canonical_output_manifest_candidates(target)
    )
    if not candidates:
        expected = ", ".join(
            str(path) for path in expected_canonical_manifest_paths(target)
        )
        raise RuntimeError(
            "Canonical T_* output manifest is missing for "
            f"material={material_name or '<unnamed>'}, "
            f"material_id={material_id or '<unknown>'}. "
            f"Expected one of: {expected}. PCG ST9 Texture에서 생성하세요."
        )

    matches = []
    manifest_errors = []

    def raw_output_matches_request(raw_output, asset_root):
        raw_targets = raw_output.get("material_targets")
        if not isinstance(raw_targets, list):
            return False
        for raw_target in raw_targets:
            if not isinstance(raw_target, dict):
                continue
            raw_spm = _target_path(asset_root, raw_target.get("spm"))
            if raw_spm != target:
                continue
            raw_id = _material_id(raw_target.get("material_id"))
            raw_name = str(
                raw_target.get("material_name") or ""
            ).strip()
            if material_id is not None and raw_id == material_id:
                return True
            if (
                material_name
                and raw_name.casefold() == material_name.casefold()
            ):
                return True
        return False

    for candidate in candidates:
        try:
            manifest = load_canonical_output_manifest(
                candidate,
                output_predicate=raw_output_matches_request,
            )
        except RuntimeError as exc:
            manifest_errors.append(f"{candidate}: {exc}")
            continue
        if not _path_is_relative_to(target, manifest["asset_root"]):
            continue
        for output in manifest["outputs"]:
            for material_target in output["material_targets"]:
                if material_target["spm"] != target:
                    continue
                matches.append(
                    {
                        "manifest": manifest,
                        "output": output,
                        "target": material_target,
                    }
                )

    id_matches = [
        row
        for row in matches
        if material_id is not None
        and row["target"]["material_id"] == material_id
    ]
    if id_matches:
        matches = id_matches
    else:
        matches = [
            row
            for row in matches
            if material_name
            and row["target"]["material_name"].casefold()
            == material_name.casefold()
        ]
    unique = {}
    for row in matches:
        key = (
            str(row["manifest"]["path"]).casefold(),
            row["output"]["texture_base"].casefold(),
        )
        unique[key] = row
    matches = list(unique.values())
    if (
        not matches
        and allow_absent_mapping
        and not manifest_errors
    ):
        return None
    if len(matches) != 1:
        detail = (
            " | ".join(manifest_errors)
            if manifest_errors
            else "no exact target/material mapping"
        )
        raise RuntimeError(
            "Canonical T_* output mapping must resolve exactly once for "
            f"material={material_name or '<unnamed>'}, "
            f"material_id={material_id or '<unknown>'}, target={target}; "
            f"found={len(matches)} ({detail}). PCG ST9 Texture에서 생성하세요."
        )
    result = matches[0]
    return {
        "manifest_path": result["manifest"]["path"],
        "asset_root": result["manifest"]["asset_root"],
        "texture_root": result["manifest"]["texture_root"],
        "texture_base": result["output"]["texture_base"],
        "required_roles": result["output"]["required_roles"],
        "files": dict(result["output"]["files"]),
        "producer": dict(result["output"]["producer"]),
        "material_target": dict(result["target"]),
    }


def resolve_production_texture_contract(
    target_spm,
    material_name,
    material_id=None,
    source_paths=None,
    manifest_path=None,
):
    """Select canonical T_* first, otherwise return a provisional source state."""
    candidates = (
        [_resolved_path(manifest_path)]
        if manifest_path
        else canonical_output_manifest_candidates(target_spm)
    )
    if candidates:
        output = resolve_canonical_texture_output(
            target_spm,
            material_name,
            material_id,
            manifest_path=manifest_path,
            allow_absent_mapping=True,
        )
        if output is not None:
            return {
                "texture_contract_status": CANONICAL_TEXTURE_STATUS,
                "material": str(material_name or ""),
                "files": dict(output["files"]),
                "canonical_output": output,
                "warning": None,
                "remediation": None,
            }

    sources = validate_source_texture_fallback(
        source_paths,
        material_name,
    )
    expected = expected_canonical_role_paths(
        target_spm,
        material_name,
    )
    return {
        "texture_contract_status": SOURCE_FALLBACK_STATUS,
        "material": str(material_name or ""),
        "source_paths": dict(sources),
        "source_roles": sorted(sources),
        "expected_t_paths": expected,
        "expected_texture_base": canonical_texture_base_for_material(
            material_name
        ),
        "remediation": SOURCE_FALLBACK_REMEDIATION,
        "warning": (
            "Canonical T_* manifest/output is absent. Production SpeedTree "
            "handoff is provisionally referencing original Atlas mesh-build "
            "sources directly without a cache copy."
        ),
    }
