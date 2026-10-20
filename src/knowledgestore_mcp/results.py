"""What every tool returns around its answer (spec, Tools).

- `envelope` says which store, version and index answered, and which layers.
- `searched` and `empty`: an empty result always says what was searched, with
  the store's own counts, and which layers were absent.
- `repository` is the three-way answer for a repository name: in the estate, not
  in the estate (with near misses), or referenced by the estate but not held.
- `page` cuts a list at the response cap without cutting an item, and says so.

Store text is data. The only prose composed here is `searched`'s sentence, built
from counts and the layer names this server knows, so nothing a person wrote
into the store can reach it; every store value goes in a named field.
"""

import difflib
import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .index import IndexHandle
from .locate import contained
from .manifest import KNOWN_LAYERS
from .store import Notice, Store

# The response cap. The spec sets it above the largest per-repository file a store
# holds, and that figure belongs to a consuming store; this default is overridden
# by `--max-response-bytes`.
DEFAULT_MAX_RESPONSE_BYTES = 512 * 1024

# The relationships registry, at the library's fixed layout. The manifest names the
# layer by its `by-repo/` directory, so the registry beside it is read from here.
REGISTRY = Path("knowledge") / "relationships" / "repository-dependencies.json"

# Plan rulings, Near misses are deterministic.
NEAR_MISS_COUNT = 5
NEAR_MISS_CUTOFF = 0.75

INDEX_PRESENT = "present"
INDEX_ABSENT = "absent: graph read whole"


def encode(value: Any) -> bytes:
    """The bytes a response is measured in: compact, key-sorted UTF-8 JSON."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


# --- The envelope -----------------------------------------------------------------


def envelope(store: Store, layers_used: list[str], notice: Notice | None) -> dict:
    found = store.manifest
    return {
        "store": {
            "id": found.store_id,
            "format": {"major": found.major, "minor": found.minor},
            "library_version": found.library_version,
            "sources_as_of": found.sources_as_of,
            "index": INDEX_PRESENT if isinstance(store.index, IndexHandle) else INDEX_ABSENT,
            "layers_used": sorted(set(layers_used)),
            "notice": notice.message if notice else None,
        }
    }


# --- Absence ----------------------------------------------------------------------


def repository_names(store: Store) -> list[str]:
    """The estate's repositories, sorted: the index's `repos`, or the graph's."""
    if isinstance(store.index, IndexHandle):
        rows = store.index.connection.execute("SELECT name FROM repos ORDER BY name")
        return [name for (name,) in rows]
    graph = store.layer("graph")
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    return sorted({n["repo"] for n in nodes if isinstance(n, dict) and n.get("repo")})


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _layers(names: list[str], verb: str) -> str:
    """`the context layer is absent`, `the context and history layers are absent`."""
    noun = "layer is" if len(names) == 1 else "layers are"
    return f"the {_join(names)} {noun} {verb}"


def searched(store: Store, layers: Sequence[str]) -> str:
    """What an empty answer searched: `searched 12 repositories in the graph layer;
    the relationships layer is absent`. A layer whose presence the store did not
    record (`present: null`) is said to be not recorded, never absent."""
    unknown = sorted(set(layers) - set(KNOWN_LAYERS))
    if unknown:
        raise ValueError(f"not a layer this server reads: {unknown}")
    present, absent, unrecorded = [], [], []
    for name in sorted(set(layers)):
        entry = store.manifest.layers.get(name)
        state = entry.get("present") if isinstance(entry, dict) else False
        if state is True:
            present.append(name)
        elif state is None:
            unrecorded.append(name)
        else:
            absent.append(name)
    if present:
        count = len(repository_names(store))
        noun = "repository" if count == 1 else "repositories"
        layer = "layer" if len(present) == 1 else "layers"
        parts = [f"searched {count} {noun} in the {_join(present)} {layer}"]
    else:
        parts = ["searched no layer"]
    if absent:
        parts.append(_layers(absent, "absent"))
    if unrecorded:
        parts.append(_layers(unrecorded, "not recorded"))
    return "; ".join(parts)


def empty(searched: str) -> dict:
    """An empty result, which never stands alone: it says what was searched."""
    return {"items": [], "total": 0, "searched": searched}


# --- The three-way answer for a repository name ------------------------------------


@dataclass(frozen=True)
class Known:
    """In the estate. A tool may still have nothing to say about it."""

    name: str
    matched_case_insensitively: bool

    def as_dict(self) -> dict:
        return {
            "answer": "in the estate",
            "exact": self.name,
            "matched_case_insensitively": self.matched_case_insensitively,
        }


@dataclass(frozen=True)
class Unknown:
    """Not in the estate. Each near miss is a different repository, not the answer."""

    near_misses: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "answer": "not in the estate",
            "exact": None,
            "near_misses": [{"repository": n, "near_miss": True} for n in self.near_misses],
        }


