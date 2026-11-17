import contextlib
import hashlib
import http.server
import io
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
import check_advance  # noqa: E402
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

    def blob(self, commit: str, path: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(self.path), "cat-file", "blob", f"{commit}:{path}"], check=True, capture_output=True
        ).stdout

    def commit(self, provider: bytes = b"print(1)\n", extra: dict[str, bytes] | None = None,
               catalog: bytes | None = None, message: str = "c") -> str:
        target = self.path / "providers" / "demo"
        target.mkdir(parents=True, exist_ok=True)
        (target / "provider.py").write_bytes(provider)
        if catalog is None:
            catalog = catalog_for({"provider.py": provider})
        (self.path / "catalog.json").write_bytes(catalog)
        for name, data in (extra or {}).items():
            (self.path / name).parent.mkdir(parents=True, exist_ok=True)
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

    def test_default_retention_is_forty(self):
        self.assertEqual(build_mirror.RETENTION, 40)
        for i in range(42):
            self.repo.commit(f"v{i}\n".encode(), message=str(i))
        self.assertEqual(len(self.build()["main"]["history"]), 40)

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

    def test_the_signed_files_are_copied_as_opaque_bytes_under_their_own_paths(self):
        source = self.repo.commit(b"source\n", message="source")
        body = json.dumps({"source_commit": source, "providers": []}, indent=1).encode() + b"\n"
        signed = {
            "signed/main/catalog.json": body,
            "signed/main/catalog.json.sig": b"\x00\xffsig",
            "keys/catalog-signing-keys.json": b"keys \xff",
        }
        commit = self.repo.commit(extra=signed)
        self.build()
        for path, data in signed.items():
            self.assertEqual((self.out / "v1/main" / commit / path).read_bytes(), data, path)

    def test_only_the_channels_own_signed_files_are_published(self):
        commit = self.repo.commit(extra={"signed/beta/catalog.json": b"beta", "catalog.json.sig": b"legacy"})
        self.build()
        self.assertFalse((self.out / "v1/main" / commit / "signed/beta").exists())
        self.assertFalse((self.out / "v1/main" / commit / "catalog.json.sig").exists())

    def test_a_commit_without_signed_files_publishes_none(self):
        commit = self.repo.commit()
        self.build()
        self.assertFalse((self.out / "v1/main" / commit / "signed").exists())
        self.assertFalse((self.out / "v1/main" / commit / "keys").exists())

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
        for bad in ("../x", "/abs", "a//b", "a/./b", ".hidden", "a\\b", "", "f.py\n", "a/f.py\n"):
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

        def flaky(repo, commit, channel):
            calls.append(commit)
            if len(calls) == 2:
                raise build_mirror.MirrorError("git exploded")
            return real(repo, commit, channel)

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
            build_mirror.commit_files(self.repo.path, "HEAD", "main")

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
            build_mirror.commit_files(self.repo.path, "HEAD", "main")
        self.assertEqual([c for c in calls if c[1:3] == ("cat-file", "blob") and "/f" in c[3]], [])

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
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verify_mirror.main(args), 0)
            path = self.out / "v1/main" / self.head / "catalog.json"
            path.write_bytes(path.read_bytes() + b"\n")
            self.assertEqual(verify_mirror.main(args), 1)


def plugin_files(plugin_id: str, files: dict[str, bytes]) -> dict[str, bytes]:
    """The tree of one plugin: its files and a plugin.json that pins them."""
    manifest = {
        "id": plugin_id,
        "source": {"path": f"plugins/{plugin_id}", "repo": "x/y"},
        "files": {name: sha(data) for name, data in files.items()},
    }
    tree = {f"plugins/{plugin_id}/{name}": data for name, data in files.items()}
    tree[f"plugins/{plugin_id}/plugin.json"] = json.dumps(manifest).encode()
    return tree


