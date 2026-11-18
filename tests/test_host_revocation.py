"""The vendored copy of the host's revocation parser, and the feed builder checked against it."""

import ast
import json
import os
import tempfile
import unittest
from pathlib import Path

from sdk import cli, host_revocation, released_hosts

ROOT = Path(__file__).resolve().parents[1]
HUB_NOTE = "set BAZARR_PROVIDER_HUB_DIR to a bazarr/provider_hub checkout"
META = json.loads((ROOT / "sdk" / "host_revocation.json").read_text(encoding="utf-8"))


def definitions(source):
    """Top-level assignments, classes and functions of a module by name, each with its exact source text.

    A decorated definition starts at its first decorator, so ``@dataclass`` is part of ``Revocation``.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        else:
            continue
        start = min([node.lineno] + [decorator.lineno for decorator in getattr(node, "decorator_list", [])])
        found[name] = "\n".join(lines[start - 1:node.end_lineno])
    return found


class VendoredParserTests(unittest.TestCase):
    def test_the_copy_has_not_drifted_from_what_was_vendored(self):
        vendored = (ROOT / "sdk" / "host_revocation.py").read_bytes()
        self.assertEqual(released_hosts.git_blob_id(vendored), META["vendored_blob"])
        self.assertRegex(META["bazarr_commit"], r"^[a-f0-9]{40}$")
        self.assertEqual(set(definitions(vendored.decode("utf-8"))), set(META["definitions"]))
        lines = vendored.decode("utf-8").splitlines()
        self.assertTrue(all(line.startswith("#") for line in lines[:META["notice_lines"]]))

    def host_source(self):
        return (Path(os.environ["BAZARR_PROVIDER_HUB_DIR"]) / "revocation.py").read_text(encoding="utf-8")

    def test_the_decorator_is_part_of_the_compared_definition(self):
        mine = definitions((ROOT / "sdk" / "host_revocation.py").read_text(encoding="utf-8"))
        self.assertTrue(mine["Revocation"].startswith("@dataclass(frozen=True)\nclass Revocation"))

    @unittest.skipUnless(os.environ.get("BAZARR_PROVIDER_HUB_DIR"), HUB_NOTE)
    def test_every_vendored_definition_matches_the_host_checkout(self):
        host_definitions = definitions(self.host_source())
        mine = definitions((ROOT / "sdk" / "host_revocation.py").read_text(encoding="utf-8"))
        for name in META["definitions"]:
            with self.subTest(name):
                self.assertEqual(mine[name], host_definitions[name])

    @unittest.skipUnless(os.environ.get("BAZARR_PROVIDER_HUB_DIR"), HUB_NOTE)
    def test_the_host_file_is_the_one_that_was_vendored(self):
        # Fails on any change of the host file, the parser or not. The test above says whether the parser moved.
        self.assertEqual(released_hosts.git_blob_id(self.host_source().encode("utf-8")), META["source_blob"])


class BuilderAgainstHostParserTests(unittest.TestCase):
    """Whatever the builder accepts, the host reads back unchanged. The builder may be stricter than the host, never looser."""

    CANDIDATES = [
        [],
        [{"id": "example"}],
        [{"id": "example", "version": "1.2.0", "reason": "It sends credentials.", "revoked_at": "2026-12-01T00:00:00Z"}],
        [{"id": "a" * 64, "version": "9" * 64, "reason": "r" * 300}],
        [{"id": "zeta"}, {"id": "alpha", "version": "2"}, {"id": "alpha"}, {"id": "alpha", "version": "1"}],
        [{"id": "example", "reason": "  spaced   out  "}],
        [{"id": "example", "version": "1.0-é", "reason": "Hibás"}],
        [{"id": "example\n"}],
        [{"id": " example"}],
        [{"id": "Example"}],
        [{"id": "example", "version": ""}],
        [{"id": "example", "version": None}],
        [{"id": "example", "version": "1" * 65}],
        [{"id": "example", "reason": "r" * 301}],
        [{"id": "example", "reason": ""}],
        [{"id": "example", "reason": None}],
        [{"id": "example", "revoked_at": "2026-12-01"}],
        [{"id": "example", "revoked_at": "2026-12-01T00:00:00Z\n"}],
        [{"id": "example", "extra": 1}],
        ["example"],
        [{"id": "example", "version": "1\x00"}],
        [{"id": "example", "version": "1\ud800"}],
    ]

    def test_the_builder_and_the_host_agree_on_every_candidate(self):
        for data in self.CANDIDATES:
            with self.subTest(data=repr(data)[:80]), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / cli.REVOCATIONS_FILE).write_text(json.dumps(data), encoding="utf-8")
                try:
                    built = cli.load_revocations(tmp)
                except cli.CatalogError:
                    continue
                parsed = host_revocation.parse(built)
                self.assertEqual([item.as_record() for item in parsed], built)
                self.assertEqual(len(built), len(data))

    def test_the_builder_is_never_looser_than_the_host(self):
        for data in self.CANDIDATES:
            try:
                host_revocation.parse(data)
                host_accepts = True
            except host_revocation.RevocationFormatError:
                host_accepts = False
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / cli.REVOCATIONS_FILE).write_text(json.dumps(data), encoding="utf-8")
                try:
                    cli.load_revocations(tmp)
                    builder_accepts = True
                except cli.CatalogError:
                    builder_accepts = False
            if builder_accepts:
                self.assertTrue(host_accepts, f"the builder accepts what the host refuses: {data!r}")

    def test_the_host_parser_is_run_over_the_builder_output(self):
        with self.assertRaisesRegex(cli.CatalogError, "a host would refuse"):
            cli.check_host_reads([{"id": "Example"}])
        with self.assertRaisesRegex(cli.CatalogError, "a host would refuse"):
            cli.check_host_reads({"id": "example"})
        cli.check_host_reads(None)
        cli.check_host_reads([])


if __name__ == "__main__":
    unittest.main()
