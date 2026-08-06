"""Versioned current Generator ownership and immutable slot provenance.

``generator_connection.bindings`` historically carried two independent
meanings: the provider's current Material/Mesh ownership and the structural
provenance of slots it created.  Cross-provider assembly makes those meanings
diverge.  This module keeps the current projection intentionally small while
retaining creation evidence independently.

The functions here are pure manifest transformations.  The SpeedTree writer
owns live XML inspection and filesystem transactions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path


OWNERSHIP_CONTRACT = "atlas_generator_current_binding_ownership"
OWNERSHIP_VERSION = 1
OWNERSHIP_BASIS = "live_spm_material_mesh_projection"
CREATION_CONTRACT = "atlas_generator_slot_creation_provenance"
CREATION_VERSION = 1
MATERIAL_DEFAULT_MESH_ID = -10


class GeneratorSlotOwnershipError(ValueError):
    """A current-owner or creator-provenance contract is ambiguous."""


def canonical_fingerprint(value):
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _integer(value, label, *, positive=False):
    if isinstance(value, bool):
        raise GeneratorSlotOwnershipError(f"{label} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GeneratorSlotOwnershipError(
            f"{label} must be an integer"
        ) from exc
    if positive and parsed <= 0:
        raise GeneratorSlotOwnershipError(
            f"{label} must be a positive integer"
        )
    return parsed


def _text(value, label):
    text = str(value or "").strip()
    if not text:
        raise GeneratorSlotOwnershipError(f"{label} must be non-empty")
    return text


def canonical_target_mesh_id(value, label="target_mesh_id"):
    parsed = _integer(value, label)
    if parsed != MATERIAL_DEFAULT_MESH_ID and parsed <= 0:
        raise GeneratorSlotOwnershipError(
            f"{label} must be a positive integer or "
            f"{MATERIAL_DEFAULT_MESH_ID} (material default)"
        )
    return parsed


def binding_key(binding):
    """Return the exact case-sensitive semantic slot key.

    SpeedTree GUIDs are opaque Base64-like identifiers.  Case folding them can
    collapse two different byte identities, so only surrounding whitespace is
    normalized.  Slot prefixes are likewise opaque property prefixes.
    """
    if not isinstance(binding, dict):
        raise GeneratorSlotOwnershipError("Generator binding must be an object")
    return (
        _text(binding.get("generator_guid"), "generator_guid"),
        _text(binding.get("slot_prefix"), "slot_prefix"),
    )


def ownership_binding_projection(binding):
    guid, slot_prefix = binding_key(binding)
    return {
        "generator_guid": guid,
        "slot_prefix": slot_prefix,
        "target_material_id": _integer(
            binding.get("target_material_id"),
            "target_material_id",
            positive=True,
        ),
        "target_mesh_id": canonical_target_mesh_id(
            binding.get("target_mesh_id"),
            "target_mesh_id",
        ),
    }


def canonical_ownership_bindings(bindings):
    if not isinstance(bindings, list):
        raise GeneratorSlotOwnershipError("ownership bindings must be a list")
    rows = []
    seen = set()
    for ordinal, binding in enumerate(bindings):
        try:
            row = ownership_binding_projection(binding)
        except GeneratorSlotOwnershipError as exc:
            raise GeneratorSlotOwnershipError(
                f"ownership binding #{ordinal + 1}: {exc}"
            ) from exc
        key = binding_key(row)
        if key in seen:
            raise GeneratorSlotOwnershipError(
                "ownership bindings contain duplicate Generator slots: "
                f"{key}"
            )
        seen.add(key)
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (row["generator_guid"], row["slot_prefix"]),
    )


def build_generator_binding_ownership(bindings):
    rows = canonical_ownership_bindings(list(bindings or []))
    return {
        "contract": OWNERSHIP_CONTRACT,
        "version": OWNERSHIP_VERSION,
        "basis": OWNERSHIP_BASIS,
        "binding_count": len(rows),
        "fingerprint": canonical_fingerprint(rows),
        "bindings": rows,
    }


def validate_generator_binding_ownership(contract):
    if not isinstance(contract, dict):
        raise GeneratorSlotOwnershipError(
            "generator_binding_ownership must be an object"
        )
    if (
        contract.get("contract") != OWNERSHIP_CONTRACT
        or contract.get("version") != OWNERSHIP_VERSION
        or contract.get("basis") != OWNERSHIP_BASIS
    ):
        raise GeneratorSlotOwnershipError(
            "generator_binding_ownership contract is unsupported"
        )
    rows = canonical_ownership_bindings(contract.get("bindings"))
    if contract.get("binding_count") != len(rows):
        raise GeneratorSlotOwnershipError(
            "generator_binding_ownership binding_count mismatch"
        )
    if str(contract.get("fingerprint") or "") != canonical_fingerprint(rows):
        raise GeneratorSlotOwnershipError(
            "generator_binding_ownership fingerprint mismatch"
        )
    return rows


_CREATION_DROP_FIELDS = {
    "state",
    "created_slot",
    "target_material_id",
    "target_material_name",
    "target_mesh_id",
}


def creation_provenance_slot(binding):
    """Convert one created binding into immutable structural provenance."""
    if not isinstance(binding, dict) or binding.get("created_slot") is not True:
        raise GeneratorSlotOwnershipError(
            "creation provenance requires a created_slot binding"
        )
    guid, slot_prefix = binding_key(binding)
    row = {
        key: copy.deepcopy(value)
        for key, value in binding.items()
        if key not in _CREATION_DROP_FIELDS
    }
    row["generator_guid"] = guid
    row["slot_prefix"] = slot_prefix
    row["initial_target_material_id"] = _integer(
        binding.get("target_material_id"),
        "created target_material_id",
        positive=True,
    )
    row["initial_target_mesh_id"] = canonical_target_mesh_id(
        binding.get("target_mesh_id"),
        "created target_mesh_id",
    )
    target_name = str(binding.get("target_material_name") or "").strip()
    if target_name:
        row["initial_target_material_name"] = target_name

    for field in (
        "generator_index",
        "variant_parent_children_before",
        "variant_parent_children_after",
        "leaf_ordinal",
        "source_material_id",
        "source_mesh_id",
    ):
        if field in row and row[field] is not None:
            row[field] = _integer(row[field], field)
    property_names = row.get("created_property_names")
    if property_names is not None:
        if not isinstance(property_names, list) or any(
            not isinstance(value, str) or not value.strip()
            for value in property_names
        ):
            raise GeneratorSlotOwnershipError(
                "created_property_names must be a list of non-empty strings"
            )
        row["created_property_names"] = [
            value.strip() for value in property_names
        ]
    return row


def canonical_creation_slots(slots):
    if not isinstance(slots, list):
        raise GeneratorSlotOwnershipError("creation provenance slots must be a list")
    rows = []
    seen = set()
    for ordinal, raw in enumerate(slots):
        if not isinstance(raw, dict):
            raise GeneratorSlotOwnershipError(
                f"creation provenance slot #{ordinal + 1} must be an object"
            )
        row = copy.deepcopy(raw)
        guid, slot_prefix = binding_key(row)
        row["generator_guid"] = guid
        row["slot_prefix"] = slot_prefix
        for field in (
            "generator_index",
            "variant_parent_children_before",
            "variant_parent_children_after",
            "leaf_ordinal",
            "source_material_id",
            "source_mesh_id",
            "initial_target_material_id",
        ):
            if field in row and row[field] is not None:
                row[field] = _integer(row[field], field)
        if row.get("initial_target_mesh_id") is not None:
            row["initial_target_mesh_id"] = canonical_target_mesh_id(
                row["initial_target_mesh_id"],
                "initial_target_mesh_id",
            )
        key = (guid, slot_prefix)
        if key in seen:
            raise GeneratorSlotOwnershipError(
                "creation provenance contains duplicate Generator slots: "
                f"{key}"
            )
        seen.add(key)
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (row["generator_guid"], row["slot_prefix"]),
    )


def build_generator_slot_creation_provenance(
    authored_bindings,
    *,
    existing_contract=None,
):
    by_key = {}
    if existing_contract is not None:
        for row in validate_generator_slot_creation_provenance(
            existing_contract
        ):
            by_key[binding_key(row)] = row
    for binding in authored_bindings or []:
        if not isinstance(binding, dict) or binding.get("created_slot") is not True:
            continue
        row = creation_provenance_slot(binding)
        key = binding_key(row)
        previous = by_key.get(key)
        if previous is not None:
            # The first sealed creator row is immutable.  A later explicit
            # delivery generation may reacquire the semantic slot with a new
            # live target pair without rewriting its structural origin.
            continue
        by_key[key] = row
    rows = canonical_creation_slots(list(by_key.values()))
    return {
        "contract": CREATION_CONTRACT,
        "version": CREATION_VERSION,
        "slot_count": len(rows),
        "fingerprint": canonical_fingerprint(rows),
        "slots": rows,
    }


def validate_generator_slot_creation_provenance(contract):
    if not isinstance(contract, dict):
        raise GeneratorSlotOwnershipError(
            "generator_slot_creation_provenance must be an object"
        )
    if (
        contract.get("contract") != CREATION_CONTRACT
        or contract.get("version") != CREATION_VERSION
    ):
        raise GeneratorSlotOwnershipError(
            "generator_slot_creation_provenance contract is unsupported"
        )
    rows = canonical_creation_slots(contract.get("slots"))
    if contract.get("slot_count") != len(rows):
        raise GeneratorSlotOwnershipError(
            "generator_slot_creation_provenance slot_count mismatch"
        )
    if str(contract.get("fingerprint") or "") != canonical_fingerprint(rows):
        raise GeneratorSlotOwnershipError(
            "generator_slot_creation_provenance fingerprint mismatch"
        )
    return rows


def _path_key(value, *, relative_to=None):
    path = Path(str(value or "")).expanduser()
    if relative_to is not None and not path.is_absolute():
        path = Path(relative_to) / path
    try:
        path = path.resolve(strict=False)
    except (OSError, RuntimeError):
        path = path.absolute()
    return os.path.normcase(str(path)).casefold()


def provider_identity(manifest, *, relative_to=None):
    if not isinstance(manifest, dict):
        raise GeneratorSlotOwnershipError("provider manifest must be an object")
    blend_file = _text(manifest.get("blend_file"), "provider blend_file")
    return {
        "blend_file": _path_key(blend_file, relative_to=relative_to),
        "source_collection": _text(
            manifest.get("source_collection"),
            "provider source_collection",
        ).casefold(),
        "export_scope_id": _text(
            manifest.get("export_scope_id"),
            "provider export_scope_id",
        ).casefold(),
    }


def provider_key(identity):
    return (
        identity["blend_file"],
        identity["source_collection"],
        identity["export_scope_id"],
    )


def authored_bindings(manifest):
    connection = (manifest or {}).get("generator_connection") or {}
    rows = connection.get("authored_bindings")
    if rows is None:
        rows = connection.get("bindings") or []
    if not isinstance(rows, list):
        raise GeneratorSlotOwnershipError(
            "generator_connection.authored_bindings must be a list"
        )
    return copy.deepcopy(rows)


def current_bindings(manifest):
    """Return current full binding rows; explicit ownership never falls back."""
    connection = (manifest or {}).get("generator_connection") or {}
    rows = connection.get("bindings") or []
    if not isinstance(rows, list):
        raise GeneratorSlotOwnershipError(
            "generator_connection.bindings must be a list"
        )
    ownership = (manifest or {}).get("generator_binding_ownership")
    if ownership is None:
        canonical_ownership_bindings(rows)
        return copy.deepcopy(rows)

    projected = validate_generator_binding_ownership(ownership)
    full_by_key = {}
    for row in rows:
        key = binding_key(row)
        if key in full_by_key:
            raise GeneratorSlotOwnershipError(
                f"generator_connection.bindings duplicates {key}"
            )
        full_by_key[key] = row
    if set(full_by_key) != {binding_key(row) for row in projected}:
        raise GeneratorSlotOwnershipError(
            "explicit ownership bindings differ from generator_connection.bindings"
        )
    for projection in projected:
        if ownership_binding_projection(full_by_key[binding_key(projection)]) != projection:
            raise GeneratorSlotOwnershipError(
                "explicit ownership pair differs from generator_connection binding"
            )
    return copy.deepcopy(rows)


def manifest_with_binding_contracts(
    manifest,
    current_rows,
    *,
    relinquished_rows=None,
):
    """Return one provider receipt with separate current and creator state."""
    payload = copy.deepcopy(manifest)
    # Reinsert both sibling contracts together below.  This keeps JSON key
    # order stable when an existing creation contract is carried into a new
    # receipt (notably an idempotent collection tombstone rewrite).
    payload.pop("generator_binding_ownership", None)
    existing_creation_contract = payload.pop(
        "generator_slot_creation_provenance",
        None,
    )
    current_rows = copy.deepcopy(list(current_rows or []))
    canonical_ownership_bindings(current_rows)
    connection = copy.deepcopy(payload.get("generator_connection") or {})
    original_authored = authored_bindings(payload)
    authored_keys = set()
    for row in original_authored:
        key = binding_key(row)
        if key in authored_keys:
            raise GeneratorSlotOwnershipError(
                "generator_connection.authored_bindings contains duplicate "
                f"Generator slots: {key}"
            )
        authored_keys.add(key)
    # Authored rows are an append-only structural/history snapshot.  A repeat
    # refresh may have fewer current rows after a handoff, while a later export
    # may legitimately introduce a brand-new semantic slot.  Existing rows
    # never get overwritten merely because their live target pair changed.
    for row in current_rows:
        key = binding_key(row)
        if key not in authored_keys:
            original_authored.append(copy.deepcopy(row))
            authored_keys.add(key)
    connection["authored_bindings"] = original_authored
    connection["bindings"] = current_rows
    if relinquished_rows:
        history = list(connection.get("relinquished_bindings") or [])
        known = {
            canonical_fingerprint(row)
            for row in history
            if isinstance(row, dict)
        }
        for row in relinquished_rows:
            fingerprint = canonical_fingerprint(row)
            if fingerprint not in known:
                history.append(copy.deepcopy(row))
                known.add(fingerprint)
        connection["relinquished_bindings"] = history
    payload["generator_connection"] = connection
    payload["generator_binding_ownership"] = (
        build_generator_binding_ownership(current_rows)
    )
    payload["generator_slot_creation_provenance"] = (
        build_generator_slot_creation_provenance(
            original_authored,
            existing_contract=existing_creation_contract,
        )
    )
    return payload


def _pair(binding):
    projection = ownership_binding_projection(binding)
    return (
        projection["target_material_id"],
        projection["target_mesh_id"],
    )


def plan_live_binding_reconciliation(provider_records, live_bindings):
    """Resolve stale overlapping receipts against one pre-write live SPM.

    ``provider_records`` is a list of ``{"path": ..., "payload": ...}``
    mappings.  Mirrors collapse by exact provider identity.  The live document
    selects an owner only when exactly one provider has an exact claim for the
    live Material/Mesh pair.  Zero or multiple exact matches stay blocked;
    invocation order never selects a winner.
    """
    if not isinstance(provider_records, list):
        raise GeneratorSlotOwnershipError("provider_records must be a list")
    live_rows = canonical_ownership_bindings(list(live_bindings or []))
    live_by_slot = {binding_key(row): row for row in live_rows}

    providers = {}
    for ordinal, record in enumerate(provider_records):
        if not isinstance(record, dict) or not isinstance(
            record.get("payload"), dict
        ):
            raise GeneratorSlotOwnershipError(
                f"provider record #{ordinal + 1} is invalid"
            )
        path = str(record.get("path") or "")
        payload = record["payload"]
        identity = provider_identity(
            payload,
            relative_to=(Path(path).parent if path else None),
        )
        key = provider_key(identity)
        provider = providers.setdefault(
            key,
            {
                "identity": identity,
                "records": [],
                "claims": {},
            },
        )
        rows = current_bindings(payload)
        provider["records"].append({
            "path": path,
            "payload": payload,
            "current_bindings": rows,
        })
        for row in rows:
            slot_key = binding_key(row)
            pair = _pair(row)
            claims = provider["claims"].setdefault(slot_key, {})
            claims.setdefault(pair, []).append(copy.deepcopy(row))

    all_claimed_slots = sorted({
        slot_key
        for provider in providers.values()
        for slot_key in provider["claims"]
    })
    blocking = []
    owners = {}
    for slot_key in all_claimed_slots:
        live = live_by_slot.get(slot_key)
        if live is None:
            # The live SPM is authoritative: a disappeared semantic slot has
            # no current Atlas owner.  Creator provenance is retained by a
            # separate contract and is not resurrected here.
            owners[slot_key] = None
            continue
        live_pair = _pair(live)
        matches = []
        for key, provider in providers.items():
            rows = provider["claims"].get(slot_key, {}).get(live_pair) or []
            if rows:
                matches.append((key, rows))
        if not matches:
            claimed_rows = [
                row
                for provider in providers.values()
                for rows in provider["claims"].get(slot_key, {}).values()
                for row in rows
            ]
            source_pairs = []
            for row in claimed_rows:
                try:
                    source_pairs.append((
                        _integer(
                            row.get("source_material_id"),
                            "source_material_id",
                            positive=True,
                        ),
                        _integer(row.get("source_mesh_id"), "source_mesh_id"),
                    ))
                except GeneratorSlotOwnershipError:
                    source_pairs.append(None)
            if (
                source_pairs
                and all(pair == live_pair for pair in source_pairs)
            ):
                # Exact reversible provenance proves that cleanup restored the
                # authored pair.  The live slot is intentionally unowned; no
                # former provider is resurrected as a current owner.
                owners[slot_key] = None
                continue
        if len(matches) != 1:
            blocking.append({
                "reason": (
                    "live_pair_has_no_exact_provider_claim"
                    if not matches
                    else "live_pair_has_multiple_provider_claims"
                ),
                "generator_guid": slot_key[0],
                "slot_prefix": slot_key[1],
                "live_target_material_id": live_pair[0],
                "live_target_mesh_id": live_pair[1],
                "matching_providers": [
                    providers[key]["identity"] for key, _rows in matches
                ],
            })
            continue
        owner_key, rows = matches[0]
        # Metadata outside the four-field ownership projection is not owner
        # authority.  Pick a deterministic full row for compatibility output.
        chosen = sorted(
            rows,
            key=lambda row: json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )[0]
        owners[slot_key] = {
            "provider_key": owner_key,
            "binding": chosen,
        }

    if blocking:
        return {
            "contract": "atlas_live_generator_binding_reconciliation_plan",
            "schema_version": 1,
            "status": "blocked",
            "providers": providers,
            "live_bindings": live_rows,
            "owners": owners,
            "provider_updates": {},
            "blocking": blocking,
        }

    provider_updates = {}
    for key, provider in providers.items():
        desired = []
        relinquished = []
        desired_keys = set()
        for slot_key, owner in owners.items():
            if owner is None or owner["provider_key"] != key:
                continue
            desired.append(copy.deepcopy(owner["binding"]))
            desired_keys.add(slot_key)
        desired.sort(key=binding_key)

        seen_relinquished = set()
        for record in provider["records"]:
            for row in record["current_bindings"]:
                slot_key = binding_key(row)
                if slot_key in desired_keys:
                    continue
                owner = owners.get(slot_key)
                live = live_by_slot.get(slot_key)
                history = copy.deepcopy(row)
                history["ownership_state"] = "relinquished"
                history["reason"] = (
                    "live_spm_slot_missing"
                    if live is None
                    else "live_spm_exact_successor_binding"
                )
                if live is not None:
                    history["live_target_material_id"] = live[
                        "target_material_id"
                    ]
                    history["live_target_mesh_id"] = live["target_mesh_id"]
                if owner is not None:
                    history["successor_provider"] = copy.deepcopy(
                        providers[owner["provider_key"]]["identity"]
                    )
                fingerprint = canonical_fingerprint(history)
                if fingerprint not in seen_relinquished:
                    relinquished.append(history)
                    seen_relinquished.add(fingerprint)
        provider_updates[key] = {
            "identity": provider["identity"],
            "records": provider["records"],
            "current_bindings": desired,
            "relinquished_bindings": relinquished,
        }

    public_owners = []
    for slot_key, owner in sorted(owners.items()):
        live = live_by_slot.get(slot_key)
        public_owners.append({
            "generator_guid": slot_key[0],
            "slot_prefix": slot_key[1],
            "target_material_id": (
                live["target_material_id"] if live is not None else None
            ),
            "target_mesh_id": (
                live["target_mesh_id"] if live is not None else None
            ),
            "provider": (
                copy.deepcopy(providers[owner["provider_key"]]["identity"])
                if owner is not None
                else None
            ),
        })
    projection = {
        "providers": [
            provider_updates[key]["identity"]
            for key in sorted(provider_updates)
        ],
        "owners": public_owners,
    }
    return {
        "contract": "atlas_live_generator_binding_reconciliation_plan",
        "schema_version": 1,
        "status": "repairable",
        "providers": providers,
        "live_bindings": live_rows,
        "owners": owners,
        "provider_updates": provider_updates,
        "blocking": [],
        "fingerprint": canonical_fingerprint(projection),
    }


__all__ = [
    "CREATION_CONTRACT",
    "CREATION_VERSION",
    "GeneratorSlotOwnershipError",
    "MATERIAL_DEFAULT_MESH_ID",
    "OWNERSHIP_BASIS",
    "OWNERSHIP_CONTRACT",
    "OWNERSHIP_VERSION",
    "authored_bindings",
    "binding_key",
    "build_generator_binding_ownership",
    "build_generator_slot_creation_provenance",
    "canonical_creation_slots",
    "canonical_fingerprint",
    "canonical_ownership_bindings",
    "canonical_target_mesh_id",
    "creation_provenance_slot",
    "current_bindings",
    "manifest_with_binding_contracts",
    "ownership_binding_projection",
    "plan_live_binding_reconciliation",
    "provider_identity",
    "provider_key",
    "validate_generator_binding_ownership",
    "validate_generator_slot_creation_provenance",
]
