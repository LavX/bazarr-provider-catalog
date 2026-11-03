"""Released Bazarr+ hosts' own manifest validators, run against `providers`.

Hosts up to 2.7 read only the `providers` list, validate nothing when they
refresh the catalog, and offer the highest version of an id as an update. An
entry they cannot load is therefore only discovered on a user's machine. Each
package here is a copy of bazarr/provider_hub/{__init__,manifest,migration}.py at
one released commit, with the transformations recorded in its environment.json,
plus the host-side facts the validator reads at run time: the built-in provider
module names and the settings sections. scripts/snapshot_released_host.py writes them.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import types
from dataclasses import dataclass
from pathlib import Path

HOSTS_DIR = Path(__file__).resolve().parent
RELEASED_HOSTS = ("bazarr_v2_6_2", "bazarr_v2_7_0", "bazarr_v2_7_2")


@dataclass(frozen=True)
class ReleasedHost:
    name: str
    environment: dict
    manifest: types.ModuleType
    migration: types.ModuleType


def git_blob_id(data: bytes) -> str:
    """The id `git hash-object` gives these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load_host(name: str) -> ReleasedHost:
    environment = json.loads((HOSTS_DIR / name / "environment.json").read_text(encoding="utf-8"))
    return ReleasedHost(
        name=name,
        environment=environment,
        manifest=importlib.import_module(f"{__name__}.{name}.manifest"),
        migration=importlib.import_module(f"{__name__}.{name}.migration"),
    )


def built_in_provider_ids(host: ReleasedHost) -> set[str]:
    """service._built_in_provider_ids() with no hub plugins registered."""
    return (
        set(host.environment["built_in_provider_modules"])
        | set(host.migration.MIGRATED_BUILT_IN_PROVIDER_IDS)
        | set(host.migration.RETIRED_BUILT_IN_PROVIDER_IDS)
    )


def validate_for_host(host: ReleasedHost, manifest: dict) -> None:
    """Validate as the host does for an entry from its trusted official source."""
    provider_id = str(manifest.get("provider_id") or "") if isinstance(manifest, dict) else ""
    allowed = host.migration.validation_built_in_provider_ids(provider_id, built_in_provider_ids(host), True)
    host.manifest.validate_manifest(manifest, built_in_provider_ids=allowed)


def released_host_errors(manifests) -> list[str]:
    """One line per (host, manifest) the host would refuse."""
    errors = []
    for name in RELEASED_HOSTS:
        host = load_host(name)
        for manifest in manifests:
            try:
                validate_for_host(host, manifest)
            except Exception as error:
                label = manifest.get("provider_id") if isinstance(manifest, dict) else None
                errors.append(f"{name} refuses {label or '?'}: {error}")
    return errors
