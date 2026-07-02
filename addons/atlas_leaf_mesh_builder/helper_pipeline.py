from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from skimage import measure
import triangle as tr


DEFAULT_PAIRS = [
    (1, 18),
    (2, 17),
    (3, 16),
    (4, 15),
    (10, 5),
    (6, 19),
    (7, 21),
    (8, 23),
    (9, 20),
    (11, 14),
    (12, 22),
    (13, 24),
]

TRANSFORMS = {
    "identity": lambda x, y: (x, y),
    "rot90_cw": lambda x, y: (1.0 - y, x),
    "rot180": lambda x, y: (1.0 - x, 1.0 - y),
    "rot90_ccw": lambda x, y: (y, 1.0 - x),
    "flip_x": lambda x, y: (1.0 - x, y),
    "flip_y": lambda x, y: (x, 1.0 - y),
    "transpose": lambda x, y: (y, x),
    "anti_transpose": lambda x, y: (1.0 - y, 1.0 - x),
}

QUALITY_PRESETS = {
    "FAST": {"q": 12, "area_factor": 0.24},
    "BALANCED": {"q": 18, "area_factor": 0.42},
    "HIGH": {"q": 22, "area_factor": 0.95},
}


def parse_pairs(text: str | None) -> list[tuple[int, int]]:
    if not text:
        return DEFAULT_PAIRS
    data = json.loads(text)
    pairs = []
    for item in data:
        if isinstance(item, dict):
            pairs.append((int(item["front"]), int(item["back"])))
        else:
            pairs.append((int(item[0]), int(item[1])))
    return pairs


