import hashlib
import http.server
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_mirror  # noqa: E402
import verify_mirror  # noqa: E402

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def catalog_for(files: dict[str, bytes], source_path: str = "providers/demo") -> bytes:
    manifest = {
        "provider_id": "demo",
        "source": {"path": source_path, "repo": "x/y"},
        "files": {name: sha(data) for name, data in files.items()},
    }
    return json.dumps({"name": "t", "schema_version": 1, "providers": [{"manifest": manifest}]}).encode()


class GitRepo:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.path), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, provider: bytes = b"print(1)\n", extra: dict[str, bytes] | None = None,
               catalog: bytes | None = None, message: str = "c") -> str:
        target = self.path / "providers" / "demo"
        target.mkdir(parents=True, exist_ok=True)
        (target / "provider.py").write_bytes(provider)
        if catalog is None:
            catalog = catalog_for({"provider.py": provider})
        (self.path / "catalog.json").write_bytes(catalog)
        for name, data in (extra or {}).items():
            (self.path / name).write_bytes(data)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


class MirrorTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.repo = GitRepo(self.tmp / "repo")
        self.out = self.tmp / "site"
        self.quiet = lambda message: None

    def build(self, **kwargs):
        kwargs.setdefault("channels", {"main": "main"})
        return build_mirror.build_site(self.repo.path, self.out, now=NOW, log=self.quiet, **kwargs)

    def site_fetch(self, root: Path | None = None):
        root = root or self.out

        def fetch(path: str) -> bytes:
            try:
                return (root / path).read_bytes()
            except OSError as exc:
                raise verify_mirror.FetchError(str(exc)) from exc

        return fetch


class BuildLayoutTests(MirrorTestCase):
    def test_layout_and_pointer(self):
        first = self.repo.commit(b"one\n", message="one")
        second = self.repo.commit(b"two\n", message="two")
        pointers = self.build()
        base = self.out / "v1" / "main"
        pointer = json.loads((base / "current").read_text())
        self.assertEqual(pointer, pointers["main"])
        self.assertEqual(
            set(pointer), {"channel", "commit", "catalog_sha256", "published_at", "history"}
        )
        self.assertEqual(pointer["channel"], "main")
        self.assertEqual(pointer["commit"], second)
        self.assertEqual(pointer["published_at"], "2026-11-07T12:00:00Z")
        catalog = (base / second / "catalog.json").read_bytes()
        self.assertEqual(pointer["catalog_sha256"], sha(catalog))
        self.assertEqual((base / second / "providers/demo/provider.py").read_bytes(), b"two\n")
        self.assertEqual((base / first / "providers/demo/provider.py").read_bytes(), b"one\n")

    def test_catalog_is_the_git_blob_byte_for_byte(self):
        odd = catalog_for({"provider.py": b"x\n"}).replace(b",", b" ,")
        commit = self.repo.commit(b"x\n", catalog=odd)
        self.build()
        self.assertEqual((self.out / "v1/main" / commit / "catalog.json").read_bytes(), odd)

    def test_history_is_newest_first_and_cut_at_retention(self):
        commits = [self.repo.commit(f"v{i}\n".encode(), message=str(i)) for i in range(5)]
        pointer = self.build(retention=3)["main"]
        self.assertEqual(pointer["history"], list(reversed(commits))[:3])
        self.assertEqual(pointer["history"][0], pointer["commit"])
        published = {p.name for p in (self.out / "v1/main").iterdir()}
        self.assertEqual(published, {"current", *pointer["history"]})

    def test_default_retention_is_twenty(self):
        self.assertEqual(build_mirror.RETENTION, 20)
        for i in range(22):
            self.repo.commit(f"v{i}\n".encode(), message=str(i))
        self.assertEqual(len(self.build()["main"]["history"]), 20)

    def test_history_follows_first_parent_only(self):
        self.repo.commit(b"base\n")
        self.repo.git("checkout", "-q", "-b", "side")
        side = self.repo.commit(b"side\n", message="side")
        self.repo.git("checkout", "-q", "main")
        self.repo.commit(b"main2\n", message="main2")
        self.repo.git("merge", "-q", "--no-ff", "-X", "theirs", "side", "-m", "merge")
        history = self.build()["main"]["history"]
        self.assertNotIn(side, history)
        self.assertEqual(len(history), 3)

    def test_channels_are_independent(self):
        self.repo.commit(b"main\n")
        self.repo.git("checkout", "-q", "-b", "beta")
        beta = self.repo.commit(b"beta\n", message="beta")
        pointers = self.build(channels={"main": "main", "beta": "beta"})
        self.assertEqual(pointers["beta"]["commit"], beta)
        self.assertNotEqual(pointers["main"]["commit"], beta)
        self.assertTrue((self.out / "v1/beta/current").is_file())

    def test_signature_file_is_copied_as_opaque_bytes(self):
        commit = self.repo.commit(extra={"catalog.json.sig": b"\x00\xffsig"})
        self.build()
        self.assertEqual((self.out / "v1/main" / commit / "catalog.json.sig").read_bytes(), b"\x00\xffsig")

    def test_commit_without_signature_publishes_none(self):
        commit = self.repo.commit()
        self.build()
        self.assertFalse((self.out / "v1/main" / commit / "catalog.json.sig").exists())

    def test_refuses_a_non_empty_output_directory(self):
        self.repo.commit()
        self.out.mkdir()
        (self.out / "stale").write_text("x")
        with self.assertRaises(build_mirror.MirrorError):
            self.build()