def signed_body(repo: GitRepo, source_commit: str, plugin_ids=()) -> bytes:
    """A signed body as the signing run builds it: the providers of catalog.json at the source commit
    and the plugin manifests of that commit."""
    catalog = json.loads(repo.blob(source_commit, "catalog.json"))
    plugins = [
        {"manifest": json.loads(repo.blob(source_commit, f"plugins/{plugin_id}/plugin.json"))}
        for plugin_id in plugin_ids
    ]
    return json.dumps({
        "schema_version": 2, "name": "t", "channel": "main", "signed_version": 1,
        "not_before": "2026-11-01T00:00:00Z", "expires": "2027-01-30T00:00:00Z",
        "source_commit": source_commit, "providers": catalog["providers"], "plugins": plugins,
    }).encode()


class SignedFeedTests(MirrorTestCase):
    """The mirror carries every path the client asks for: the signed trio at the head and the
    bundle files, providers and plugins, at the signed body's source commit."""

    def setUp(self):
        super().setUp()
        self.repo.commit(b"older\n", message="older")
        self.source = self.repo.commit(
            b"source\n", extra=plugin_files("alpha", {"engine.py": b"engine\n", "data/words.txt": b"words\n"}),
            message="source",
        )
        # The signing commit comes after the commit it signs and lags the head by design.
        self.head = self.repo.commit(
            b"source\n",
            extra={
                "signed/main/catalog.json": signed_body(self.repo, self.source, ["alpha"]),
                "signed/main/catalog.json.sig": b'{"schema_version": 1, "signatures": []}',
                "keys/catalog-signing-keys.json": b"{}",
            },
            message="sign",
        )
        self.build()

    def verify(self, expected=None):
        return verify_mirror.verify_channel(
            self.site_fetch(), self.repo.path, "main", expected or self.head, attempts=1, delay=0, workers=4,
            ref="main",
        )

    def test_the_client_finds_every_path_it_asks_for_byte_identical_to_git(self):
        fetch = self.site_fetch()
        head = f"v1/main/{self.head}/"
        # The three signed files, at the head, under the channel's own path.
        for path in ("signed/main/catalog.json", "signed/main/catalog.json.sig", "keys/catalog-signing-keys.json"):
            self.assertEqual(fetch(head + path), self.repo.blob(self.head, path), path)
        body = json.loads(fetch(head + "signed/main/catalog.json"))
        # Bundle files are read at the body's source commit, as source.path plus the file name.
        source = f"v1/main/{body['source_commit']}/"
        asked = 0
        for item in body["providers"] + body["plugins"]:
            manifest = item["manifest"]
            for name, digest in manifest["files"].items():
                path = "/".join(part for part in (manifest["source"]["path"], name) if part)
                got = fetch(source + path)
                self.assertEqual(sha(got), digest, path)
                self.assertEqual(got, self.repo.blob(body["source_commit"], path), path)
                asked += 1
        self.assertGreaterEqual(asked, 3)
        self.assertIn("plugins/alpha/data/words.txt", [
            "/".join((i["manifest"]["source"]["path"], n)) for i in body["plugins"] for n in i["manifest"]["files"]
        ])

    def test_plugin_files_are_published_under_the_commit_that_has_them(self):
        base = self.out / "v1/main" / self.source / "plugins/alpha"
        self.assertEqual((base / "engine.py").read_bytes(), b"engine\n")
        self.assertEqual((base / "data/words.txt").read_bytes(), b"words\n")
        self.assertFalse((base / "plugin.json").exists())

    def test_the_clean_site_verifies(self):
        self.assertEqual(self.verify(), [])

    def test_a_flipped_signed_byte_is_caught(self):
        for path in ("signed/main/catalog.json", "signed/main/catalog.json.sig", "keys/catalog-signing-keys.json"):
            with self.subTest(path=path):
                target = self.out / "v1/main" / self.head / path
                original = target.read_bytes()
                target.write_bytes(original + b" ")
                self.assertTrue(any(path in e and "git blob" in e for e in self.verify()))
                target.write_bytes(original)

    def test_a_missing_signed_file_is_caught(self):
        (self.out / "v1/main" / self.head / "keys/catalog-signing-keys.json").unlink()
        self.assertTrue(any("catalog-signing-keys.json" in e for e in self.verify()))

    def test_a_missing_plugin_file_at_the_source_commit_is_caught(self):
        (self.out / "v1/main" / self.source / "plugins/alpha/engine.py").unlink()
        self.assertTrue(any("plugins/alpha/engine.py" in e for e in self.verify()))

    def sign_again(self, **changes):
        body = json.loads(self.repo.blob(self.head, "signed/main/catalog.json"))
        for key, value in changes.items():
            body[key] = value
        return self.repo.commit(
            b"source\n", extra={"signed/main/catalog.json": json.dumps(body).encode()}, message="sign again"
        )

    def test_a_head_that_names_a_commit_git_does_not_have_fails_the_build(self):
        self.sign_again(source_commit="b" * 40)
        with self.assertRaisesRegex(build_mirror.MirrorError, "its signed catalog names " + "b" * 40):
            build_mirror.build_site(self.repo.path, self.tmp / "site-bad", channels={"main": "main"}, now=NOW,
                                    log=self.quiet)

    def test_a_head_whose_pins_its_source_commit_does_not_carry_fails_the_build(self):
        body = json.loads(self.repo.blob(self.head, "signed/main/catalog.json"))
        body["plugins"][0]["manifest"]["files"]["engine.py"] = "0" * 64
        self.repo.commit(
            b"source\n", extra={"signed/main/catalog.json": json.dumps(body).encode()}, message="bad pin"
        )
        with self.assertRaisesRegex(build_mirror.MirrorError, "plugins/alpha/engine.py"):
            build_mirror.build_site(self.repo.path, self.tmp / "site-pin", channels={"main": "main"}, now=NOW,
                                    log=self.quiet)

    def test_a_head_with_an_unreadable_signed_body_fails_the_build(self):
        self.repo.commit(b"source\n", extra={"signed/main/catalog.json": b"not json"}, message="broken")
        with self.assertRaisesRegex(build_mirror.MirrorError, "signed catalog is not a catalog"):
            build_mirror.build_site(self.repo.path, self.tmp / "site-x", channels={"main": "main"}, now=NOW,
                                    log=self.quiet)

    def test_an_unreadable_signed_body_on_an_older_commit_is_skipped(self):
        self.repo.commit(b"source\n", extra={"signed/main/catalog.json": b"not json"}, message="broken")
        self.repo.git("rm", "-q", "signed/main/catalog.json")
        self.repo.git("commit", "-q", "-m", "unsign")
        pointer = build_mirror.build_site(
            self.repo.path, self.tmp / "site-skip", channels={"main": "main"}, now=NOW, log=self.quiet
        )["main"]
        self.assertGreater(len(pointer["history"]), 3)

    def test_a_missing_bundle_at_the_source_commit_is_caught_by_verify(self):
        (self.out / "v1/main" / self.source / "plugins/alpha/engine.py").unlink()
        self.assertTrue(any("plugins/alpha/engine.py" in e for e in self.verify()))

    def test_directories_without_a_manifest_are_ignored_whatever_they_are_called(self):
        extra = {"plugins/.cache/x.bin": b"x", "plugins/my plugin/notes.txt": b"x", "plugins/-odd/a.txt": b"x"}
        self.repo.commit(b"source\n", extra=extra, message="stray")
        self.assertIn("catalog.json", build_mirror.commit_files(self.repo.path, "HEAD", "main"))

    def test_plugin_paths_the_client_accepts_are_published(self):
        files = {"data/a+b.txt": b"1", "-lead.txt": b"2", "x@y=z,w~v.txt": b"3"}
        self.repo.commit(b"source\n", extra=plugin_files("beta_plugin", files), message="odd names")
        out = self.tmp / "site-odd"
        pointer = build_mirror.build_site(self.repo.path, out, channels={"main": "main"}, now=NOW, log=self.quiet)
        for name, data in files.items():
            self.assertEqual((out / "v1/main" / pointer["main"]["commit"] / "plugins/beta_plugin" / name).read_bytes(), data)

    def test_the_source_path_falls_back_to_bundle_path_and_null_means_empty(self):
        for source, expected in (({"bundle_path": "plugins/gamma"}, "plugins/gamma/f.py"), ({"path": None}, "f.py")):
            with self.subTest(source=source):
                manifest = {"source": source, "files": {"f.py": sha(b"x")}}
                self.assertEqual(list(build_mirror.catalog_pins(catalog_for({}), [manifest])), [expected])

    def test_a_plugin_pin_that_does_not_match_its_bytes_fails_the_build(self):
        manifest = json.loads(self.repo.blob(self.head, "plugins/alpha/plugin.json"))
        manifest["files"]["engine.py"] = "0" * 64
        self.repo.commit(b"source\n", extra={"plugins/alpha/plugin.json": json.dumps(manifest).encode()})
        with self.assertRaisesRegex(build_mirror.MirrorError, "does not match its pin"):
            build_mirror.build_site(self.repo.path, self.tmp / "site-x", channels={"main": "main"}, now=NOW,
                                    log=self.quiet)

    def test_a_plugin_pin_on_a_signed_path_is_refused(self):
        tree = plugin_files("alpha", {"engine.py": b"engine\n"})
        manifest = json.loads(tree["plugins/alpha/plugin.json"])
        manifest["source"]["path"] = "signed/main"
        manifest["files"] = {"catalog.json": sha(b"x")}
        with self.assertRaisesRegex(build_mirror.ValidationError, "collides with a mirror file"):
            build_mirror.catalog_pins(
                catalog_for({"provider.py": b"x"}), [manifest], {"catalog.json", *build_mirror.opaque_paths("main")}
            )

    def test_a_malformed_plugin_manifest_is_a_validation_error(self):
        self.repo.commit(b"source\n", extra={"plugins/alpha/plugin.json": b"not json"})
        with self.assertRaisesRegex(build_mirror.ValidationError, "is not JSON"):
            build_mirror.commit_files(self.repo.path, "HEAD", "main")

    def test_the_same_tree_gives_the_same_site(self):
        again = self.tmp / "site-again"
        build_mirror.build_site(self.repo.path, again, channels={"main": "main"}, now=NOW, log=self.quiet)
        listing = lambda root: sorted(  # noqa: E731
            (str(p.relative_to(root)), p.read_bytes()) for p in root.rglob("*") if p.is_file()
        )
        self.assertEqual(listing(self.out), listing(again))


