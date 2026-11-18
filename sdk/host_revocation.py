# This file is an excerpt of bazarr/provider_hub/revocation.py from the Bazarr project at 5e07a2b27b7604791bb810b7cc5b54a9ebfdb261.
# Bazarr is licensed under GPL-3.0. Its license file is LICENSE at that commit of that project.
# Only the pure parsing of the revocations list is copied, each definition byte for byte. tests/test_host_revocation.py checks that.
"""How a host reads the ``revocations`` list of a signed catalog body: the shape rules, nothing else."""
from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Any, Mapping

MAX_ENTRIES = 2000
MAX_REASON = 300
_MAX_VERSION = 64
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", re.ASCII)
_ENTRY_FIELDS = frozenset({"id", "version", "reason", "revoked_at"})


class RevocationFormatError(ValueError):
    """The revocation list of a signed catalog does not have the expected shape."""


@dataclass(frozen=True)
class Revocation:
    plugin_id: str
    # None revokes every release of the plugin.
    version: str | None = None
    reason: str | None = None
    revoked_at: str | None = None

    def as_record(self) -> dict[str, str]:
        optional = {"version": self.version, "reason": self.reason, "revoked_at": self.revoked_at}
        return {"id": self.plugin_id, **{name: value for name, value in optional.items() if value is not None}}


def _text(entry: Mapping[str, Any], name: str, limit: int) -> str:
    value = entry[name]
    if not isinstance(value, str):
        raise RevocationFormatError(f"a revocation {name} must be text")
    value = " ".join(value.split())
    if not value:
        raise RevocationFormatError(f"a revocation {name} must not be blank")
    if len(value) > limit:
        if name != "reason":
            raise RevocationFormatError(f"a revocation {name} is longer than {limit} characters")
        value = value[: limit - 3] + "..."
    return value


def _has_text(entry: Mapping[str, Any], name: str) -> bool:
    """Whether an optional note is given. A blank reason says nothing, which is not an error."""
    value = entry.get(name)
    return name in entry and not (isinstance(value, str) and not value.strip())


def parse_entry(entry: Any) -> Revocation:
    if not isinstance(entry, Mapping):
        raise RevocationFormatError("each revocation must be an object")
    unknown = sorted(str(name) for name in entry if name not in _ENTRY_FIELDS)
    if unknown:
        raise RevocationFormatError(f"a revocation has a field this host does not know: {', '.join(unknown)[:60]}")
    plugin_id = entry.get("id")
    if not isinstance(plugin_id, str) or not _ID_RE.fullmatch(plugin_id):
        raise RevocationFormatError("each revocation needs the id of the plugin it revokes, in the plugin id format")
    revoked_at = _text(entry, "revoked_at", _MAX_VERSION) if "revoked_at" in entry else None
    if revoked_at is not None and not _TIMESTAMP_RE.fullmatch(revoked_at):
        raise RevocationFormatError("a revocation revoked_at must look like 2026-12-31T00:00:00Z")
    return Revocation(
        plugin_id=plugin_id,
        version=_text(entry, "version", _MAX_VERSION) if "version" in entry else None,
        reason=_text(entry, "reason", MAX_REASON) if _has_text(entry, "reason") else None,
        revoked_at=revoked_at,
    )


def parse(value: Any) -> tuple[Revocation, ...]:
    """The list a verified catalog body carries. Absent means none. Any entry that cannot be read raises.

    A host cannot tell what an unreadable entry would have revoked, so the caller refuses the whole
    catalog instead of skipping the entry.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RevocationFormatError("revocations must be a list")
    if len(value) > MAX_ENTRIES:
        raise RevocationFormatError(f"revocations holds at most {MAX_ENTRIES} entries")
    return tuple(parse_entry(item) for item in value)
