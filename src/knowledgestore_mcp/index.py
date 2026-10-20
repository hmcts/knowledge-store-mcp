"""Open the query index read-only (spec, Start-up step 3).

The committed `store.sqlite.gz` cannot be opened by SQLite, so it is decompressed once into
`<cache_home>/knowledge-store/<store.id>/<sha256 of the archive>.sqlite`. That
cache is the server's only write: it is never inside the store, an entry is
written to a temp file in the same directory and renamed into place, so no
partial entry is ever opened, and a failure to write it falls back to the graph.

The archive wins whenever it exists: the manifest's index digest describes it.
A plain `store.sqlite` is a local, gitignored build that goes stale after a pull,
so it is opened in place only when there is no archive.
"""

import gzip
import hashlib
import io
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .locate import OutsideStore, contained
from .manifest import Manifest

INDEX_PLAIN = Path("knowledge") / "index" / "store.sqlite"
INDEX_ARCHIVE = Path("knowledge") / "index" / "store.sqlite.gz"

CHUNK = 1 << 20

# The index schema major this server reads. A newer minor only adds to it.
# Source: SCHEMA in knowledge-store-builder src/knowledgestore/store_index.py
# (#457, feat/query-index at e4c55a2), written to meta.index_schema as "1.0".
SCHEMA_MAJOR = 1

# A temp file this old belongs to a writer that crashed: no decompress takes a day.
# A younger one may be a live peer's write, so it is never touched.
STALE_TEMP_SECONDS = 24 * 60 * 60

# `store.id` becomes one directory name, so it must be one plain path component.
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class IndexHandle:
    """A read-only connection to the index, and the file it reads."""

    connection: sqlite3.Connection
    path: Path

    def close(self) -> None:
        self.connection.close()


@dataclass(frozen=True)
class Fallback:
    """The index is not used; `reason` says why, and the graph is read instead."""

    reason: str


def cache_home(env: Mapping[str, str], home: Path) -> Path:
    """`${XDG_CACHE_HOME:-~/.cache}`. A relative value is ignored, as the XDG base
    directory specification says: it would put the cache under the working
    directory, which may be the store."""
    value = env.get("XDG_CACHE_HOME") or ""
    return Path(value) if Path(value).is_absolute() else home / ".cache"


def open_index(root: Path, manifest: Manifest, cache_home: Path) -> IndexHandle | Fallback:
    try:
        plain = contained(root, INDEX_PLAIN)
        archive = contained(root, INDEX_ARCHIVE)
    except OutsideStore as error:
        return Fallback(f"the index is outside the store: {error}")
    if not archive.is_file():
        if plain.is_file():
            # Not immutable: a rebuild may rewrite this file while the server runs.
            return _connect(plain, immutable=False)
        return Fallback(f"no index in the store ({INDEX_ARCHIVE.as_posix()} or {INDEX_PLAIN.name})")

    if not _SAFE_ID.fullmatch(manifest.store_id):
        return Fallback(f"store.id {manifest.store_id!r} cannot name a cache directory")
    directory = cache_home / "knowledge-store" / manifest.store_id
    resolved = directory.resolve()
    if resolved == root or root in resolved.parents:
        return Fallback(f"the index cache {directory} would be inside the store")
    try:
        entry = _cached(archive, directory)
    except (OSError, EOFError, zlib.error) as error:
        return Fallback(f"the index cache could not be written: {error}")
    # Immutable is safe here: an entry is named by its content, appears only by an
    # atomic rename, and is never written again. Skipping SQLite's locking also
    # means a peer server removing the entry cannot disturb an open handle.
    return _connect(entry, immutable=True)


class _HashingReader:
    """Hashes the archive's bytes as gzip reads them, so the entry is named by the
    bytes actually decompressed even if the archive changes mid-read (a pull)."""

    def __init__(self, raw):
        self._raw = raw
        self.sha256 = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self._raw.read(size)
        self.sha256.update(data)
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        # gzip seeks only to rewind, which a single forward read never does.
        raise io.UnsupportedOperation("the archive is read once, forwards")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _cached(archive: Path, directory: Path) -> Path:
    """The cache entry for `archive`, decompressing it only when absent."""
    entry = directory / f"{_sha256(archive)}.sqlite"
    if not entry.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        # The pid says whose file it is; the random suffix keeps two threads of
        # one process, which share a pid, from writing the same temp file.
        fd, name = tempfile.mkstemp(dir=directory, prefix=f"{entry.name}.tmp-{os.getpid()}-")
        temp = Path(name)
        try:
            with os.fdopen(fd, "wb") as out, archive.open("rb") as raw:
                reader = _HashingReader(raw)
                with gzip.GzipFile(fileobj=reader, mode="rb") as stream:
                    shutil.copyfileobj(stream, out, CHUNK)
            entry = directory / f"{reader.sha256.hexdigest()}.sqlite"
            os.replace(temp, entry)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    _remove_others(directory, entry)
    return entry


def _remove_others(directory: Path, keep: Path) -> None:
    """Remove this store id's other entries, and temp files a crashed writer left.
    A young temp file may be a live peer's write and is kept; a file a peer has
    already removed is not an error."""
    stale_before = time.time() - STALE_TEMP_SECONDS
    for other in directory.iterdir():
        try:
            if other.name.endswith(".sqlite"):
                remove = other.name != keep.name
            else:
                remove = ".sqlite.tmp-" in other.name and other.stat().st_mtime < stale_before
            if remove:
                other.unlink()
        except OSError:
            pass


def _connect(path: Path, *, immutable: bool) -> IndexHandle | Fallback:
    uri = path.as_uri() + "?mode=ro" + ("&immutable=1" if immutable else "")
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as error:
        return Fallback(f"the index {path} could not be opened: {error}")
    try:
        connection.execute("PRAGMA query_only=1")
        row = connection.execute("SELECT value FROM meta WHERE key = 'index_schema'").fetchone()
    except sqlite3.Error as error:
        connection.close()
        return Fallback(f"the index {path} could not be read: {error}")
    schema = row[0] if row else None
    if _major(schema) != SCHEMA_MAJOR:
        connection.close()
        return Fallback(
            f"index schema {schema!r} is not one this server reads (reads major {SCHEMA_MAJOR})"
        )
    return IndexHandle(connection, path)


def _major(schema: object) -> int | None:
    if not isinstance(schema, str):
        return None
    major = schema.split(".", 1)[0]
    return int(major) if major.isdigit() else None