class BuildRefusalTests(MirrorTestCase):
    def test_head_with_a_bad_pin_fails_the_build(self):
        catalog = catalog_for({"provider.py": b"expected\n"})
        self.repo.commit(b"different\n", catalog=catalog)
        with self.assertRaisesRegex(build_mirror.MirrorError, "does not match its pin"):
            self.build()

    def test_older_commit_with_a_bad_pin_is_dropped_from_history(self):
        bad = self.repo.commit(b"different\n", catalog=catalog_for({"provider.py": b"expected\n"}))
        good = self.repo.commit(b"fine\n")
        pointer = self.build()["main"]
        self.assertEqual(pointer["history"], [good])
        self.assertFalse((self.out / "v1/main" / bad).exists())

    def test_missing_pinned_file_fails_the_build(self):
        catalog = catalog_for({"provider.py": b"a\n", "extra.py": b"b\n"})
        self.repo.commit(b"a\n", catalog=catalog)
        with self.assertRaisesRegex(build_mirror.MirrorError, "pinned file missing"):
            self.build()

    def test_unsafe_paths_are_refused(self):
        for bad in ("../x", "/abs", "a//b", "a/./b", ".hidden", "a\\b", ""):
            with self.subTest(path=bad), self.assertRaises(build_mirror.MirrorError):
                build_mirror.safe_relative_path(bad)

    def test_unsafe_pinned_path_fails_the_build(self):
        catalog = catalog_for({"../escape.py": b"a\n"})
        self.repo.commit(b"a\n", catalog=catalog)
        with self.assertRaisesRegex(build_mirror.MirrorError, "unsafe path"):
            self.build()

    def test_a_file_and_a_directory_sharing_a_path_is_a_clean_error(self):
        for files in ({"a": b"1", "a/b": b"2"}, {"a/b": b"2", "a": b"1"}, {"catalog.json": b"x"}):
            with self.subTest(files=sorted(files)):
                catalog = catalog_for(files, source_path="")
                with self.assertRaises(build_mirror.MirrorError):
                    build_mirror.catalog_pins(catalog)

    def test_a_colliding_pin_set_fails_the_build_without_a_traceback(self):
        catalog = catalog_for({"a": b"1", "a/b": b"2"}, source_path="")
        self.repo.commit(catalog=catalog)
        with self.assertRaisesRegex(build_mirror.MirrorError, "file and also used as a directory"):
            self.build()

    def test_oversized_blob_is_refused_before_it_is_read(self):
        self.repo.commit(b"x" * 64)
        calls = []
        real = build_mirror.git
        build_mirror.git = lambda *args: calls.append(args) or real(*args)
        self.addCleanup(setattr, build_mirror, "git", real)
        self.assertIsNone(build_mirror.blob_or_none(self.repo.path, "HEAD", "missing"))
        with self.assertRaisesRegex(build_mirror.MirrorError, "exceeds 16 bytes"):
            build_mirror.blob_or_none(self.repo.path, "HEAD", "providers/demo/provider.py", max_bytes=16)
        self.assertEqual(calls, [])

    def test_a_non_validation_error_on_an_older_commit_fails_the_build(self):
        self.repo.commit(b"one\n")
        self.repo.commit(b"two\n")
        real = build_mirror.commit_files
        calls = []

        def flaky(repo, commit):
            calls.append(commit)
            if len(calls) == 2:
                raise build_mirror.MirrorError("git exploded")
            return real(repo, commit)

        build_mirror.commit_files = flaky
        self.addCleanup(setattr, build_mirror, "commit_files", real)
        with self.assertRaisesRegex(build_mirror.MirrorError, "git exploded"):
            self.build()

    def test_a_validation_error_on_an_older_commit_is_skipped(self):
        self.repo.commit(b"broken\n", catalog=catalog_for({"provider.py": b"expected\n"}))
        self.repo.commit(b"two\n")
        self.assertEqual(len(self.build()["main"]["history"]), 1)

    def test_a_pinned_directory_is_a_validation_error(self):
        catalog = catalog_for({"sub": b"x"}, source_path="providers/demo")
        self.repo.commit(catalog=catalog)
        (self.repo.path / "providers/demo/provider.py").unlink()
        sub = self.repo.path / "providers/demo/sub"
        sub.mkdir()
        (sub / "inner").write_text("x")
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", "dir")
        with self.assertRaisesRegex(build_mirror.ValidationError, "not a file"):
            build_mirror.commit_files(self.repo.path, "HEAD")

    def test_excessive_pin_counts_are_refused_before_any_blob_is_read(self):
        original = build_mirror.MAX_PINS
        build_mirror.MAX_PINS = 3
        self.addCleanup(setattr, build_mirror, "MAX_PINS", original)
        catalog = catalog_for({f"f{i}.py": b"x" for i in range(4)})
        with self.assertRaisesRegex(build_mirror.ValidationError, "more than 3"):
            build_mirror.catalog_pins(catalog)

    def test_cumulative_commit_size_is_bounded_from_object_sizes(self):
        files = {f"f{i}.py": b"x" * 100 for i in range(4)}
        catalog = catalog_for(files)
        self.repo.commit(catalog=catalog)
        for name, data in files.items():
            (self.repo.path / "providers/demo" / name).write_bytes(data)
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", "files")
        original = build_mirror.MAX_COMMIT_BYTES
        build_mirror.MAX_COMMIT_BYTES = 350
        self.addCleanup(setattr, build_mirror, "MAX_COMMIT_BYTES", original)
        calls = []
        real = build_mirror.git
        build_mirror.git = lambda *args: calls.append(args) or real(*args)
        self.addCleanup(setattr, build_mirror, "git", real)
        with self.assertRaisesRegex(build_mirror.ValidationError, "in total"):
            build_mirror.commit_files(self.repo.path, "HEAD")
        self.assertEqual([c for c in calls if c[:2] == ("cat-file", "blob") and "f" in c[2]], [])

    def test_file_count_bound(self):
        self.repo.commit()
        with self.assertRaisesRegex(build_mirror.MirrorError, "files"):
            self.build(max_files=2)

    def test_site_size_bound(self):
        self.repo.commit()
        with self.assertRaisesRegex(build_mirror.MirrorError, "bytes"):
            self.build(max_site_bytes=10)

    def test_single_file_size_bound(self):
        self.repo.commit(b"x" * 64)
        original = build_mirror.MAX_FILE_BYTES
        build_mirror.MAX_FILE_BYTES = 16
        self.addCleanup(setattr, build_mirror, "MAX_FILE_BYTES", original)
        with self.assertRaisesRegex(build_mirror.MirrorError, "exceeds"):
            self.build()

    def test_catalog_that_is_not_a_catalog_is_refused(self):
        self.repo.commit(catalog=b"not json")
        with self.assertRaises(build_mirror.MirrorError):
            self.build()

    def test_unknown_ref_is_refused(self):
        self.repo.commit()
        with self.assertRaises(build_mirror.MirrorError):
            self.build(channels={"beta": "origin/beta"})

    def test_pins_of_the_real_catalog_are_collected(self):
        pins = build_mirror.catalog_pins((ROOT / "catalog.json").read_bytes())
        self.assertGreater(len(pins), 50)
        for path, digest in pins.items():
            self.assertEqual(sha((ROOT / path).read_bytes()), digest, path)