def transform_image(image, name):
    if name == "identity":
        return image
    if name == "rot90_cw":
        return np.rot90(image, 3)
    if name == "rot180":
        return np.rot90(image, 2)
    if name == "rot90_ccw":
        return np.rot90(image, 1)
    if name == "flip_x":
        return np.fliplr(image)
    if name == "flip_y":
        return np.flipud(image)
    if name == "transpose":
        return image.T
    if name == "anti_transpose":
        return np.rot90(image.T, 2)
    raise ValueError(name)


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
    def __init__(self, albedo_path, alpha_path, pairs, quality, alpha_threshold, min_area):
        self.albedo_path = Path(albedo_path)
        self.alpha_path = Path(alpha_path)
        self.pairs = pairs
        self.quality = QUALITY_PRESETS[quality]
        self.quality_name = quality
        self.alpha_threshold = alpha_threshold
        self.min_area = min_area

        alpha = np.array(Image.open(self.alpha_path).convert("RGB"))[..., 0]
        self.height, self.width = alpha.shape
        mask = (alpha > alpha_threshold).astype(np.uint8) * 255
        self.mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
        self.num_labels, self.labels, self.stats, self.centroids = cv2.connectedComponentsWithStats(
            self.mask, connectivity=8
        )
        self.components = self._collect_components()

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

    def select_transform(self, front_index, back_index):
        front_canon = self.canonical_mask(front_index)
        back_canon = self.canonical_mask(back_index)
        scores = []
        for name in TRANSFORMS:
            moved_front = transform_image(front_canon, name)
            if moved_front.shape != back_canon.shape:
                moved_front = (
                    cv2.resize(moved_front.astype(np.uint8), back_canon.shape[::-1], interpolation=cv2.INTER_NEAREST)
                    > 0
                )
            intersection = np.logical_and(moved_front, back_canon).sum()
            union = np.logical_or(moved_front, back_canon).sum()
            scores.append((float(intersection / union) if union else 0.0, name))
        scores.sort(reverse=True)
        return scores[0][1], scores

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
        image_points = remove_near_duplicates(image_points, 0.35)
        contour_np = np.array(image_points, dtype=np.float32).reshape((-1, 1, 2))
        perimeter = cv2.arcLength(contour_np, True)
        epsilon = max(0.55, min(1.35, perimeter * 0.00055))
        approx = cv2.approxPolyDP(contour_np, epsilon, True).reshape((-1, 2))
        simplified = remove_near_duplicates([(float(px), float(py)) for px, py in approx], 0.6)
        simplified = remove_almost_collinear(simplified, 0.05)
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
        min_angles = [triangle_min_angle(out_vertices[a], out_vertices[b], out_vertices[c]) for a, b, c in triangles]
        return out_vertices, triangles, max_area, float(min(min_angles))

    def uv_from_pixel(self, px, py):
        return px / self.width, 1.0 - py / self.height

    def map_front_pixel_to_back(self, px, py, front_bbox, back_bbox, transform_name):
        fx, fy, fw, fh = front_bbox
        bx, by, bw, bh = back_bbox
        nx = (px - fx) / max(1, fw)
        ny = (py - fy) / max(1, fh)
        tx, ty = TRANSFORMS[transform_name](nx, ny)
        return bx + tx * bw, by + ty * bh

    def build(self):
        objects = []
        summaries = []
        leaf_scale = 1.0 / 620.0
        for pair_number, (front_index, back_index) in enumerate(self.pairs, 1):
            transform_name, transform_scores = self.select_transform(front_index, back_index)
            front_bbox = self.components[front_index]["bbox"]
            back_bbox = self.components[back_index]["bbox"]
            fx, fy, fw, fh = front_bbox
            image_poly, dense_count, epsilon = self.extract_subpixel_polygon(front_index)
            object_poly = [
                ((px - (fx + fw * 0.5)) * leaf_scale, ((fy + fh * 0.5) - py) * leaf_scale)
                for px, py in image_poly
            ]
            if signed_area(object_poly) < 0:
                image_poly = list(reversed(image_poly))
                object_poly = list(reversed(object_poly))
            q_vertices, q_triangles, max_area, min_angle = self.triangulate_quality(object_poly)

            col = (pair_number - 1) % 4
            row = (pair_number - 1) // 4
            leaf_center_x = (col - 1.5) * 1.45
            leaf_center_y = (1 - row) * 1.45

            front_vertices = []
            back_vertices = []
            front_uvs = []
            back_uvs = []
            for ox, oy in q_vertices:
                px = ox / leaf_scale + (fx + fw * 0.5)
                py = (fy + fh * 0.5) - oy / leaf_scale
                world = [float(ox + leaf_center_x), float(oy + leaf_center_y), 0.0]
                fu, fv = self.uv_from_pixel(px, py)
                bx, by = self.map_front_pixel_to_back(px, py, front_bbox, back_bbox, transform_name)
                bu, bv = self.uv_from_pixel(bx, by)
                front_vertices.append(world)
                back_vertices.append(world)
                front_uvs.append([float(fu), float(fv)])
                back_uvs.append([float(bu), float(bv)])

            front_faces = [[int(a), int(b), int(c)] for a, b, c in q_triangles]
            back_faces = [[int(c), int(b), int(a)] for a, b, c in q_triangles]
            objects.append(
                {
                    "name": f"leaf_pair_{pair_number:02d}_front_{front_index:02d}_to_back_{back_index:02d}_{transform_name}",
                    "material": "front",
                    "vertices": front_vertices,
                    "uvs": front_uvs,
                    "faces": front_faces,
                }
            )
            objects.append(
                {
                    "name": f"leaf_pair_{pair_number:02d}_back_{back_index:02d}_from_front_{front_index:02d}_{transform_name}",
                    "material": "back",
                    "vertices": back_vertices,
                    "uvs": back_uvs,
                    "faces": back_faces,
                }
            )
            summaries.append(
                {
                    "pair": pair_number,
                    "front": front_index,
                    "back": back_index,
                    "transform": transform_name,
                    "iou": transform_scores[0][0],
                    "boundary_points": len(image_poly),
                    "quality_vertices": len(q_vertices),
                    "triangles_per_side": len(q_triangles),
                    "epsilon": epsilon,
                    "max_area": max_area,
                    "min_angle": min_angle,
                    "top_candidates": [{"transform": name, "score": score} for score, name in transform_scores[:4]],
                }
            )
        return {
            "version": 1,
            "albedo_path": str(self.albedo_path),
            "alpha_path": str(self.alpha_path),
            "quality": self.quality_name,
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
    parser.add_argument("--quality", choices=sorted(QUALITY_PRESETS), default="BALANCED")
    parser.add_argument("--alpha-threshold", type=int, default=127)
    parser.add_argument("--min-area", type=int, default=400)
    args = parser.parse_args()

    pipeline = Pipeline(
        args.albedo,
        args.alpha,
        parse_pairs(args.pairs_json),
        args.quality,
        args.alpha_threshold,
        args.min_area,
    )
    data = pipeline.build()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data), encoding="utf-8")


if __name__ == "__main__":
    main()
