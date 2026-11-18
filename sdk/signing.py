"""Signing and checking the signed plugins feed.

``sign`` stamps the unsigned body that ``build-signed-feed`` produces with the four signed
fields (``signed_version``, ``not_before``, ``expires`` and ``source_commit``), signs the exact
bytes with the CI signing key and writes ``signed/<channel>/catalog.json`` and its detached
envelope ``catalog.json.sig``. ``verify`` runs the host's own verifier over a written pair.

The verifier is a byte-for-byte copy of the host's (``sdk/host_signing.py``), so a pair that
passes here passes there, and a drift test keeps the copy honest. The only thing trusted without
proof is the root public key below. It signs the key file (``keys/catalog-signing-keys.json``),
which lists the CI signing key; it never signs a catalog and its private half is never here.

The CI signing key reaches this module as PEM text in an environment variable and nowhere else.
It is never written to disk, never put on a command line and never part of an error message.
"""

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import cli

KEY_FILE_PATH = "keys/catalog-signing-keys.json"
SIGNED_DIR = "signed"
BODY_NAME = "catalog.json"
ENVELOPE_NAME = "catalog.json.sig"
DEFAULT_VALIDITY_DAYS = 90
MAX_VALIDITY_DAYS = 366
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_STAMPED_FIELDS = ("signed_version", "not_before", "expires", "source_commit")

# The root the hosts trust, as the release ships it: key id, then the raw 32-byte Ed25519 public
# key in base64. A test pins this to the host's own table.
ROOTS = {
    "d2edde7047b7b2fe": base64.b64decode("39GrDDEActD1h3UWLIQljZzTXHoBZIYggT7ZRBPYpCU="),
}


def _host_signing():
    # Imported late: it needs the cryptography package, which `sdk validate` does not.
    from . import host_signing

    return host_signing


def _cryptography():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise cli.CatalogError(
            "signing needs the cryptography package: pip install --require-hashes -r sdk/requirements-signing.txt"
        ) from exc
    return serialization, ed25519


def signed_paths(root, channel):
    directory = Path(root) / SIGNED_DIR / channel
    return directory / BODY_NAME, directory / ENVELOPE_NAME


def key_id(public_key):
    """The first 16 hex digits of SHA-256 over the raw 32-byte public key."""
    return hashlib.sha256(public_key).hexdigest()[:16]


def format_time(moment):
    return moment.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def load_private_key(pem):
    """An Ed25519 private key from unencrypted PKCS8 PEM. Nothing about the key is ever in the error."""
    serialization, ed25519 = _cryptography()
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception:
        raise cli.CatalogError("the signing key is not a readable unencrypted PEM private key") from None
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise cli.CatalogError("the signing key is not an Ed25519 key")
    return key


def private_key_from_env(name):
    """Read the key from an environment variable and drop the variable from this process."""
    value = os.environ.pop(name, None)
    if not value or not value.strip():
        raise cli.CatalogError(f"the environment variable {name} holds no signing key")
    return load_private_key(value.encode("utf-8"))


def public_key_bytes(private_key):
    serialization, _ = _cryptography()
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def read_key_file(root):
    path = Path(root) / KEY_FILE_PATH
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise cli.CatalogError(f"missing file: {path}") from exc


def parse_key_file(data, roots=None):
    """The parsed key file, refused unless a trusted root signed it."""
    host = _host_signing()
    roots = ROOTS if roots is None else roots
    try:
        key_file = host._parse_key_file(host._as_object(data, "key file"))
    except host.WireFormatError as exc:
        raise cli.CatalogError(f"the key file is malformed: {exc}") from exc
    root = roots.get(key_file.root_key_id)
    if root is None:
        raise cli.CatalogError(f"the key file names root {key_file.root_key_id}, which is not trusted")
    if not host._ed25519_verify(root, key_file.signature, host.statement_for(key_file)):
        raise cli.CatalogError("the key file is not signed by its root")
    return key_file


