"""The store a call is answered from, and the check that it is still current.

`Session.current()` re-reads the small manifest on every call and compares its
`digest`, which is computed from the layers' content: a rebuild or pull that
changes the store reloads it, and a clone or identical rebuild does not. A
manifest without a digest is compared by the sha256 of its bytes instead.

A pull can write the new manifest before the new index archive, so each call also
stats the archive. A changed (size, mtime) costs one hash, and reloads only when
the archive's content is not the one the loaded index came from.
"""

import gzip
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import index, manifest
from .index import Fallback, IndexHandle
from .locate import MANIFEST, OutsideStore, contained
from .manifest import Manifest, Refused


@dataclass(frozen=True)
class Notice:
    """Something about the store the result must say, beside the answer."""

    message: str


RELOADED = Notice("reloaded: store digest changed")
RELOADED_ARCHIVE = Notice("reloaded: index archive changed")
DEFERRED = Notice("reload deferred: manifest unreadable")


class Store:
    """One loaded store. Layers other than the index are read on first use.

    A `Store` is valid until `Session.current()` reloads: the reload closes its
    index connection. Take the store from `current()` per call; do not keep it.
    """

    def __init__(
        self,
        root: Path,
        manifest: Manifest,
        index: IndexHandle | Fallback,
        archive_state: tuple[int, int] | None = None,
    ):
        self.root = root
        self.manifest = manifest
        self.index = index
        # The archive's (size, mtime_ns) when the index was opened.
        self.archive_state = archive_state
        self._layers: dict[str, Any] = {}
        self._memo: dict[str, Any] = {}

    def layer(self, name: str) -> Any:
        """The parsed JSON of a file layer, or None when the manifest says it is
        absent. The path comes from the manifest and stays inside the store."""
        if name not in self._layers:
            self._layers[name] = self._read(name)
        return self._layers[name]

    def memo(self, key: str, compute: Callable[[], Any]) -> Any:
        """`compute()` once per loaded store: a reload builds a new `Store`, so
        anything kept here is read again from the store that replaced it."""
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    def _read(self, name: str) -> Any:
        entry = self.manifest.layers.get(name)
        if not isinstance(entry, dict) or entry.get("present") is not True:
            return None
        for rel in entry.get("paths") or []:
            if rel.endswith("/"):
                raise ValueError(f"layer {name} is a directory, not a file")
            path = contained(self.root, rel)
            if path.is_file():
                data = path.read_bytes()
                if path.suffix == ".gz":
                    data = gzip.decompress(data)
                return json.loads(data)
        return None

    def close(self) -> None:
        if isinstance(self.index, IndexHandle):
            self.index.close()


def open_store(
    root: Path, cache_home: Path, *, max_index_bytes: int = index.DEFAULT_MAX_INDEX_BYTES
) -> Store:
    loaded = manifest.load(root)
    # Stat before opening: a change in between is then seen as a change next call.
    state = index.archive_state(root)
    opened = index.open_index(root, loaded, cache_home, max_bytes=max_index_bytes)
    return Store(root, loaded, opened, state)


def _manifest_sha256(root: Path) -> str | None:
    try:
        return hashlib.sha256(contained(root, MANIFEST).read_bytes()).hexdigest()
    except (OSError, OutsideStore):
        return None


class Session:
    """The current store, reloaded when the manifest digest on disk changes.

    A `Store` returned by `current()` stays valid until a later call reloads.
    """

    def __init__(self, store_factory: Callable[[], Store], clock: Callable[[], datetime]):
        self._factory = store_factory
        self._clock = clock
        self._store = store_factory()
        self._manifest_sha256 = _manifest_sha256(self._store.root)
        self._archive_state = self._store.archive_state

    def current(self) -> tuple[Store, Notice | None]:
        root = self._store.root
        try:
            on_disk = manifest.load(root)
        except Refused:
            # Mid-pull, the manifest may be half written: answer from what is loaded.
            return self._store, DEFERRED
        if on_disk.digest or self._store.manifest.digest:
            manifest_changed = on_disk.digest != self._store.manifest.digest
        else:
            manifest_changed = _manifest_sha256(root) != self._manifest_sha256
        if manifest_changed:
            return self._reload(RELOADED)
        state = index.archive_state(root)
        if state != self._archive_state:
            loaded = self._store.index
            known = loaded.archive_sha256 if isinstance(loaded, IndexHandle) else None
            if state is None or known is None or index.archive_sha256(root) != known:
                return self._reload(RELOADED_ARCHIVE)
            # Touched, not changed: remember the new stat so it is hashed once.
            self._archive_state = state
        return self._store, None

    def _reload(self, notice: Notice) -> tuple[Store, Notice | None]:
        # Hash before loading: a change in between then shows as a change next call.
        manifest_sha256 = _manifest_sha256(self._store.root)
        try:
            fresh = self._factory()
        except Refused:
            return self._store, DEFERRED
        previous, self._store = self._store, fresh
        self._manifest_sha256 = manifest_sha256
        self._archive_state = fresh.archive_state
        previous.close()
        return fresh, notice

    def stale_days(self) -> int | None:
        """Whole days from `sources_as_of` to the clock's now, or None if unrecorded."""
        recorded = self._store.manifest.sources_as_of
        if not recorded:
            return None
        try:
            as_of = datetime.fromisoformat(recorded)
        except ValueError:
            return None
        now = self._clock()
        if as_of.tzinfo is None or now.tzinfo is None:
            # An instant without an offset cannot be placed against another.
            return None
        return (now - as_of).days
