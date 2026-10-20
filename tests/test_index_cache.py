"""Opening the query index (spec, Start-up step 3), on the synthetic store.

The cache directory is a real temporary directory and the archives are the
fixture's real files: every assertion lands on what is on disk afterwards or on
what a query through the handle returns. Expected repository names are the
literals in fixture_store/DESIGN.md section 1.
"""

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path
from unittest import mock

from fixture_store.build import GRAPH, INDEX_ARCHIVE, INDEX_PLAIN, MANIFEST, build
from knowledgestore_mcp import index, manifest, store
from knowledgestore_mcp.index import Fallback, IndexHandle

# DESIGN.md 1: the twelve repositories, `k` 1 to 12 in this order.
REPOS = [
    "accounts-web",
    "audit-service",
    "chart-web",
    "gateway-service",
    "infra-modules",
    "ledger-service",
    "notifications-service",
    "payment-apis",
    "payments-api",
    "reporting-worker",
    "shared-lib-java",
    "ui-components",
]


def repo_names(handle: IndexHandle) -> list[str]:
    return [row[0] for row in handle.connection.execute("SELECT name FROM repos ORDER BY k")]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, tuple]:
    """Every file and directory under `root`, with its bytes' hash, mode and mtime."""
    state: dict[str, tuple] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        content = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        state[path.relative_to(root).as_posix()] = (info.st_mode, info.st_mtime_ns, content)
    return state


def open_in_child(root: str, cache: str, barrier, results) -> None:
    """One server's start-up, in its own process (the concurrency test)."""
    root_path = Path(root)
    barrier.wait()
    handle = index.open_index(root_path, manifest.load(root_path), Path(cache))
    if isinstance(handle, IndexHandle):
        results.put(repo_names(handle))
        handle.close()
    else:
        results.put(handle.reason)


class CacheTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.cache = self.tmp / "cache"
        # Nothing here may reach the real cache, even through a default.
        patcher = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.entries = self.cache / "knowledge-store" / "example-store"

    def open(self, root: Path) -> IndexHandle | Fallback:
        result = index.open_index(root, manifest.load(root), self.cache)
        if isinstance(result, IndexHandle):
            self.addCleanup(result.close)
        return result

    def open_handle(self, root: Path) -> IndexHandle:
        result = self.open(root)
        assert isinstance(result, IndexHandle), result
        return result

    def listing(self, directory: Path) -> list[str]:
        return sorted(p.name for p in directory.iterdir()) if directory.exists() else []


class CacheHomeTest(unittest.TestCase):
    def test_xdg_cache_home_is_used_when_set(self):
        """Catches: ignoring XDG_CACHE_HOME, so the cache lands in ~/.cache regardless."""
        got = index.cache_home({"XDG_CACHE_HOME": "/srv/cache"}, Path("/home/u"))
        self.assertEqual(got, Path("/srv/cache"))

    def test_unset_or_empty_xdg_cache_home_means_home_dot_cache(self):
        """Catches: `${XDG_CACHE_HOME-}` instead of `:-` semantics, so an empty value
        writes the cache relative to the working directory."""
        self.assertEqual(index.cache_home({}, Path("/home/u")), Path("/home/u/.cache"))
        self.assertEqual(
            index.cache_home({"XDG_CACHE_HOME": ""}, Path("/home/u")), Path("/home/u/.cache")
        )

    def test_a_relative_xdg_cache_home_is_ignored(self):
        """Catches: honouring a relative value, which writes the cache under the
        working directory - inside the store when the server is started there."""
        got = index.cache_home({"XDG_CACHE_HOME": "cache"}, Path("/home/u"))
        self.assertEqual(got, Path("/home/u/.cache"))


