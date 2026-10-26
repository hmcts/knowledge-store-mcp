"""One way to open the library's stores for a contract test, in both read modes.

`store/` holds the library's query index; `no-index/` is the same inputs built
without the `index` stage, so the server reads the graph whole. A contract holds
in both, so each contract is written once, as a mixin, and `both_modes` makes it
two test classes:

    class GraphContract(Contract):
        def test_...(self): ...

    GraphWithIndex, GraphWithoutIndex = harness.both_modes(GraphContract)

`Contract` is `LibraryStoreCase` to the type checker and `object` at run time,
so the mixin is never collected and run on its own, with no store.

Git shas are the one input read rather than written as literals: the generator
records each clone's HEAD in `inputs.json`, from the clones it made.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

from knowledgestore_mcp import store
from library_store.generate import INPUTS, NO_INDEX, OUT

# The session's clock: 18 days and 3 hours after the newest input commit.
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
SHAS: dict[str, str] = json.loads(INPUTS.read_text("utf-8"))["shas"]


class LibraryStoreCase(unittest.TestCase):
    """A session over `STORE`, with its index cache in a fresh directory, so no
    test reads another's cache or the user's. `INDEXED` says whether `STORE` holds
    the query index."""

    STORE: Path
    INDEXED: bool

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = Path(tmp.name).resolve() / "cache"
        patcher = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session = store.Session(lambda: store.open_store(self.STORE, cache), lambda: NOW)
        self.addCleanup(lambda: self.session.current()[0].close())

    @property
    def reader(self) -> str:
        """The layer every graph answer comes from in this mode."""
        return "index" if self.INDEXED else "graph"


if TYPE_CHECKING:
    Contract = LibraryStoreCase
else:
    Contract = object


def both_modes(mixin: type) -> tuple[type, type]:
    """`mixin`'s tests as two test classes: over `store/`, with its index, and over
    `no-index/`, where the graph answers."""
    with_index = type(
        f"{mixin.__name__}WithIndex",
        (mixin, LibraryStoreCase),
        {"STORE": OUT, "INDEXED": True, "__module__": mixin.__module__},
    )
    without_index = type(
        f"{mixin.__name__}WithoutIndex",
        (mixin, LibraryStoreCase),
        {"STORE": NO_INDEX, "INDEXED": False, "__module__": mixin.__module__},
    )
    return with_index, without_index
