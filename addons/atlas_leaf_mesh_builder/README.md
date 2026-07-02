# Atlas Leaf Mesh Builder

Blender add-on for building no-opacity front/back leaf meshes from paired texture-atlas alpha islands.

## What It Does

- Reads an albedo atlas and alpha atlas.
- Detects alpha islands.
- Uses a front/back pair table.
- Auto-selects the best UV transform per pair.
- Extracts a subpixel silhouette with marching squares.
- Builds a constrained quality triangulation with the external `triangle` Python package.
- Creates separate Blender mesh objects for each front and back side.
- Keeps the original atlas texture set intact.

## Blender UI

Location:

```text
3D Viewport > Sidebar > Atlas Leaf > Atlas Leaf Mesh
```

## Current Default Preset

The default paths and pair preset target:

```text
D:/OneDrive/Forestportfolio/Texture/Elm/TCom_Leaves_Elm01
```

The default pair table is the Elm01 pair table validated during the test pass.

## Dependency Model

The add-on UI runs in Blender, but the image processing and triangulation run in an external Python process. This avoids packaging compiled modules inside Blender.

Required external Python packages:

- Pillow
- opencv-python
- scikit-image
- triangle

Use the add-on's `Check Dependencies` and `Install Dependencies` buttons.

## Generated Structure

The add-on creates:

```text
Atlas_Leaf_Meshes
  leaf_pair_01_front_01_to_back_18_flip_x
  leaf_pair_01_back_18_from_front_01_flip_x
  ...
```

Each pair has a separate front object and back object.

Materials:

- `elm01_leaf_front_atlas`
- `elm01_leaf_back_atlas`

Both materials reference the same albedo atlas texture.

## Notes

This first add-on pass intentionally keeps pair selection as a user-editable JSON preset. The transform inside each pair is still auto-selected by mask similarity.

Future improvements:

- visual island labeling inside Blender,
- automatic pair suggestions,
- manual transform override per pair,
- batch processing,
- FBX export options,
- support for assigning normal/roughness/translucency maps into the generated materials.
