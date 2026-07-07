import subprocess
from pathlib import Path


def run_external_python(python_exe, args, timeout=300):
    command = [python_exe] + args
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def dependency_status(python_exe):
    code = "import PIL, cv2, skimage, triangle; print('ok')"
    result = run_external_python(python_exe, ["-c", code], timeout=30)
    return result.returncode == 0, result.stdout.strip(), result.stderr.strip()

def write_report(result, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "atlas_leaf_mesh_builder_report.txt"
    lines = [
        "Atlas Leaf Mesh Builder Report",
        f"Albedo: {result['albedo_path']}",
        f"Alpha: {result['alpha_path']}",
        f"Quality: {result['quality']}",
        f"Surface mode: {result.get('surface_mode', 'DOUBLE')}",
        f"Shell gap: {result.get('shell_gap', 0.0)}",
        f"Side UV inset: {result.get('side_uv_inset', 0.0)}",
        f"Place at origin: {result.get('place_at_origin', False)}",
        f"Component count: {result['component_count']}",
        f"Object count: {len(result['objects'])}",
        "",
    ]
    for item in result["summary"]:
        template = (
            "leaf {pair:02d}: front {front:02d}, "
            "surface {surface_mode}, verts {quality_vertices}, "
            "tris/side {triangles_per_side}, side_quads {side_quads}, "
            "shell_gap {shell_gap:.4f}, side_uv_inset {side_uv_inset:.4f}, "
            "pivot {pivot_source}, min_angle {min_angle:.2f}"
        )
        lines.append(template.format(**item))
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
