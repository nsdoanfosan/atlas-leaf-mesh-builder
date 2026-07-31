# Atlas fleet refresh hotfix

This is a one-shot command-line recovery tool. It does not add a Blender UI
button or save new properties into source `.blend` files.

The safe order is always:

1. `plan` discovers exact `*.atlas_leaf_targets.json` registries and hashes all
   source blends, target SPMs, managed exports, and receipts without writing the
   production tree.
2. Review the generated JSON plan.
3. `apply` rechecks every hash, creates a persistent byte-for-byte backup, runs
   each source through Blender, and performs a read-only semantic verification.
4. `rollback` restores the persistent backup and removes only managed artifacts
   created after the plan.

For field validation, create a path-rebased staging clone first. The clone keeps
SPM bytes identical and refuses absolute production Mesh filenames so a Blender
worker cannot silently write through to the source tree:

```powershell
python tools/atlas_fleet_refresh.py stage-clone `
  --source-root 'D:\OneDrive\Forestportfolio\02_nature\Tree' `
  --staging-root 'C:\Users\PARK\Documents\CodexStaging\atlas-silky-20260801' `
  --registry 'D:\...\M_cluster_silky_dogwood_atlas_01.atlas_leaf_targets.json' `
  --registry 'D:\...\M_leaf_silky_dogwood_atlas_01.atlas_leaf_targets.json' `
  --registry 'D:\...\SK_cluster_Silky_Dogwood_01.atlas_leaf_targets.json'
```

An original-tree plan is read-only and is useful only for inventory review:

```powershell
python tools/atlas_fleet_refresh.py plan `
  --production-root 'D:\OneDrive\Forestportfolio\02_nature\Tree' `
  --output 'C:\Users\PARK\Documents\CodexBackups\atlas-refresh\plan.json'
```

Do not apply that D: plan. Build a second plan whose production root and registry
paths are all inside the verified staging clone. Apply is intentionally explicit
and requires the tested add-on worktree and a new, empty backup directory:

```powershell
python tools/atlas_fleet_refresh.py plan `
  --production-root 'C:\Users\PARK\Documents\CodexStaging\atlas-silky-20260801' `
  --output 'C:\Users\PARK\Documents\CodexBackups\atlas-refresh\staging-plan.json'

python tools/atlas_fleet_refresh.py apply `
  --plan 'C:\Users\PARK\Documents\CodexBackups\atlas-refresh\staging-plan.json' `
  --backup-root 'C:\Users\PARK\Documents\CodexBackups\atlas-refresh\backup-20260801' `
  --blender 'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe' `
  --addon-root 'C:\Users\PARK\Documents\CodexWorktrees\atlas-leaf-issue-4-fleet-refresh'
```

Do not reuse a backup directory. If any source update or verification fails,
the controller stops and restores the complete pre-run managed inventory.

`applied_and_verified_with_reference_attention` is not permission to delete
unbound assets. It means validation succeeded but at least one managed Mesh is
not referenced by a Generator. The result separates current authoritative
source output from groupless legacy or non-authoritative ownership; legacy data
must have lineage/tombstone evidence before cleanup.
