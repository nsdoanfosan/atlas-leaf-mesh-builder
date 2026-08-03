# Atlas Leaf Mesh Builder

Blender add-on for building no-opacity leaf meshes from texture-atlas alpha islands.

This add-on was built around the `TCom_Leaves_Elm01` atlas workflow:

- keep the original texture atlas set intact,
- use alpha islands as real mesh silhouettes,
- generate every detected alpha island automatically in one-plate mode,
- choose one plate or two matching front/back plates,
- generate one mesh per front island,
- bridge the real triangulation boundary between front/back surfaces,
- keep shell bridge UVs inset inside the front island,
- place each leaf object at the stem-side pivot origin,
- use quality triangulation to avoid long fan triangles,
- build or update a SpeedTree 10.1 `.spm` asset file plus per-leaf FBX meshes and atlas textures.

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

- albedo input with automatic same-folder opacity/alpha matching,
- Elm01 front island preset,
- editable front island JSON,
- hidden legacy Front Island Editor internals for compatibility, with no island-list management UI in the normal panel,
- automatic all-island selection whenever One Plate mode is used,
- marching-squares subpixel contour extraction,
- Triangle quality triangulation,
- one-plate or two-plate mesh output,
- manual Front/Back Projected Shell workflow for atlases whose front/back silhouettes do not match exactly,
- explicit Front and Back Projection object assignment, with the Front topology/pivot preserved and back UVs barycentrically projected from a manually aligned larger plate,
- projection coverage validation that stops before output creation when any Front vertex falls outside the Back Projection mesh and selects the nearest Back boundary vertices in Edit Mode,
- successful projected-shell builds move both source plates into a hidden, render-disabled, non-selectable backup collection and clear the Front/Back slots,
- two-plate shell objects with adjustable shell gap,
- adjustable side UV inset for the shell bridge,
- simple shell side geometry with material-boundary sharp edges and angle-based shell-side sharp edges,
- stem-side pivot placement at object origin,
- destructive Straight Mesh processing with one hidden original backup per source object, reused on repeat runs,
- geometry-preserving Straight Mesh behavior that never automatically deletes root or below-pivot geometry,
- branching-plate detection with a deformation guard: safe main-stem unbend when possible, automatic shape-preserving alignment when plate edges would stretch, and nonlinear unbend for strand-like meshes,
- same-atlas front/back material slots using the same UVs,
- SpeedTree 10.1 `.spm` create/update with named atlas materials linked to every detected live mesh variant,
- repeat updates that reuse the existing material/mesh IDs when the material name already exists,
- per-Generator-slot current ownership receipts derived from the live SPM, with no fixed Type/Frond count,
- immutable slot-creation provenance kept separately from current ownership so multiple Atlas providers can intentionally own disjoint slots in one SPM,
- fail-closed cross-provider reconciliation and atomic staged rewrites of every affected provider receipt,
- generation report.

Not implemented yet:

- visual island label overlay inside Blender,
- normal/roughness/height/translucency material node setup.

## Validation

Validated with Blender 5.1.2:

- add-on enables successfully,
- default enable saved to Blender preferences,
- generation operator creates 12 mesh objects for Elm01,
- front/back material slots both reference the same source albedo atlas and use matching UVs,
- shell bridge side faces have inset UVs inside the front atlas island,
- generated shell meshes use simple side quads, material-boundary sharp edges, shell-side angle sharp edges, and weighted normals,
- Chestnut atlas `leaf_04_front_04_single_plate` projected from `leaf_13_front_13_single_plate` creates 270 vertices and 437 faces while preserving both source meshes; the duplicated surfaces contain 169 matching faces each plus 99 side faces,
- SpeedTree build/update operator creates or updates one target `.spm`, exports the collection's live FBX variants, references the original texture files without creating copies, and records material/mesh IDs plus Generator ownership/provenance contracts in the manifest.

## SpeedTree Generator receipt contracts

Each current target receipt publishes two independent versioned blocks:

- `generator_binding_ownership`: the provider's current live `(Generator GUID, slot prefix) -> (Material ID, Mesh ID)` projection.
- `generator_slot_creation_provenance`: immutable evidence for slots structurally created by that provider.

`generator_connection.bindings` remains a compatibility view of current ownership only. `generator_connection.authored_bindings` retains the provider's original full binding rows. When the live SPM proves that another provider now owns a slot, fleet updates shrink the former provider's current bindings and append relinquishment history without deleting its creation provenance. Ambiguous or unproven takeovers stop before commit; cross-provider receipt changes require the staged all-or-nothing target transaction.
