from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

from test_speedtree_xml import load_speedtree_module


speedtree = load_speedtree_module()


def test_cloud_ao_retains_reference_without_reading_or_trusting_cache(tmp_path):
    color = tmp_path / "color.png"
    color.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (2048).to_bytes(4, "big") * 2)
    ao = tmp_path / "ao.png"
    ao.write_bytes(b"not available locally")
    blend = tmp_path / "source.blend"
    blend.write_bytes(b"blend")
    maps = {"color": str(color), "ao": str(ao)}
    inputs = {"leaf:color": str(color), "leaf:ao": str(ao)}

    with patch.object(speedtree, "_texture_needs_cloud_recall", side_effect=lambda p: Path(p) == ao):
        assert speedtree.common_texture_dimensions(maps) == (2048, 2048)
        signature = speedtree.texture_path_signature(inputs, allow_unavailable_optional_ao=True)
        receipt = speedtree.source_refresh_receipt(blend, signature, inputs)
        assert not speedtree.source_refresh_receipt_is_current(receipt, blend)
        material = ET.Element("Material_v8")
        ao_node = ET.SubElement(material, "Map", {"Name": "AO"})
        canonical = {role: str(color) for role in ("color", "opacity", "normal", "extra", "height", "subsurface")}
        canonical["ao"] = str(ao)
        speedtree.update_spm_material_texture_maps(material, tmp_path / "target.spm", canonical)
        assert ao_node.findtext("TexFilename").replace("\\", "/").endswith("ao.png")
        assert ao_node.findtext("TexEnabled") == "true"

    # Once the real data is local, the path-only signature cannot certify it.
    assert speedtree.texture_path_signature(inputs) != signature


def test_dimension_mismatch_still_fails_for_local_ao(tmp_path):
    def write_png(path, size):
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + size.to_bytes(4, "big") * 2)

    color = tmp_path / "color.png"
    ao = tmp_path / "ao.png"
    write_png(color, 2048)
    write_png(ao, 1024)
    try:
        speedtree.common_texture_dimensions({"color": color, "ao": ao})
    except RuntimeError as exc:
        assert "must share one pixel resolution" in str(exc)
    else:
        raise AssertionError("Local mismatched AO must still be rejected")
