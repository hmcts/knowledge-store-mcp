"""The store a call is answered from, and the check that it is still current.

`Session.current()` re-reads the small manifest on every call and compares its
`digest`, which is computed from the layers' content: a rebuild or pull that
changes the store reloads it, and a clone or identical rebuild does not.
"""

import gzip
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import index, manifest
from .index import Fallback, IndexHandle
from .locate import contained
from .manifest import Manifest, Refused


@dataclass(frozen=True)
class Notice:
    """Something about the store the result must say, beside the answer."""

    message: str


RELOADED = Notice("reloaded: store digest changed")
DEFERRED = Notice("reload deferred: manifest unreadable")


class Store:
    """One loaded store. Layers other than the index are read on first use.

    A `Store` is valid until `Session.current()` reloads: the reload closes its
    index connection. Take the store from `current()` per call; do not keep it.
    """

    def __init__(self, root: Path, manifest: Manifest, index: IndexHandle | Fallback):
        self.root = root
        self.manifest = manifest
        self.index = index
        self._layers: dict[str, Any] = {}

    def layer(self, name: str) -> Any:
        """The parsed JSON of a file layer, or None when the manifest says it is
        absent. The path comes from the manifest and stays inside the store."""
        if name not in self._layers:
            self._layers[name] = self._read(name)
        return self._layers[name]

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


def open_store(root: Path, cache_home: Path) -> Store:
    loaded = manifest.load(root)
    return Store(root, loaded, index.open_index(root, loaded, cache_home))


class Session:
    """The current store, reloaded when the manifest digest on disk changes.

    A `Store` returned by `current()` stays valid until a later call reloads.
    """

    def __init__(self, store_factory: Callable[[], Store], clock: Callable[[], datetime]):
        self._factory = store_factory
        self._clock = clock
        self._store = store_factory()

    def current(self) -> tuple[Store, Notice | None]:
        try:
            on_disk = manifest.load(self._store.root)
        except Refused:
            # Mid-pull, the manifest may be half written: answer from what is loaded.
            return self._store, DEFERRED
        if on_disk.digest == self._store.manifest.digest:
            return self._store, None
        try:
            fresh = self._factory()
        except Refused:
            return self._store, DEFERRED
        previous, self._store = self._store, fresh
        previous.close()
        return fresh, RELOADED

    def stale_days(self) -> int | None:
        """Whole days from `sources_as_of` to the clock's now, or None if unrecorded."""
        recorded = self._store.manifest.sources_as_of
        if not recorded:
            return None
        try:
            as_of = datetime.fromisoformat(recorded)
        except ValueError:
            return None
        if as_of.tzinfo is None:
            # An instant without an offset cannot be placed against the clock.
            return None
        return (self._clock() - as_of).days
