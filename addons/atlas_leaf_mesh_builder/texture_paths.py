import re
from pathlib import Path


TEXTURE_EXTENSIONS = {".bmp", ".exr", ".jpeg", ".jpg", ".png", ".tga", ".tif", ".tiff"}
ROLE_TOKENS = {
    "albedo": ("albedo", "base_color", "basecolor", "diffuse", "color"),
    "alpha": ("opacity", "alpha", "cutout", "mask"),
    "height": ("height", "displacement", "disp"),
    "normal": ("normal", "norm", "nrm"),
    "gloss": ("gloss", "glossiness", "smoothness"),
    "roughness": ("roughness", "rough"),
    "ao": ("ambient_occlusion", "ambientocclusion", "ao", "occlusion"),
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