class RetentionBoundaryTests(MirrorTestCase):
    """A signed body can name a commit older than the retained window."""

    RETENTION = 3

    def setUp(self):
        super().setUp()
        self.old = self.repo.commit(
            b"old\n", extra=plugin_files("alpha", {"engine.py": b"engine\n"}), message="old source"
        )
        for i in range(5):
            self.repo.commit(f"v{i}\n".encode(), message=f"v{i}")
        self.head = self.repo.commit(
            b"v4\n",
            extra={
                "signed/main/catalog.json": signed_body(self.repo, self.old, ["alpha"]),
                "signed/main/catalog.json.sig": b"sig",
                "keys/catalog-signing-keys.json": b"{}",
            },
            message="sign",
        )
        self.pointer = self.build(retention=self.RETENTION)["main"]

    def verify(self, fetch=None):
        return verify_mirror.verify_channel(
            fetch or self.site_fetch(), self.repo.path, "main", self.head, attempts=1, delay=0, workers=4,
            ref="main", retention=self.RETENTION,
        )

    def test_the_source_commit_is_outside_the_window_and_still_published(self):
        self.assertNotIn(self.old, self.pointer["history"])
        self.assertEqual(len(self.pointer["history"]), self.RETENTION)
        base = self.out / "v1/main" / self.old
        self.assertEqual((base / "plugins/alpha/engine.py").read_bytes(), b"engine\n")
        self.assertEqual((base / "providers/demo/provider.py").read_bytes(), b"old\n")

    def test_the_client_can_fetch_every_bundle_the_head_signed_catalog_pins(self):
        fetch = self.site_fetch()
        body = json.loads(fetch(f"v1/main/{self.head}/signed/main/catalog.json"))
        self.assertEqual(body["source_commit"], self.old)
        for item in body["providers"] + body["plugins"]:
            manifest = item["manifest"]
            for name, digest in manifest["files"].items():
                path = "/".join((manifest["source"]["path"], name))
                self.assertEqual(sha(fetch(f"v1/main/{self.old}/{path}")), digest, path)

    def test_the_site_verifies_and_a_missing_bundle_only_commit_file_is_caught(self):
        self.assertEqual(self.verify(), [])
        (self.out / "v1/main" / self.old / "plugins/alpha/engine.py").unlink()
        self.assertTrue(any("plugins/alpha/engine.py" in e for e in self.verify()))

    def test_every_signed_body_in_the_window_keeps_its_bundles(self):
        # An older signing commit inside the window names a still older source commit.
        out = self.tmp / "site-two"
        repo = GitRepo(self.tmp / "repo2")
        first = repo.commit(b"first\n", extra=plugin_files("alpha", {"engine.py": b"one\n"}), message="first")
        repo.commit(b"mid\n", extra={"signed/main/catalog.json": signed_body(repo, first, ["alpha"])}, message="sign 1")
        second = repo.commit(b"second\n", extra=plugin_files("alpha", {"engine.py": b"two\n"}), message="second")
        for i in range(3):
            repo.commit(f"v{i}\n".encode(), message=f"v{i}")
        head = repo.commit(b"head\n", extra={"signed/main/catalog.json": signed_body(repo, second, ["alpha"])}, message="sign 2")
        pointer = build_mirror.build_site(repo.path, out, channels={"main": "main"}, retention=3, now=NOW,
                                          log=self.quiet)["main"]
        self.assertEqual(pointer["commit"], head)
        for source in (first, second):
            self.assertNotIn(source, pointer["history"])
            self.assertTrue((out / "v1/main" / source / "plugins/alpha/engine.py").is_file(), source)

    def test_only_the_window_and_the_source_commit_are_published(self):
        listing = sorted(p.name for p in (self.out / "v1/main").iterdir())
        self.assertEqual(listing, sorted(["current", self.old, *self.pointer["history"]]))


