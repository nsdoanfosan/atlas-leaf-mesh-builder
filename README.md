# Atlas Leaf Mesh Builder

Blender add-on for building no-opacity front/back leaf meshes from paired texture-atlas alpha islands.

This add-on was built around the `TCom_Leaves_Elm01` atlas workflow:

- keep the original texture atlas set intact,
- use alpha islands as real mesh silhouettes,
- pair front/back atlas islands,
- auto-select UV transforms inside each pair,
- generate front/back as separate Blender objects,
- avoid shell side faces,
- use quality triangulation to avoid long fan triangles.

## Blender Install

The add-on package lives at:

```text
addons/atlas_leaf_mesh_builder
```

On this machine it is installed into Blender 5.1 by junction:

```text
C:/Users/PARK/AppData/Roaming/Blender Foundation/Blender/5.1/scripts/addons/atlas_leaf_mesh_builder
```

Blender UI:

```text
3D Viewport > Sidebar > Atlas Leaf > Atlas Leaf Mesh
```

## Dependencies

The Blender UI calls an external Python helper for image processing and triangulation.

Required external Python packages:

- Pillow
- opencv-python
- scikit-image
- triangle

The add-on includes `Check Dependencies` and `Install Dependencies` buttons.

## Current State

Implemented:

- albedo/alpha file inputs,
- Elm01 pair preset,
- editable pair JSON,
- automatic per-pair transform selection,
- marching-squares subpixel contour extraction,
- Triangle quality triangulation,
- separate front/back Blender objects,
- same-atlas front/back material slots,
- generation report.

Not implemented yet:

- visual island label overlay inside Blender,
- automatic pair suggestions,
- per-pair transform override UI,
- normal/roughness/height/translucency material node setup,
- export operators.

## Validation

Validated with Blender 5.1.2:

- add-on enables successfully,
- default enable saved to Blender preferences,
- generation operator creates 24 mesh objects for Elm01,
- front/back material slots both reference the same source albedo atlas.