def _loads_body(data):
    try:
        document = json.loads(data)
    except ValueError as exc:
        raise cli.CatalogError(f"the signed body is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise cli.CatalogError("the signed body must be a JSON object")
    return document


def _validity(days):
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_VALIDITY_DAYS:
        raise cli.CatalogError(f"validity must be 1 to {MAX_VALIDITY_DAYS} days")
    return timedelta(days=days)


def stamp(body, *, now, validity_days=DEFAULT_VALIDITY_DAYS, source_commit=None, previous_version=None):
    """The body with the four signed fields set, as the bytes that get signed.

    ``previous_version`` is the signed_version of the file being replaced. A new one has to be
    higher, so a signing runner whose clock reads earlier than the last signing cannot roll a
    channel back with a valid signature.
    """
    document = dict(body)
    if source_commit is not None:
        if not isinstance(source_commit, str) or not _COMMIT_RE.fullmatch(source_commit):
            raise cli.CatalogError("source_commit must be a full 40 digit lowercase hex commit")
        document["source_commit"] = source_commit
    elif not isinstance(document.get("source_commit"), str) or not _COMMIT_RE.fullmatch(document["source_commit"]):
        raise cli.CatalogError("the body carries no valid source_commit")
    signed_version = int(now.timestamp())
    if previous_version is not None and signed_version <= previous_version:
        raise cli.CatalogError(
            f"signed_version {signed_version} would not be higher than the current {previous_version}; "
            "the runner clock reads earlier than the last signing"
        )
    document["signed_version"] = signed_version
    document["not_before"] = format_time(now)
    document["expires"] = format_time(now + _validity(validity_days))
    return cli.dump_json(document).encode("utf-8")


def sign_bytes(data, private_key):
    """The detached envelope for ``data``, as the bytes written beside it."""
    public = public_key_bytes(private_key)
    envelope = {
        "schema_version": 1,
        "signatures": [
            {
                "algorithm": "ed25519",
                "key_id": key_id(public),
                "signature": base64.b64encode(private_key.sign(data)).decode("ascii"),
            }
        ],
    }
    return cli.dump_json(envelope).encode("utf-8")


def verify_pair(data, envelope, key_file, *, channel, now, roots=None, baked=False):
    """The host verifier's result for one signed body. ``baked`` skips expiry and the replay counter."""
    host = _host_signing()
    return host.verify_catalog(
        data,
        envelope,
        key_file,
        roots=ROOTS if roots is None else roots,
        channel=channel,
        now=now,
        baked=baked,
    )


def verify_files(root, channel, *, now, roots=None, baked=False):
    body_path, envelope_path = signed_paths(root, channel)
    for path in (body_path, envelope_path):
        if not path.is_file():
            raise cli.CatalogError(f"missing file: {path}")
    return verify_pair(
        body_path.read_bytes(),
        envelope_path.read_bytes(),
        read_key_file(root),
        channel=channel,
        now=now,
        roots=roots,
        baked=baked,
    )


def _current_version(root, channel):
    body_path, _ = signed_paths(root, channel)
    if not body_path.is_file():
        return None
    version = _loads_body(body_path.read_bytes()).get("signed_version")
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def _refuse_dropped_revocations(body_path, new_body, channel):
    """A channel that published revocations never silently publishes a body without the field.

    Lifting every entry is allowed, but it is written down: keep revocations.json as an empty
    array. Losing the file, or building from a tree that never had it, would otherwise lift
    every revocation on the next signing without anyone choosing that.
    """
    if not body_path.is_file() or "revocations" in new_body:
        return
    held = _loads_body(body_path.read_bytes()).get("revocations")
    if isinstance(held, list) and held:
        raise cli.CatalogError(
            f"the signed {channel} body carries {len(held)} revocation(s) and the new body has no revocations "
            f"field: restore {cli.REVOCATIONS_FILE}, or set it to [] to lift every entry on purpose"
        )


def sign(root, channel, private_key, *, now, source_commit=None, renew=False,
         validity_days=DEFAULT_VALIDITY_DAYS, roots=None):
    """Sign a channel's feed and write the pair. Nothing is written unless the host verifier accepts it.

    A new signing builds the body from the tree and needs the commit it was built from. A renewal
    keeps the body it renews, source_commit included, and only restamps the dates.
    Returns the verification result.
    """
    root = Path(root).resolve()
    if channel not in cli.SIGNED_FEED_CHANNELS:
        raise cli.CatalogError(f"channel must be one of {', '.join(cli.SIGNED_FEED_CHANNELS)}")
    key_file = read_key_file(root)
    key_file_doc = parse_key_file(key_file, roots)
    signer = key_id(public_key_bytes(private_key))
    if signer not in {key.key_id for key in key_file_doc.keys}:
        raise cli.CatalogError(f"signing key {signer} is not listed in {KEY_FILE_PATH}")
    if signer in {revocation.key_id for revocation in key_file_doc.revocations}:
        raise cli.CatalogError(f"signing key {signer} is revoked in {KEY_FILE_PATH}")

    body_path, envelope_path = signed_paths(root, channel)
    if renew:
        if not body_path.is_file():
            raise cli.CatalogError(f"nothing to renew: {body_path} does not exist")
        body = _loads_body(body_path.read_bytes())
        if body.get("channel") != channel:
            raise cli.CatalogError(f"the signed body is for {body.get('channel')!r}, not {channel!r}")
        for field in ("signed_version", "not_before", "expires"):
            body.pop(field, None)
        if source_commit is not None and source_commit != body.get("source_commit"):
            raise cli.CatalogError("a renewal keeps the source_commit of the body it renews")
        source_commit = None
    else:
        if source_commit is None:
            raise cli.CatalogError("signing needs --source-commit, the commit the body is built from")
        body = cli.build_signed_feed(root, channel)
        _refuse_dropped_revocations(body_path, body, channel)
    data = stamp(
        body,
        now=now,
        validity_days=validity_days,
        source_commit=source_commit,
        previous_version=_current_version(root, channel),
    )
    envelope = sign_bytes(data, private_key)
    result = verify_pair(data, envelope, key_file, channel=channel, now=now, roots=roots)
    if not result.ok or result.signer_key_id != signer:
        raise cli.CatalogError(f"the host verifier refuses the new signature: {result.status.value}: {result.detail}")
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_bytes(data)
    envelope_path.write_bytes(envelope)
    return result


def utc_now():
    return datetime.now(timezone.utc)


def parse_now(text):
    try:
        return datetime.strptime(text, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise cli.CatalogError(f"--now must look like 2026-12-31T00:00:00Z: {exc}") from exc