class LiveBeforeTests(MirrorTestCase):
    def setUp(self):
        super().setUp()
        self.c1 = self.repo.commit(b"1\n", message="1")
        self.c2 = self.repo.commit(b"2\n", message="2")
        self.out = self.tmp / "site-new"
        self.pointer = self.build()["main"]

    def verify(self, live_before):
        return verify_mirror.verify_channel(
            self.site_fetch(), self.repo.path, "main", self.c2, attempts=1, delay=0, workers=4, ref="main",
            live_before=live_before,
        )

    def seen(self, commit, published_at="2026-11-07T11:00:00Z"):
        return {"channel": "main", "commit": commit, "catalog_sha256": "0" * 64, "published_at": published_at,
                "history": [commit]}

    def test_a_pointer_that_advanced_is_fine(self):
        self.assertEqual(self.verify(self.seen(self.c1)), [])
        self.assertEqual(self.verify(self.seen(self.c2)), [])
        self.assertEqual(self.verify(None), [])

    def test_a_pointer_older_than_the_one_seen_before_the_deploy_fails(self):
        problems = self.verify(self.seen(self.c2, "2026-11-08T00:00:00Z"))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("went backwards", problems[0])

    def test_a_pointer_behind_the_commit_seen_before_the_deploy_fails(self):
        c3 = self.repo.commit(b"3\n", message="3")
        problems = self.verify(self.seen(c3))
        self.assertTrue(any("went backwards" in p for p in problems), problems)