@dataclass(frozen=True)
class NotHeld:
    """A target the estate references and no clone holds."""

    name: str
    referenced_by: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "answer": "referenced by the estate but not held",
            "exact": None,
            "target": self.name,
            "referenced_by": list(self.referenced_by),
        }


def repository(store: Store, name: str) -> Known | Unknown | NotHeld:
    """Look `name` up; it is never joined into a path."""
    names = repository_names(store)
    if name in names:
        return Known(name, matched_case_insensitively=False)
    folded = [n for n in names if n.casefold() == name.casefold()]
    if folded:
        return Known(folded[0], matched_case_insensitively=True)
    referrers = _outside_estate(store).get(name)
    if referrers is not None:
        return NotHeld(name, referrers)
    return Unknown(near_misses(name, names))


def near_misses(name: str, names: list[str]) -> tuple[str, ...]:
    """difflib's candidates, re-sorted by (similarity descending, name ascending):
    difflib orders a tie by name descending."""
    close = difflib.get_close_matches(name, names, n=NEAR_MISS_COUNT, cutoff=NEAR_MISS_CUTOFF)
    # Scored as difflib scores them: the candidate is the first sequence.
    return tuple(sorted(close, key=lambda c: (-difflib.SequenceMatcher(None, c, name).ratio(), c)))


def _outside_estate(store: Store) -> dict[str, tuple[str, ...]]:
    """Each target outside the estate, with every repository that references it."""
    entry = store.manifest.layers.get("relationships")
    if not isinstance(entry, dict) or entry.get("present") is not True:
        return {}
    path = contained(store.root, REGISTRY)
    if not path.is_file():
        return {}
    document = json.loads(path.read_bytes())
    by_kind = (
        document.get("unresolved_targets_outside_estate") if isinstance(document, dict) else None
    )
    referrers: dict[str, set[str]] = {}
    for targets in (by_kind or {}).values():
        for target, detail in (targets or {}).items():
            referrers.setdefault(target, set()).update(detail.get("referenced_by") or [])
    return {target: tuple(sorted(names)) for target, names in referrers.items()}


# --- Paging -----------------------------------------------------------------------


# ASCII only: `str.isdigit` accepts characters such as `²` that `int` refuses.
_DIGITS = re.compile(r"[0-9]+")


class CursorRefused(ValueError):
    """A `next_page` that does not belong to this listing of this store."""

    def __init__(self, cursor: str):
        super().__init__(
            f"page cursor {cursor!r} is not from this listing of the current store "
            "(the store may have changed since): start again without a page cursor"
        )


def page(
    items: list,
    cap_bytes: int,
    page: str | None,
    *,
    digest: str,
    key: Callable[[Any], Any],
) -> dict:
    """One page of `items`, sorted by `key` with the item's own bytes as the
    tiebreak, so equal keys still order the same on every call.

    A page holds whole items whose JSON array encodes to at most `cap_bytes`. An
    item that alone exceeds the cap is a page of its own, flagged `oversize`.
    `page` is None for the first page, or a `next_page` from the same listing on a
    store with the same `digest`; anything else raises `CursorRefused`.
    """
    encoded = sorted(((key(item), encode(item), item) for item in items), key=lambda t: t[:2])
    pages = _split([len(e) for _, e, _ in encoded], cap_bytes)
    listing = hashlib.sha256()
    for _, item_bytes, _ in encoded:
        listing.update(len(item_bytes).to_bytes(8, "big") + item_bytes)
    tag = hashlib.sha256(
        f"{digest}\0{cap_bytes}\0{listing.hexdigest()}".encode("utf-8")
    ).hexdigest()[:16]
    number = 1 if page is None else _number(page, tag, len(pages))
    start, end = pages[number - 1]
    result: dict[str, Any] = {
        "items": [item for _, _, item in encoded[start:end]],
        "total": len(encoded),
        "page": number,
        "next_page": f"{number + 1}.{tag}" if number < len(pages) else None,
    }
    if end - start == 1 and 2 + len(encoded[start][1]) > cap_bytes:
        result["oversize"] = True
    return result


def _split(sizes: list[int], cap: int) -> list[tuple[int, int]]:
    """[start, end) of each page. An array of n items costs 2 + sizes + (n - 1)."""
    pages: list[tuple[int, int]] = []
    start, used = 0, 2
    for i, size in enumerate(sizes):
        cost = size + (0 if i == start else 1)
        if i > start and used + cost > cap:
            pages.append((start, i))
            start, used, cost = i, 2, size
        # An item over the cap alone leaves `used` over it, so the next item
        # starts a new page: an oversize item is always a page of its own.
        used += cost
    if start < len(sizes) or not pages:
        pages.append((start, len(sizes)))
    return pages


def _number(cursor: str, tag: str, count: int) -> int:
    number, _, given = cursor.partition(".")
    if given != tag or not _DIGITS.fullmatch(number) or not 2 <= int(number) <= count:
        raise CursorRefused(cursor)
    return int(number)