class ArchiveTest(CacheTestCase):
    def test_the_cache_entry_is_named_by_the_archives_sha256(self):
        """Catches: keying the entry on anything but the archive's full sha256 (the
        decompressed bytes, a truncated digest, the manifest digest), or decompressing
        to the wrong bytes."""
        root = build(self.tmp / "s")
        archive = root / INDEX_ARCHIVE
        handle = self.open_handle(root)
        name = sha256_of(archive) + ".sqlite"
        self.assertEqual(handle.path, self.entries / name)
        self.assertEqual(self.listing(self.entries), [name])
        self.assertEqual(handle.path.read_bytes(), gzip.decompress(archive.read_bytes()))
        self.assertEqual(repo_names(handle), REPOS)

    def test_a_second_open_reuses_the_entry_without_rewriting_it(self):
        """Catches: decompressing on every start, which rewrites the entry (a new
        inode, a new mtime) and costs the start-up time the cache exists to save."""
        root = build(self.tmp / "s")
        first = self.open_handle(root)
        first.close()
        os.utime(first.path, ns=(1_000_000_000, 1_000_000_000))
        before = first.path.stat()
        second = self.open_handle(root)
        after = second.path.stat()
        self.assertEqual(second.path, first.path)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, 1_000_000_000))
        self.assertEqual(repo_names(second), REPOS)

    def test_a_changed_archive_gets_a_new_entry_and_the_old_one_is_removed(self):
        """Catches: serving a stale entry after the archive changes, or leaving every
        old entry behind so the cache grows by an index per rebuild."""
        root = build(self.tmp / "s")
        archive = root / INDEX_ARCHIVE
        old = self.open_handle(root).path.name
        # The same database, re-compressed with another header time: new archive bytes.
        archive.write_bytes(gzip.compress(gzip.decompress(archive.read_bytes()), mtime=1))
        new = sha256_of(archive) + ".sqlite"
        self.assertNotEqual(new, old)
        handle = self.open_handle(root)
        self.assertEqual(handle.path.name, new)
        self.assertEqual(self.listing(self.entries), [new])
        self.assertEqual(repo_names(handle), REPOS)

    def test_an_archive_replaced_after_hashing_is_named_by_what_was_decompressed(self):
        """Catches: naming the entry by the first hash when a pull replaced the
        archive between hashing it and decompressing it, which files the new index
        under the old archive's name and serves it if that archive returns."""
        root = build(self.tmp / "s")
        archive = root / INDEX_ARCHIVE
        database = gzip.decompress(archive.read_bytes())
        hash_file = index._sha256

        def hash_then_pull(path: Path) -> str:
            digest = hash_file(path)
            archive.write_bytes(gzip.compress(database, mtime=1))  # the pull lands
            return digest

        with mock.patch.object(index, "_sha256", hash_then_pull):
            handle = self.open_handle(root)
        name = sha256_of(archive) + ".sqlite"
        self.assertEqual(handle.path, self.entries / name)
        self.assertEqual(self.listing(self.entries), [name])

    def test_other_store_ids_and_in_flight_temp_files_survive_the_cleanup(self):
        """Catches: a cleanup that removes more than this store id's other entries -
        another store's cache, an unrelated file, or a peer server's temp file that
        is still being written."""
        other = self.cache / "knowledge-store" / "other-store"
        other.mkdir(parents=True)
        (other / "0123.sqlite").write_bytes(b"other")
        self.entries.mkdir(parents=True)
        (self.entries / "stale.sqlite").write_bytes(b"stale")
        (self.entries / "abc.sqlite.tmp-1-xyz").write_bytes(b"peer")
        (self.entries / "README").write_bytes(b"note")
        root = build(self.tmp / "s")
        name = self.open_handle(root).path.name
        self.assertEqual(self.listing(other), ["0123.sqlite"])
        self.assertEqual((other / "0123.sqlite").read_bytes(), b"other")
        self.assertEqual(
            self.listing(self.entries), sorted(["README", "abc.sqlite.tmp-1-xyz", name])
        )

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores file modes")
    def test_a_read_only_cache_directory_falls_back_and_leaves_nothing(self):
        """Catches: an OSError writing the cache escaping as a crash, instead of the
        spec's fallback to the graph."""
        self.entries.mkdir(parents=True)
        self.entries.chmod(0o500)
        self.addCleanup(self.entries.chmod, 0o700)
        root = build(self.tmp / "s")
        result = self.open(root)
        self.assertIsInstance(result, Fallback)
        assert isinstance(result, Fallback)
        self.assertIn("could not be written", result.reason)
        self.assertEqual(self.listing(self.entries), [])

    def test_a_write_that_fails_part_way_removes_its_temp_file(self):
        """Catches: a failed decompress leaving its partial `.tmp-` file behind, or
        renaming it into place as an entry. The archive is cut mid-stream, so the
        temp file exists and holds bytes when the error arrives."""
        root = build(self.tmp / "s")
        archive = root / INDEX_ARCHIVE
        whole = archive.read_bytes()
        archive.write_bytes(whole[: len(whole) // 2])
        result = self.open(root)
        self.assertIsInstance(result, Fallback)
        self.assertEqual(self.listing(self.entries), [])

    def test_two_processes_decompressing_at_once_leave_one_whole_entry(self):
        """Catches: a fixed temp name that two servers share, so one renames the
        other's half-written file into place, or a cleanup that deletes a peer's
        entry before it is renamed."""
        root = build(self.tmp / "s")
        expected = gzip.decompress((root / INDEX_ARCHIVE).read_bytes())
        context = get_context("spawn")
        for _ in range(3):
            shutil.rmtree(self.cache, ignore_errors=True)
            barrier = context.Barrier(2)
            results = context.Queue()
            children = [
                context.Process(
                    target=open_in_child, args=(str(root), str(self.cache), barrier, results)
                )
                for _ in range(2)
            ]
            for child in children:
                child.start()
            got = [results.get(timeout=60) for _ in children]
            for child in children:
                child.join(timeout=60)
                self.assertEqual(child.exitcode, 0)
            self.assertEqual(got, [REPOS, REPOS])
            name = sha256_of(root / INDEX_ARCHIVE) + ".sqlite"
            self.assertEqual(self.listing(self.entries), [name])
            self.assertEqual((self.entries / name).read_bytes(), expected)

    def test_two_threads_decompressing_at_once_leave_one_whole_entry(self):
        """Catches: a temp name keyed on the pid alone, which two threads of one
        process share."""
        root = build(self.tmp / "s")
        loaded = manifest.load(root)
        expected = gzip.decompress((root / INDEX_ARCHIVE).read_bytes())
        for _ in range(5):
            shutil.rmtree(self.cache, ignore_errors=True)
            barrier = threading.Barrier(4)
            got: list[object] = []

            def run():
                barrier.wait()
                result = index.open_index(root, loaded, self.cache)
                got.append(repo_names(result) if isinstance(result, IndexHandle) else result)
                if isinstance(result, IndexHandle):
                    result.close()

            threads = [threading.Thread(target=run) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            self.assertEqual(got, [REPOS] * 4)
            name = sha256_of(root / INDEX_ARCHIVE) + ".sqlite"
            self.assertEqual(self.listing(self.entries), [name])
            self.assertEqual((self.entries / name).read_bytes(), expected)


class PlacementTest(CacheTestCase):
    def test_a_plain_index_is_opened_in_place_and_nothing_is_cached(self):
        """Catches: copying or decompressing when a plain `store.sqlite` is present,
        or preferring the archive over it (spec: a plain file is read when present)."""
        root = build(self.tmp / "s", index="plain")
        plain = root / INDEX_PLAIN
        # The archive beside it too, as a store with a local build holds both.
        (root / INDEX_ARCHIVE).write_bytes(gzip.compress(plain.read_bytes()))
        handle = self.open_handle(root)
        self.assertEqual(handle.path, plain)
        self.assertEqual(repo_names(handle), REPOS)
        self.assertFalse(self.cache.exists())

    def test_no_index_falls_back_and_says_so(self):
        """Catches: treating a missing index as an error, or falling back silently."""
        root = build(self.tmp / "s", index="none")
        result = self.open(root)
        self.assertIsInstance(result, Fallback)
        assert isinstance(result, Fallback)
        self.assertIn("no index in the store", result.reason)
        self.assertFalse(self.cache.exists())

    def test_an_unsafe_store_id_is_not_joined_into_the_cache_path(self):
        """Catches: joining `store.id` into a path unchecked, so `../x` writes the
        cache outside its directory."""
        root = build(self.tmp / "s")
        path = root / MANIFEST
        document = json.loads(path.read_text(encoding="utf-8"))
        document["store"]["id"] = "../escaped"
        path.write_text(json.dumps(document), encoding="utf-8")
        result = self.open(root)
        self.assertIsInstance(result, Fallback)
        self.assertFalse(self.cache.exists())
        self.assertFalse((self.tmp / "escaped").exists())

    def test_a_cache_home_inside_the_store_is_refused(self):
        """Catches: writing the cache into the store when the cache home points there,
        which would break the server's only promise about writes."""
        root = build(self.tmp / "s")
        before = snapshot(root)
        result = index.open_index(root, manifest.load(root), root / ".cache")
        self.assertIsInstance(result, Fallback)
        self.assertEqual(snapshot(root), before)

    def test_an_index_symlinked_outside_the_store_is_not_opened(self):
        """Catches: following a symlink in the store to an index outside it."""
        root = build(self.tmp / "s", index="plain")
        plain = root / INDEX_PLAIN
        outside = self.tmp / "outside.sqlite"
        plain.rename(outside)
        plain.symlink_to(outside)
        result = self.open(root)
        self.assertIsInstance(result, Fallback)
        assert isinstance(result, Fallback)
        self.assertIn("outside the store", result.reason)


class ReadOnlyTest(CacheTestCase):
    def assert_read_only(self, handle: IndexHandle) -> None:
        with self.assertRaises(sqlite3.OperationalError):
            handle.connection.execute("CREATE TEMP TABLE scratch(a)")
        # With query_only lifted, the file itself must still refuse the write.
        handle.connection.execute("PRAGMA query_only=0")
        with self.assertRaises(sqlite3.OperationalError):
            handle.connection.execute("INSERT INTO meta(key, value) VALUES ('k', 'v')")

    def test_a_cache_entry_refuses_writes(self):
        """Catches: dropping `PRAGMA query_only` (the temp-table write succeeds) or the
        read-only URI (the insert succeeds once query_only is lifted)."""
        self.assert_read_only(self.open_handle(build(self.tmp / "s")))

    def test_a_plain_index_in_the_store_refuses_writes(self):
        """Catches: opening the store's own file writable, so a stray statement
        could change the store."""
        self.assert_read_only(self.open_handle(build(self.tmp / "s", index="plain")))


class StoreTest(CacheTestCase):
    def test_the_index_path_never_parses_the_graph(self):
        """Catches: start-up or a query reading the whole graph although the index is
        present. The graph is corrupt, so any parse of it fails the test."""
        root = build(self.tmp / "s")
        (root / GRAPH).write_bytes(b"not a gzip archive")
        loaded = store.open_store(root, self.cache)
        self.addCleanup(loaded.close)
        assert isinstance(loaded.index, IndexHandle)
        self.assertEqual(repo_names(loaded.index), REPOS)
        self.assertEqual(loaded.layer("provenance")["repositories"]["chart-web"]["sha"], "3" * 40)
        # The graph really is unreadable: reading it is what would have failed.
        with self.assertRaises(OSError):
            loaded.layer("graph")

    def test_without_an_index_the_graph_layer_is_read(self):
        """Catches: a fallback store that cannot reach the graph it falls back to."""
        root = build(self.tmp / "s", index="none")
        loaded = store.open_store(root, self.cache)
        self.addCleanup(loaded.close)
        self.assertIsInstance(loaded.index, Fallback)
        expected = json.loads(gzip.decompress((root / GRAPH).read_bytes()))
        self.assertEqual(loaded.layer("graph"), expected)
        self.assertIs(loaded.layer("graph"), loaded.layer("graph"))

    def test_an_absent_layer_reads_as_none(self):
        """Catches: an absent layer raising, or reading a path the manifest says is
        not there (DESIGN.md 9.4: `context` is absent)."""
        loaded = store.open_store(build(self.tmp / "s"), self.cache)
        self.addCleanup(loaded.close)
        self.assertIsNone(loaded.layer("context"))
        self.assertIsNone(loaded.layer("no-such-layer"))

    def test_the_store_tree_is_byte_identical_after_start_up_and_queries(self):
        """Catches: any write into the store - a decompressed index, a journal or
        `-shm` beside a plain index, a touched file - in each of the index modes."""
        clock = lambda: datetime(2026, 10, 20, tzinfo=timezone.utc)  # noqa: E731
        for mode in ("gz", "plain", "none"):
            with self.subTest(index=mode):
                root = build(self.tmp / mode, index=mode)
                before = snapshot(root)
                session = store.Session(lambda r=root: store.open_store(r, self.cache), clock)
                loaded, _ = session.current()
                self.addCleanup(loaded.close)
                if isinstance(loaded.index, IndexHandle):
                    self.assertEqual(repo_names(loaded.index), REPOS)
                loaded.layer("graph")
                loaded.layer("provenance")
                session.current()
                self.assertEqual(snapshot(root), before)


if __name__ == "__main__":
    unittest.main()
