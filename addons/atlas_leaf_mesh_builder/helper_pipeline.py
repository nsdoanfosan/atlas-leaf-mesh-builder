from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skimage import measure, morphology
import triangle as tr


DEFAULT_FRONTS = [1, 2, 3, 4, 10, 6, 7, 8, 9, 11, 12, 13]

QUALITY_PRESETS = {
    "FAST": {"q": 12, "area_factor": 0.24},
    "BALANCED": {"q": 18, "area_factor": 0.42},
    "HIGH": {"q": 22, "area_factor": 0.95},
    "SPEEDTREE_LOW": {
        "q": 6,
        "area_factor": 0.04,
        "epsilon_min": 2.4,
        "epsilon_max": 6.0,
        "epsilon_scale": 0.0024,
        "near_duplicate": 1.5,
        "collinear": 0.30,
    },
}


def parse_pairs(text: str | None) -> list[dict]:
    if not text:
        return []
    data = json.loads(text)
    pairs = []
    for item in data:
        if isinstance(item, dict):
            pairs.append({"front": int(item["front"])})
        else:
            pairs.append({"front": int(item[0])})
    return pairs


def load_font(size):
    for name in ("arial.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def draw_text_with_box(draw, xy, text, font, fill=(255, 255, 255), box_fill=(0, 0, 0)):
    x, y = xy
    bbox = draw.textbbox((x, y), text, font=font, stroke_width=0)
    pad = 6
    draw.rectangle((bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad), fill=box_fill)
    draw.text((x, y), text, fill=fill, font=font)


def signed_area(points):
    total = 0.0
    for i, (x0, y0) in enumerate(points):
        x1, y1 = points[(i + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return total * 0.5


def remove_near_duplicates(points, min_dist=0.35):
    cleaned = []
    for point in points:
        if not cleaned or np.linalg.norm(np.array(point) - np.array(cleaned[-1])) >= min_dist:
            cleaned.append(tuple(map(float, point)))
    if len(cleaned) > 1 and np.linalg.norm(np.array(cleaned[0]) - np.array(cleaned[-1])) < min_dist:
        cleaned.pop()
    return cleaned


def remove_almost_collinear(points, epsilon=0.05):
    if len(points) < 4:
        return points
    result = points[:]
    changed = True
    while changed and len(result) >= 4:
        changed = False
        keep = []
        for i, point in enumerate(result):
            prev_pt = np.array(result[i - 1])
            curr_pt = np.array(point)
            next_pt = np.array(result[(i + 1) % len(result)])
            dx1, dy1 = curr_pt - prev_pt
            dx2, dy2 = next_pt - curr_pt
            area2 = abs(dx1 * dy2 - dy1 * dx2)
            base = np.linalg.norm(next_pt - prev_pt)
            distance = area2 / base if base > 1e-8 else 0.0
            if distance >= epsilon:
                keep.append(point)
            else:
                changed = True
        result = keep
    return result


def triangle_area(a, b, c):
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) * 0.5


def triangle_min_angle(a, b, c):
    pts = [np.array(a), np.array(b), np.array(c)]
    angles = []
    for i in range(3):
        v1 = pts[(i + 1) % 3] - pts[i]
        v2 = pts[(i + 2) % 3] - pts[i]
        denom = np.linalg.norm(v1) * np.linalg.norm(v2)
        if denom <= 1e-9:
            return 0.0
        cos_value = float(np.clip(np.dot(v1, v2) / denom, -1.0, 1.0))
        angles.append(np.degrees(np.arccos(cos_value)))
    return min(angles)


class Pipeline:
    def __init__(
        self,
        albedo_path,
        alpha_path,
        pairs,
        quality,
        alpha_threshold,
        min_area,
        shell_gap,
        side_uv_inset,
        place_at_origin,
        no_shell=False,
        surface_mode="DOUBLE",
    ):
        self.albedo_path = Path(albedo_path)
        self.alpha_path = Path(alpha_path)
        self.pairs = pairs
        self.quality = QUALITY_PRESETS[quality]
        self.quality_name = quality
        self.alpha_threshold = alpha_threshold
        self.min_area = min_area
        self.shell_gap = shell_gap
        self.side_uv_inset = side_uv_inset
        self.place_at_origin = place_at_origin
        self.no_shell = bool(no_shell)
        self.surface_mode = str(surface_mode or "DOUBLE").upper()
        if self.surface_mode not in {"SINGLE", "DOUBLE"}:
            raise ValueError(f"Unknown surface mode: {surface_mode}")

        alpha = np.array(Image.open(self.alpha_path).convert("RGB"))[..., 0]
        self.height, self.width = alpha.shape
        mask = (alpha > alpha_threshold).astype(np.uint8) * 255
        self.mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
        self.num_labels, self.labels, self.stats, self.centroids = cv2.connectedComponentsWithStats(
            self.mask, connectivity=8
        )
        self.components = self._collect_components()
        if not self.pairs:
            self.pairs = [{"front": index} for index in self.components]

    def _collect_components(self):
        components = []
        for label in range(1, self.num_labels):
            area = int(self.stats[label, cv2.CC_STAT_AREA])
            if area < self.min_area:
                continue
            bbox = tuple(int(self.stats[label, j]) for j in range(4))
            components.append(
                {"cc_label": label, "area": area, "bbox": bbox, "centroid": self.centroids[label].tolist()}
            )
        components.sort(key=lambda c: (c["bbox"][1], c["bbox"][0]))
        return {index: comp for index, comp in enumerate(components, 1)}

    def component_mask(self, index):
        return ((self.labels == self.components[index]["cc_label"]).astype(np.uint8) * 255)

    def canonical_mask(self, index, size=320):
        x, y, w, h = self.components[index]["bbox"]
        crop = self.component_mask(index)[y : y + h, x : x + w]
        return cv2.resize(crop, (size, size), interpolation=cv2.INTER_NEAREST) > 127

    def resolved_pairs(self):
        rows = []
        for pair_number, pair in enumerate(self.pairs, 1):
            front_index = int(pair["front"])
            rows.append(
                {
                    "pair": pair_number,
                    "front": front_index,
                }
            )
        return rows

    def crop_component_image(self, image, index, size):
        x, y, w, h = self.components[index]["bbox"]
        pad = max(24, int(max(w, h) * 0.06))
        crop = image.crop((max(0, x - pad), max(0, y - pad), min(image.width, x + w + pad), min(image.height, y + h + pad)))
        crop.thumbnail(size, Image.Resampling.LANCZOS)
        return crop

    def write_label_previews(self, output_dir):
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        image = Image.open(self.albedo_path).convert("RGB")
        front_set = {int(pair["front"]) for pair in self.pairs}

        label_image = image.copy()
        draw = ImageDraw.Draw(label_image)
        number_font = load_font(82)
        small_font = load_font(42)
        for index, component in self.components.items():
            x, y, w, h = component["bbox"]
            if index in front_set:
                color = (0, 220, 255)
                tag = "F"
            else:
                color = (255, 120, 40)
                tag = "?"
            draw.rectangle((x, y, x + w, y + h), outline=color, width=8)
            draw_text_with_box(draw, (x + 12, y + 12), f"{index:02d}", number_font, fill=color)
            draw_text_with_box(draw, (x + 12, y + 112), tag, small_font, fill=color)

        label_image.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
        label_path = output / "atlas_leaf_island_labels.jpg"
        label_image.save(label_path, quality=94)

        pairs = self.resolved_pairs()
        tile_w = 420
        tile_h = 520
        columns = 4
        rows = int(np.ceil(len(pairs) / columns))
        sheet = Image.new("RGB", (tile_w * columns, tile_h * rows), (22, 22, 22))
        title_font = load_font(34)
        label_font = load_font(28)
        for item in pairs:
            pair_index = item["pair"] - 1
            tile_x = (pair_index % columns) * tile_w
            tile_y = (pair_index // columns) * tile_h
            tile_draw = ImageDraw.Draw(sheet)
            tile_draw.rectangle((tile_x + 8, tile_y + 8, tile_x + tile_w - 8, tile_y + tile_h - 8), outline=(90, 90, 90), width=2)
            title = f"Leaf {item['pair']:02d}  F{item['front']:02d}"
            draw_text_with_box(tile_draw, (tile_x + 22, tile_y + 20), title, title_font, fill=(255, 255, 255), box_fill=(40, 40, 40))

            front_crop = self.crop_component_image(image, item["front"], (350, 410))
            front_x = tile_x + (tile_w - front_crop.width) // 2
            crop_y = tile_y + 96
            sheet.paste(front_crop, (front_x, crop_y + (410 - front_crop.height) // 2))
            draw_text_with_box(tile_draw, (tile_x + 38, tile_y + 466), f"front {item['front']:02d}", label_font, fill=(0, 220, 255))

        pair_path = output / "atlas_leaf_front_preview.jpg"
        sheet.save(pair_path, quality=94)
        return {"island_labels": str(label_path), "pair_preview": str(pair_path), "pairs": pairs}

    def extract_subpixel_polygon(self, index):
        x, y, w, h = self.components[index]["bbox"]
        comp = self.component_mask(index)
        pad = 3
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(self.width, x + w + pad)
        y1 = min(self.height, y + h + pad)
        crop = (comp[y0:y1, x0:x1] > 127).astype(np.float32)
        contours = measure.find_contours(crop, 0.5, fully_connected="high")
        contour = max(contours, key=len)
        image_points = [(float(x0 + col), float(y0 + row)) for row, col in contour]
        image_points = remove_near_duplicates(image_points, float(self.quality.get("near_duplicate", 0.35)))
        contour_np = np.array(image_points, dtype=np.float32).reshape((-1, 1, 2))
        perimeter = cv2.arcLength(contour_np, True)
        epsilon = max(
            float(self.quality.get("epsilon_min", 0.55)),
            min(float(self.quality.get("epsilon_max", 1.35)), perimeter * float(self.quality.get("epsilon_scale", 0.00055))),
        )
        approx = cv2.approxPolyDP(contour_np, epsilon, True).reshape((-1, 2))
        simplified = remove_near_duplicates(
            [(float(px), float(py)) for px, py in approx],
            float(self.quality.get("near_duplicate", 0.6)),
        )
        simplified = remove_almost_collinear(simplified, float(self.quality.get("collinear", 0.05)))
        return simplified, len(image_points), epsilon

    def triangulate_quality(self, object_poly):
        if signed_area(object_poly) < 0:
            object_poly = list(reversed(object_poly))
        vertices = np.array(object_poly, dtype=np.float64)
        segments = np.array([(i, (i + 1) % len(object_poly)) for i in range(len(object_poly))], dtype=np.int32)
        polygon_area = abs(signed_area(object_poly))
        max_area = max(polygon_area / max(64, len(object_poly) * self.quality["area_factor"]), 0.00012)
        result = tr.triangulate({"vertices": vertices, "segments": segments}, f"pq{self.quality['q']}a{max_area:.8f}")
        out_vertices = result["vertices"]
        triangles = result["triangles"]
        out_segments = result.get("segments", segments)
        min_angles = [triangle_min_angle(out_vertices[a], out_vertices[b], out_vertices[c]) for a, b, c in triangles]
        return out_vertices, triangles, out_segments, max_area, float(min(min_angles))

    def uv_from_pixel(self, px, py):
        return px / self.width, 1.0 - py / self.height

    def inset_uv_toward(self, uv, target_uv):
        u, v = uv
        tu, tv = target_uv
        factor = float(np.clip(self.side_uv_inset, 0.0, 0.95))
        return [float(u + (tu - u) * factor), float(v + (tv - v) * factor)]

    def stem_pivot_from_polygon(self, object_poly):
        points = np.array(object_poly, dtype=np.float64)
        min_y = float(points[:, 1].min())
        max_y = float(points[:, 1].max())
        height = max(max_y - min_y, 1e-8)
        candidates = points[points[:, 1] <= min_y + height * 0.04]
        if len(candidates) == 0:
            candidates = points[points[:, 1] == min_y]
        return float(np.median(candidates[:, 0])), min_y, "contour_bottom"

    def stem_pivot_from_skeleton(self, front_index, object_poly, leaf_scale):
        x, y, w, h = self.components[front_index]["bbox"]
        crop = self.component_mask(front_index)[y : y + h, x : x + w] > 127
        if crop.sum() == 0:
            return self.stem_pivot_from_polygon(object_poly)

        skeleton = morphology.skeletonize(crop)
        if not skeleton.any():
            return self.stem_pivot_from_polygon(object_poly)

        padded = np.pad(skeleton.astype(np.uint8), 1)
        endpoints = []
        ys, xs = np.nonzero(skeleton)
        for sy, sx in zip(ys, xs):
            window = padded[sy : sy + 3, sx : sx + 3]
            neighbor_count = int(window.sum()) - 1
            if neighbor_count == 1:
                endpoints.append((sx, sy))

        if not endpoints:
            return self.stem_pivot_from_polygon(object_poly)

        centroid_x = float(self.components[front_index]["centroid"][0] - x)
        centroid_y = float(self.components[front_index]["centroid"][1] - y)
        height = max(float(h), 1.0)
        width = max(float(w), 1.0)

        def endpoint_score(point):
            sx, sy = point
            lower_score = sy / height
            center_score = 1.0 - min(abs(sx - centroid_x) / width, 1.0)
            distance_score = np.hypot((sx - centroid_x) / width, (sy - centroid_y) / height)
            return lower_score * 3.0 + distance_score * 0.75 + center_score * 0.15

        sx, sy = max(endpoints, key=endpoint_score)
        px = x + float(sx)
        py = y + float(sy)
        ox = (px - (x + w * 0.5)) * leaf_scale
        oy = ((y + h * 0.5) - py) * leaf_scale
        return float(ox), float(oy), "skeleton_endpoint"

    def build(self):
        objects = []
        summaries = []
        leaf_scale = 1.0 / 620.0
        single_plate = self.surface_mode == "SINGLE"
        for pair_number, pair in enumerate(self.pairs, 1):
            front_index = int(pair["front"])
            front_bbox = self.components[front_index]["bbox"]
            fx, fy, fw, fh = front_bbox
            image_poly, dense_count, epsilon = self.extract_subpixel_polygon(front_index)
            object_poly = [
                ((px - (fx + fw * 0.5)) * leaf_scale, ((fy + fh * 0.5) - py) * leaf_scale)
                for px, py in image_poly
            ]
            if signed_area(object_poly) < 0:
                image_poly = list(reversed(image_poly))
                object_poly = list(reversed(object_poly))
            q_vertices, q_triangles, q_segments, max_area, min_angle = self.triangulate_quality(object_poly)
            pivot_x, pivot_y, pivot_source = self.stem_pivot_from_skeleton(front_index, object_poly, leaf_scale)

            col = (pair_number - 1) % 4
            row = (pair_number - 1) // 4
            leaf_center_x = 0.0 if self.place_at_origin else (col - 1.5) * 1.45
            leaf_center_y = 0.0 if self.place_at_origin else (1 - row) * 1.45

            vertices = []
            uvs = []
            front_z = 0.0 if single_plate else self.shell_gap * 0.5
            back_z = -self.shell_gap * 0.5
            front_uvs = []
            back_uvs = []
            face_uvs = []
            front_center_uv = self.uv_from_pixel(
                self.components[front_index]["centroid"][0],
                self.components[front_index]["centroid"][1],
            )
            for ox, oy in q_vertices:
                px = ox / leaf_scale + (fx + fw * 0.5)
                py = (fy + fh * 0.5) - oy / leaf_scale
                front_world = [float(ox - pivot_x + leaf_center_x), float(oy - pivot_y + leaf_center_y), float(front_z)]
                fu, fv = self.uv_from_pixel(px, py)
                vertices.append(front_world)
                uvs.append([float(fu), float(fv)])
                front_uvs.append([float(fu), float(fv)])
                if not single_plate:
                    back_world = [float(ox - pivot_x + leaf_center_x), float(oy - pivot_y + leaf_center_y), float(back_z)]
                    vertices.append(back_world)
                    uvs.append([float(fu), float(fv)])
                    back_uvs.append([float(fu), float(fv)])

            faces = []
            face_materials = []
            for a, b, c in q_triangles:
                if single_plate:
                    faces.append([int(a), int(b), int(c)])
                else:
                    faces.append([int(a * 2), int(b * 2), int(c * 2)])
                face_materials.append(0)
                face_uvs.append([front_uvs[int(a)], front_uvs[int(b)], front_uvs[int(c)]])
            if not single_plate:
                for a, b, c in q_triangles:
                    faces.append([int(c * 2 + 1), int(b * 2 + 1), int(a * 2 + 1)])
                    face_materials.append(1)
                    face_uvs.append([back_uvs[int(c)], back_uvs[int(b)], back_uvs[int(a)]])

            if not single_plate and not self.no_shell:
                for start_index, end_index in q_segments:
                    start_index = int(start_index)
                    end_index = int(end_index)
                    faces.append([start_index * 2, start_index * 2 + 1, end_index * 2 + 1, end_index * 2])
                    face_materials.append(2)
                    start_uv = front_uvs[start_index]
                    end_uv = front_uvs[end_index]
                    start_inner_uv = self.inset_uv_toward(start_uv, front_center_uv)
                    end_inner_uv = self.inset_uv_toward(end_uv, front_center_uv)
                    face_uvs.append([start_uv, start_inner_uv, end_inner_uv, end_uv])

            objects.append(
                {
                    "name": f"leaf_{pair_number:02d}_front_{front_index:02d}_{self.surface_mode.lower()}_plate",
                    "materials": ["front"] if single_plate else ["front", "back", "side"],
                    "vertices": vertices,
                    "uvs": uvs,
                    "faces": faces,
                    "face_materials": face_materials,
                    "face_uvs": face_uvs,
                    "location": [0.0, 0.0, 0.0],
                    "pivot": [pivot_x, pivot_y],
                    "pivot_source": pivot_source,
                    "shell_gap": self.shell_gap,
                    "side_uv_inset": self.side_uv_inset,
                    "no_shell": self.no_shell,
                    "surface_mode": self.surface_mode,
                    "boundary_count": int(len(q_segments)),
                }
            )
            summaries.append(
                {
                    "pair": pair_number,
                    "front": front_index,
                    "boundary_points": len(image_poly),
                    "quality_vertices": len(q_vertices),
                    "triangles_per_side": len(q_triangles),
                    "side_quads": 0 if single_plate or self.no_shell else int(len(q_segments)),
                    "shell_gap": self.shell_gap,
                    "side_uv_inset": self.side_uv_inset,
                    "no_shell": self.no_shell,
                    "surface_mode": self.surface_mode,
                    "pivot": [pivot_x, pivot_y],
                    "pivot_source": pivot_source,
                    "epsilon": epsilon,
                    "max_area": max_area,
                    "min_angle": min_angle,
                }
            )
        return {
            "version": 1,
            "albedo_path": str(self.albedo_path),
            "alpha_path": str(self.alpha_path),
            "quality": self.quality_name,
            "shell_gap": self.shell_gap,
            "side_uv_inset": self.side_uv_inset,
            "no_shell": self.no_shell,
            "surface_mode": self.surface_mode,
            "place_at_origin": self.place_at_origin,
            "component_count": len(self.components),
            "objects": objects,
            "summary": summaries,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--albedo", required=True)
    parser.add_argument("--alpha", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--pairs-json", default="")
    parser.add_argument("--preview-dir", default="")
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--quality", choices=sorted(QUALITY_PRESETS), default="BALANCED")
    parser.add_argument("--alpha-threshold", type=int, default=127)
    parser.add_argument("--min-area", type=int, default=400)
    parser.add_argument("--shell-gap", type=float, default=0.012)
    parser.add_argument("--side-uv-inset", type=float, default=0.035)
    parser.add_argument("--no-shell", action="store_true")
    parser.add_argument("--surface-mode", choices=("SINGLE", "DOUBLE"), default="DOUBLE")
    parser.add_argument("--place-at-origin", action="store_true")
    args = parser.parse_args()

    pairs = parse_pairs(args.pairs_json)
    pipeline = Pipeline(
        args.albedo,
        args.alpha,
        pairs,
        args.quality,
        args.alpha_threshold,
        args.min_area,
        args.shell_gap,
        args.side_uv_inset,
        args.place_at_origin,
        args.no_shell,
        args.surface_mode,
    )
    if args.preview_only:
        data = {
            "version": 1,
            "albedo_path": str(pipeline.albedo_path),
            "alpha_path": str(pipeline.alpha_path),
            "component_count": len(pipeline.components),
            "preview": pipeline.write_label_previews(args.preview_dir or Path(args.output).parent),
        }
    else:
        data = pipeline.build()
        if args.preview_dir:
            data["preview"] = pipeline.write_label_previews(args.preview_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data), encoding="utf-8")


if __name__ == "__main__":
    main()