class AdvanceGuardTests(MirrorTestCase):
    def setUp(self):
        super().setUp()
        self.c1 = self.repo.commit(b"1\n", message="1")
        self.c2 = self.repo.commit(b"2\n", message="2")
        self.c3 = self.repo.commit(b"3\n", message="3")

    def site(self, ref, name, when=NOW):
        out = self.tmp / name
        build_mirror.build_site(self.repo.path, out, channels={"main": ref}, now=when, log=self.quiet)
        return out

    def live(self, root):
        def fetch(path, retry_not_found=True):
            try:
                return (root / path).read_bytes()
            except FileNotFoundError as exc:
                raise verify_mirror.NotFound(path) from exc
        return fetch

    def check(self, live_ref, new_ref, live_when=NOW, new_when=NOW):
        live = self.site(live_ref, "live", live_when) if live_ref else self.tmp / "nothing"
        new = self.site(new_ref, "new", new_when)
        return check_advance.check_channel(self.live(live), self.repo.path, new, "main")[0]

    def test_an_advance_is_allowed(self):
        self.assertIsNone(self.check(self.c2, self.c3))

    def test_the_same_commit_is_allowed(self):
        self.assertIsNone(self.check(self.c3, self.c3))

    def test_a_first_deploy_is_allowed(self):
        self.assertIsNone(self.check(None, self.c3))

    def test_an_older_commit_is_refused(self):
        self.assertIn("not an ancestor", self.check(self.c3, self.c1))

    def test_a_diverged_commit_is_refused(self):
        self.repo.git("checkout", "-q", "-b", "side", self.c2)
        side = self.repo.commit(b"side\n", message="side")
        self.repo.git("checkout", "-q", "main")
        self.assertIn("not an ancestor", self.check(self.c3, side))

    def test_a_live_commit_this_clone_does_not_know_is_refused(self):
        live = self.tmp / "live"
        (live / "v1/main").mkdir(parents=True)
        pointer = {"channel": "main", "commit": "a" * 40, "catalog_sha256": "0" * 64,
                   "published_at": "2026-11-01T00:00:00Z", "history": ["a" * 40]}
        (live / "v1/main/current").write_text(json.dumps(pointer))
        new = self.site(self.c3, "new")
        self.assertIn("not an ancestor", check_advance.check_channel(self.live(live), self.repo.path, new, "main")[0])

    def test_an_older_stamp_is_refused_even_for_a_newer_commit(self):
        later = datetime(2026, 11, 8, tzinfo=timezone.utc)
        self.assertIn("older than the previously served", self.check(self.c2, self.c3, live_when=later))

    def test_an_unreadable_live_pointer_is_refused(self):
        new = self.site(self.c3, "new")

        def broken(path, retry_not_found=True):
            raise verify_mirror.FetchError("boom")

        self.assertIn("cannot read the live pointer", check_advance.check_channel(broken, self.repo.path, new, "main")[0])

    def test_a_malformed_live_pointer_is_refused(self):
        live = self.tmp / "live"
        (live / "v1/main").mkdir(parents=True)
        (live / "v1/main/current").write_text("{}")
        new = self.site(self.c3, "new")
        self.assertIn("unreadable", check_advance.check_channel(self.live(live), self.repo.path, new, "main")[0])

    def serve(self, directory):
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def __init__(inner, *a, **kw):
                super().__init__(*a, directory=str(directory), **kw)

            def log_message(inner, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/"

    def test_a_first_deploy_passes_through_the_real_fetcher_and_records_no_live_pointer(self):
        empty = self.tmp / "empty-site"
        empty.mkdir()
        new = self.site(self.c3, "new")
        outputs = self.tmp / "outputs"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = check_advance.main(["--site", str(new), "--base-url", self.serve(empty),
                                         "--repo", str(self.repo.path), "--outputs-file", str(outputs)])
        self.assertEqual(status, 0)
        self.assertEqual(outputs.read_text(), 'live={"main": null}\n')

    def test_the_live_pointer_seen_is_recorded_for_verify(self):
        live = self.site(self.c2, "live")
        new = self.site(self.c3, "new")
        outputs = self.tmp / "outputs"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = check_advance.main(["--site", str(new), "--base-url", self.serve(live),
                                         "--repo", str(self.repo.path), "--outputs-file", str(outputs)])
        self.assertEqual(status, 0)
        recorded = json.loads(outputs.read_text().removeprefix("live="))
        self.assertEqual(recorded["main"]["commit"], self.c2)

    def test_exit_status_and_the_override(self):
        live = self.site(self.c3, "live")
        new = self.site(self.c1, "new")

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def __init__(inner, *a, **kw):
                super().__init__(*a, directory=str(live), **kw)

            def log_message(inner, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        args = ["--site", str(new), "--base-url", f"http://127.0.0.1:{server.server_address[1]}/",
                "--repo", str(self.repo.path)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(check_advance.main(args), 1)
            self.assertEqual(check_advance.main(args + ["--allow-non-advance"]), 0)
            self.assertEqual(check_advance.main(["--site", str(live), "--base-url", args[3], "--repo", args[5]]), 0)


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

    def serve(self, handler):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/"

    def test_a_404_is_typed_and_retried_only_on_request(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(inner):
                seen.append(inner.path)
                inner.send_response(404)
                inner.end_headers()

            def log_message(inner, *args):
                pass

        fetch = verify_mirror.http_fetcher(self.serve(Handler), retries=3, backoff=0)
        with self.assertRaises(verify_mirror.NotFound):
            fetch("v1/main/current", retry_not_found=False)
        self.assertEqual(len(seen), 1)
        with self.assertRaises(verify_mirror.NotFound):
            fetch("v1/main/current")
        self.assertEqual(len(seen), 4)

    def test_a_truncated_body_is_retried_and_ends_as_a_fetch_error(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(inner):
                seen.append(inner.path)
                inner.send_response(200)
                inner.send_header("Content-Length", "100")
                inner.end_headers()
                inner.wfile.write(b"abc")

            def log_message(inner, *args):
                pass

        fetch = verify_mirror.http_fetcher(self.serve(Handler), retries=3, backoff=0)
        with self.assertRaises(verify_mirror.FetchError):
            fetch("v1/main/current")
        self.assertEqual(len(seen), 3)

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

    def job(self, name):
        """The text of one job under ``jobs:``."""
        import re

        jobs = self.text.split("\njobs:\n", 1)[1]
        blocks = re.split(r"^  (?=[a-z][\w-]*:\n)", jobs, flags=re.M)
        for block in blocks:
            if block.startswith(f"{name}:\n"):
                return block
        self.fail(f"no job {name}")

    def test_no_wide_permissions(self):
        self.assertNotIn("write-all", self.text)
        self.assertNotIn("contents: write", self.text)
        top = self.text.split("\npermissions:\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(top.split(), ["contents:", "read"])
        for name in ("build", "verify"):
            with self.subTest(job=name):
                block = self.job(name)
                self.assertNotIn("pages: write", block)
                self.assertNotIn("id-token: write", block)
        deploy = self.job("deploy")
        self.assertIn("pages: write", deploy)
        self.assertIn("id-token: write", deploy)
        self.assertNotIn("checkout", deploy)

    def test_runs_never_overlap_and_are_never_cancelled(self):
        head = self.text.split("\njobs:\n", 1)[0]
        self.assertIn("group: pages-mirror", head)
        self.assertIn("cancel-in-progress: false", head)

    def test_builds_and_verifies_from_the_branch_tips_not_the_event_commit(self):
        refresh = "+refs/heads/main:refs/remotes/origin/main"
        for name in ("build", "verify"):
            with self.subTest(job=name):
                block = self.job(name)
                self.assertIn(refresh, block)
                self.assertIn("+refs/heads/beta:refs/remotes/origin/beta", block)
                self.assertLess(block.index("actions/checkout"), block.index(refresh))
        build = self.job("build")
        self.assertLess(build.index(refresh), build.index("scripts/build_mirror.py"))

    def test_the_site_artifact_is_named_per_attempt_so_a_deploy_only_rerun_finds_none(self):
        name = "github-pages-${{ github.run_attempt }}"
        self.assertIn(f"name: {name}", self.job("build"))
        self.assertIn(f"artifact_name: {name}", self.job("deploy"))
        self.assertNotIn("name: github-pages\n", self.job("build"))

    def test_deploy_only_deploys(self):
        deploy = self.job("deploy")
        self.assertNotIn("run:", deploy)
        self.assertEqual(deploy.count("uses:"), 1)

    def test_verify_gets_the_live_pointers_seen_before_the_deploy(self):
        self.assertIn("live: ${{ steps.advance.outputs.live }}", self.job("build"))
        verify = self.job("verify")
        self.assertIn("needs.build.outputs.live", verify)
        self.assertIn("--live-before", verify)

    def test_the_advance_guard_runs_before_anything_is_uploaded(self):
        build = self.job("build")
        self.assertIn("scripts/check_advance.py", build)
        self.assertLess(build.index("scripts/build_mirror.py"), build.index("scripts/check_advance.py"))
        self.assertLess(build.index("scripts/check_advance.py"), build.index("upload-pages-artifact"))
        self.assertIn("allow_non_advance", self.text)


if __name__ == "__main__":
    unittest.main()