class VerifyTests(MirrorTestCase):
    def setUp(self):
        super().setUp()
        self.repo.commit(b"one\n", message="one")
        self.head = self.repo.commit(b"two\n", message="two")
        self.build()

    def verify(self, fetch=None, expected=None):
        return verify_mirror.verify_channel(
            fetch or self.site_fetch(), self.repo.path, "main", expected, attempts=2, delay=0, workers=4,
            ref="main",
        )

    def rewrite_pointer(self, **changes):
        path = self.out / "v1/main/current"
        pointer = json.loads(path.read_text())
        pointer.update(changes)
        path.write_text(json.dumps(pointer))
        return pointer

    def test_clean_site_verifies(self):
        self.assertEqual(self.verify(expected=self.head), [])

    def test_a_flipped_catalog_byte_is_caught_by_the_blob_check(self):
        path = self.out / "v1/main" / self.head / "catalog.json"
        data = bytearray(path.read_bytes())
        data[10] ^= 0x01
        path.write_bytes(bytes(data))
        errors = self.verify(expected=self.head)
        self.assertTrue(any("catalog.json" in e and "git blob" in e for e in errors), errors)

    def test_a_flipped_catalog_byte_in_an_older_commit_is_caught(self):
        older = json.loads((self.out / "v1/main/current").read_text())["history"][1]
        path = self.out / "v1/main" / older / "catalog.json"
        path.write_bytes(path.read_bytes() + b" ")
        self.assertTrue(any(older in e for e in self.verify(expected=self.head)))

    def test_a_flipped_bundle_byte_is_caught_by_the_pin(self):
        path = self.out / "v1/main" / self.head / "providers/demo/provider.py"
        path.write_bytes(b"twp\n")
        errors = self.verify(expected=self.head)
        self.assertTrue(any("catalog pin" in e for e in errors), errors)

    def test_a_missing_file_is_reported(self):
        (self.out / "v1/main" / self.head / "providers/demo/provider.py").unlink()
        self.assertTrue(any("provider.py" in e for e in self.verify(expected=self.head)))

    def test_pointer_catalog_hash_must_match_git(self):
        path = self.out / "v1/main/current"
        pointer = json.loads(path.read_text())
        pointer["catalog_sha256"] = "0" * 64
        path.write_text(json.dumps(pointer))
        self.assertTrue(any("catalog_sha256" in e for e in self.verify(expected=self.head)))

    def test_history_with_a_commit_from_another_channel_is_rejected(self):
        self.repo.git("checkout", "-q", "-b", "beta", "HEAD~1")
        beta_only = self.repo.commit(b"beta only\n", message="beta")
        self.repo.git("checkout", "-q", "main")
        history = json.loads((self.out / "v1/main/current").read_text())["history"]
        self.rewrite_pointer(history=[history[0], beta_only, history[1]])
        errors = self.verify(expected=self.head)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("first-parent chain", errors[0])
        self.assertIn(beta_only, errors[0])

    def test_history_out_of_order_is_rejected(self):
        history = json.loads((self.out / "v1/main/current").read_text())["history"]
        self.assertGreater(len(history), 1)
        self.rewrite_pointer(history=[history[1], history[0]], commit=history[1])
        errors = self.verify(expected=history[1])
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("out of order", errors[0])

    def test_omitting_a_healthy_commit_is_rejected(self):
        third = self.repo.commit(b"three\n", message="three")
        out = self.tmp / "site3"
        build_mirror.build_site(self.repo.path, out, channels={"main": "main"}, now=NOW, log=self.quiet)
        path = out / "v1/main/current"
        pointer = json.loads(path.read_text())
        self.assertEqual(len(pointer["history"]), 3)
        pointer["history"] = [pointer["history"][0], pointer["history"][2]]
        path.write_text(json.dumps(pointer))
        errors = self.verify(self.site_fetch(out), expected=third)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("differs from the retained history", errors[0])

    def test_a_pointer_reduced_to_the_head_is_rejected(self):
        self.rewrite_pointer(history=[self.head])
        errors = self.verify(expected=self.head)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("missing", errors[0])

    def test_an_extra_entry_beyond_the_window_is_rejected(self):
        history = json.loads((self.out / "v1/main/current").read_text())["history"]
        self.rewrite_pointer(history=history + [history[-1]])
        self.assertEqual(len(self.verify(expected=self.head)), 1)

    def test_a_commit_that_failed_validation_is_legitimately_absent(self):
        bad = self.repo.commit(b"broken\n", catalog=catalog_for({"provider.py": b"expected\n"}), message="bad")
        newest = self.repo.commit(b"fine\n", message="fine")
        out = self.tmp / "site4"
        pointer = build_mirror.build_site(
            self.repo.path, out, channels={"main": "main"}, now=NOW, log=self.quiet
        )["main"]
        self.assertNotIn(bad, pointer["history"])
        self.assertEqual(self.verify(self.site_fetch(out), expected=newest), [])

    def test_a_dropped_commit_that_is_healthy_in_git_is_rejected_even_if_published_consistently(self):
        newest = self.repo.commit(b"fine\n", message="fine")
        out = self.tmp / "site5"
        build_mirror.build_site(self.repo.path, out, channels={"main": "main"}, now=NOW, log=self.quiet)
        path = out / "v1/main/current"
        pointer = json.loads(path.read_text())
        dropped = pointer["history"].pop(1)
        path.write_text(json.dumps(pointer))
        (out / "v1/main" / dropped / "catalog.json").unlink()
        self.assertTrue(self.verify(self.site_fetch(out), expected=newest))

    def test_a_pointer_that_lags_the_build_fails(self):
        errors = self.verify(expected="a" * 40)
        self.assertTrue(any("expected" in e for e in errors), errors)

    def test_a_newer_deploy_than_the_build_is_accepted(self):
        newer = self.repo.commit(b"three\n", message="three")
        shutil_out = self.tmp / "site2"
        build_mirror.build_site(
            self.repo.path, shutil_out, channels={"main": "main"}, now=NOW, log=self.quiet
        )
        self.assertEqual(self.verify(self.site_fetch(shutil_out), expected=self.head), [])
        self.assertEqual(
            json.loads((shutil_out / "v1/main/current").read_text())["commit"], newer
        )

    def test_malformed_pointers_are_rejected(self):
        good = json.loads((self.out / "v1/main/current").read_text())
        for mutate in (
            lambda p: p.update(commit="x"),
            lambda p: p.update(history=[]),
            lambda p: p.update(history=list(reversed(p["history"]))),
            lambda p: p.update(channel="beta"),
            lambda p: p.pop("published_at"),
            lambda p: p.update(extra=1),
        ):
            pointer = json.loads(json.dumps(good))
            mutate(pointer)
            with self.subTest(pointer=pointer), self.assertRaises(build_mirror.MirrorError):
                verify_mirror.parse_pointer("main", json.dumps(pointer).encode())

    def test_exit_status_reflects_the_result(self):
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def __init__(inner, *a, **kw):
                super().__init__(*a, directory=str(self.out), **kw)

            def log_message(inner, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        args = ["--base-url", url, "--repo", str(self.repo.path),
                "--heads", json.dumps({"main": self.head}), "--attempts", "1", "--delay", "0",
                "--ref-template", "{channel}"]
        self.assertEqual(verify_mirror.main(args), 0)
        path = self.out / "v1/main" / self.head / "catalog.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self.assertEqual(verify_mirror.main(args), 1)


class FetcherTests(unittest.TestCase):
    def test_every_request_has_a_unique_cache_buster_and_no_cache_header(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(inner):
                seen.append((inner.path, inner.headers.get("Cache-Control")))
                inner.send_response(200)
                inner.end_headers()
                inner.wfile.write(b"ok")

            def log_message(inner, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        fetch = verify_mirror.http_fetcher(f"http://127.0.0.1:{server.server_address[1]}/site/")
        self.assertEqual(fetch("v1/main/current"), b"ok")
        self.assertEqual(fetch("v1/main/current"), b"ok")
        self.assertEqual(len(seen), 2)
        for path, cache_control in seen:
            self.assertTrue(path.startswith("/site/v1/main/current?cb="), path)
            self.assertEqual(cache_control, "no-cache")
        self.assertNotEqual(seen[0][0], seen[1][0])

    def test_plain_http_to_a_real_host_is_refused(self):
        with self.assertRaises(build_mirror.MirrorError):
            verify_mirror.http_fetcher("http://example.invalid/")


class WorkflowTests(unittest.TestCase):
    """Static guards on the publish workflow; Actions cannot run locally."""

    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / ".github/workflows/pages-mirror.yml").read_text()

    def test_every_action_is_pinned_by_full_sha_with_a_version_comment(self):
        import re

        uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", self.text, re.M)
        self.assertGreater(len(uses), 0)
        for ref, rest in uses:
            with self.subTest(ref=ref):
                self.assertRegex(ref, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
                self.assertRegex(rest, r"^\s+#\s*v\d+(\.\d+)*\s*$")

    def test_never_triggers_on_pull_requests(self):
        triggers = self.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        for forbidden in ("pull_request", "pull_request_target", "workflow_run"):
            self.assertNotIn(forbidden, triggers)
        self.assertIn("push:", triggers)
        self.assertIn("workflow_dispatch:", triggers)

    def test_only_main_and_beta_deploy(self):
        self.assertIn("branches: [main, beta]", self.text)
        self.assertIn("refs/heads/main", self.text)
        self.assertIn("refs/heads/beta", self.text)

    def test_no_wide_permissions(self):
        self.assertNotIn("write-all", self.text)
        self.assertNotIn("contents: write", self.text)


if __name__ == "__main__":
    unittest.main()
