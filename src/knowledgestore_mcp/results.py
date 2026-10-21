"""What every tool returns around its answer (spec, Tools).

- `envelope` says which store, version and index answered, and which layers.
- `searched` and `empty`: an empty result always says what was searched, with
  the store's own counts, and which layers were absent.
- `repository` is the three-way answer for a repository name: in the estate, not
  in the estate (with near misses), or referenced by the estate but not held.
- `page` cuts a list so the whole response fits the cap, without cutting an
  item, and says so.

Store text is data. The only prose composed here is `searched`'s sentence, built
from counts and layer names: the ones this server knows, and answers layers the
manifest declares, which are store structure and must be plain identifiers. So
nothing a person wrote into the store can reach it; every store value goes in a
named field.
"""

import difflib
import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import graph
from .index import IndexHandle
from .locate import OutsideStore, contained
from .manifest import KNOWN_LAYERS
from .store import Notice, Store

# The response cap. The spec sets it above the largest per-repository file a store
# holds, and that figure belongs to a consuming store; this default is overridden
# by `--max-response-bytes`.
DEFAULT_MAX_RESPONSE_BYTES = 512 * 1024

# The relationships registry, at the library's fixed layout. The manifest names the
# layer by its `by-repo/` directory, so the registry beside it is read from here.
REGISTRY = Path("knowledge") / "relationships" / "repository-dependencies.json"

# A layer name a sentence may carry: one plain identifier.
_LAYER_NAME = re.compile(r"[A-Za-z0-9_-]+")

# Plan rulings, Near misses are deterministic.
NEAR_MISS_COUNT = 5
NEAR_MISS_CUTOFF = 0.75

INDEX_PRESENT = "present"
INDEX_ABSENT = "absent: graph read whole"


# A lone UTF-16 surrogate, which `json.loads` accepts from `"\\ud800"` but UTF-8
# cannot carry.
_SURROGATE = re.compile("[\ud800-\udfff]")


def encode(value: Any) -> bytes:
    """The bytes a response is sent as, and so the bytes the cap measures: compact,
    key-sorted UTF-8 JSON. A lone surrogate in store text becomes U+FFFD, the
    replacement character, rather than failing the whole response.

    The cap bounds this text, the tool result the model reads. The JSON-RPC frame
    around it (the envelope, and an escape before every quote) is transport, and
    is not counted."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _SURROGATE.sub("\ufffd", text).encode("utf-8")


# An absolute path on this host, consumed whole:
# - a `file:` URI;
# - a quoted path, to its closing quote, so a space inside it is part of it;
# - an unquoted path: a `/` that starts a word, or follows a colon (`error:/x`),
#   but not `//` (a URL's `https://`), with every following space-separated piece
#   that still holds a `/` (`/x/Application Support/y`).
_PIECE = r"[^\s'\",;()]"
_HOST_PATH = re.compile(
    rf"file:/+{_PIECE}*(?:\s+{_PIECE}*/{_PIECE}*)*"
    r"|'/[^']*'"
    r'|"/[^"]*"'
    rf"|(?<![\w.~/-])/(?!/){_PIECE}+(?:\s+{_PIECE}*/{_PIECE}*)*"
)


def _replace(match: re.Match[str]) -> str:
    found = match.group(0)
    quote = found[0] if found[0] in "'\"" else ""
    return f"{quote}<host path>{quote}"


def without_host_paths(text: str) -> str:
    """`text` with every absolute path replaced: a message the server composes
    around an OS or SQLite error never says where anything is on this host. A
    quoted path keeps its quotes."""
    return _HOST_PATH.sub(_replace, text)


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
    """The estate's repositories, sorted: the index's `repos`, or, without it, the
    graph's and the ticket layer's, collected as the index builder collects them;
    and in both modes every repository the relationships layer has a `by-repo` file
    for, since that writer runs over every clone, including one with no graph node
    and no ticket. Never the empty name: a node with no repository is not one,
    whatever the index holds."""
    if isinstance(store.index, IndexHandle):
        # An index built before the library stopped listing "" still holds it; the
        # filter below drops it in every mode.
        rows = store.index.connection.execute("SELECT name FROM repos")
        names = {name for (name,) in rows}
    else:
        names = set(graph.read(store).repositories()) | store.memo(
            "intent.repositories", lambda: graph.ticket_repositories(store.read("intent"))
        )
    names |= set(by_repo_files(store))
    return sorted(name for name in names if name)


@dataclass(frozen=True)
class ByRepo:
    """The `by-repo/` listing: each file by its stem, and how many entries were
    skipped because they resolve outside the store."""

    files: dict[str, Path]
    skipped: int


def by_repo_listing(store: Store) -> ByRepo:
    """The `by-repo/` directory the manifest names for the relationships layer,
    listed once per loaded store and never parsed here. An entry - or the directory
    itself - that resolves outside the store is skipped and counted, never followed
    and never quoted: one hostile link must not fail every lookup. Empty when the
    layer is absent."""
    return store.memo("relationships.by_repo", lambda: _list_by_repo(store))


def by_repo_files(store: Store) -> dict[str, Path]:
    """Each `by-repo/` file by its stem. A name is matched against these keys; it
    never becomes part of a path."""
    return by_repo_listing(store).files


def _list_by_repo(store: Store) -> ByRepo:
    entry = store.manifest.layers.get("relationships")
    if not isinstance(entry, dict) or entry.get("present") is not True:
        return ByRepo({}, 0)
    paths = entry.get("paths")
    skipped = 0
    for rel in paths if isinstance(paths, list) else []:
        if not isinstance(rel, str) or not rel.endswith("/"):
            continue
        try:
            directory = contained(store.root, rel)
        except OutsideStore:
            skipped += 1
            continue
        if not directory.is_dir():
            continue
        files, outside = _by_repo_directory(store, directory)
        return ByRepo(files, skipped + outside)
    return ByRepo({}, skipped)


def _by_repo_directory(store: Store, directory: Path) -> tuple[dict[str, Path], int]:
    """The `.json` files of one `by-repo/` directory by stem, and how many entries
    resolve outside the store."""
    files: dict[str, Path] = {}
    skipped = 0
    for path in sorted(directory.iterdir()):
        if path.suffix != ".json":
            continue
        try:
            resolved = contained(store.root, path.relative_to(store.root))
        except OutsideStore:
            skipped += 1
            continue
        if resolved.is_file():
            files[path.stem] = resolved
    return files, skipped


def join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def layers_clause(names: list[str], verb: str) -> str:
    """`the context layer is absent`, `the context and history layers are absent`."""
    noun = "layer is" if len(names) == 1 else "layers are"
    return f"the {join_names(names)} {noun} {verb}"


def nameable_layers(store: Store) -> dict[str, Any]:
    """The layers a sentence may name, with their manifest entries: the ones this
    server knows, and each `extra_layers` entry of kind `answers` whose name is a
    plain identifier."""
    names: dict[str, Any] = {name: store.manifest.layers.get(name) for name in KNOWN_LAYERS}
    for name, entry in store.manifest.extra_layers.items():
        if (
            name not in names
            and isinstance(entry, dict)
            and entry.get("kind") == "answers"
            and _LAYER_NAME.fullmatch(name)
        ):
            names[name] = entry
    return names


def searched(store: Store, layers: Sequence[str]) -> str:
    """What an empty answer searched: `searched 12 repositories in the graph layer;
    the relationships layer is absent`. A layer whose presence the store did not
    record (`present: null`) is said to be not recorded, never absent."""
    nameable = nameable_layers(store)
    refused = set(layers) - set(nameable)
    if refused:
        # Counted, not quoted: a refused name may be anything the store holds.
        raise ValueError(f"{len(refused)} layer name(s) this server cannot name")
    present, absent, unrecorded = [], [], []
    for name in sorted(set(layers)):
        entry = nameable[name]
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
        parts = [f"searched {count} {noun} in the {join_names(present)} {layer}"]
    else:
        parts = ["searched no layer"]
    if absent:
        parts.append(layers_clause(absent, "absent"))
    if unrecorded:
        parts.append(layers_clause(unrecorded, "not recorded"))
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
    if len(folded) == 1:
        return Known(folded[0], matched_case_insensitively=True)
    if folded:
        # Case-only twins: the name cannot say which is meant, so offer each.
        return Unknown(tuple(folded))
    entries = outside_estate(store, name)
    if entries:
        referrers = {r for entry in entries for r in entry.referenced_by}
        return NotHeld(name, tuple(sorted(referrers)))
    return Unknown(near_misses(name, names))


def near_misses(name: str, names: list[str]) -> tuple[str, ...]:
    """The top five by similarity, a tie broken by name ascending, at or above the
    cutoff. Every candidate is scored first: `difflib.get_close_matches` keeps a
    tie at its fifth place by name descending, so it would drop the wrong names."""
    # Scored as difflib scores them: the candidate is the first sequence.
    scored = sorted((-difflib.SequenceMatcher(None, c, name).ratio(), c) for c in names)
    close = [c for negative, c in scored if -negative >= NEAR_MISS_CUTOFF]
    return tuple(close[:NEAR_MISS_COUNT])


@dataclass(frozen=True)
class Outside:
    """One target outside the estate, under one kind, as the registry holds it.
    `target` is the registry's key, which may be `<org>/<repo>` for a name asked
    without its organisation."""

    kind: str
    target: str
    references: Any
    referenced_by: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "target": self.target,
            "references": self.references,
            "referenced_by": list(self.referenced_by),
        }


def outside_estate(store: Store, name: str) -> tuple[Outside, ...]:
    """Every registry entry `name` names, by (kind, target): its own key, and a
    Terraform-style `<org>/<name>` whose organisation is the store's own, compared
    ignoring case with `lower`, as the library's resolver compares it. Another
    organisation's repository of the same name is a different target."""
    return _outside_estate(store).get(name, ())


def targets_outside_estate(store: Store) -> tuple[Outside, ...]:
    """Every registry entry once, by (kind, target)."""
    unique = {(o.kind, o.target): o for found in _outside_estate(store).values() for o in found}
    return tuple(unique[key] for key in sorted(unique))


def _outside_estate(store: Store) -> dict[str, tuple[Outside, ...]]:
    """Each name a target outside the estate answers to, with its entries. Read
    once per loaded store."""
    return store.memo("relationships.outside_estate", lambda: _read_outside_estate(store))


def _outside(kind: str, target: str, detail: Any) -> Outside:
    """One registry entry, its referrers de-duplicated and sorted."""
    detail = detail if isinstance(detail, dict) else {}
    referrers = detail.get("referenced_by")
    return Outside(
        kind,
        target,
        detail.get("references"),
        tuple(sorted(set(referrers))) if isinstance(referrers, list) else (),
    )


def _outside_names(target: str, own: str) -> set[str]:
    """The names a registry target answers to: its key, and `<repo>` for a key
    `<org>/<repo>` under the store's own organisation `own` (lower-cased)."""
    names = {target}
    org, slash, rest = target.partition("/")
    if own and slash and org.lower() == own and rest and "/" not in rest:
        names.add(rest)
    return names


def _read_outside_estate(store: Store) -> dict[str, tuple[Outside, ...]]:
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
    # `lower`, not `casefold`: the library's resolver compares organisations so.
    own = store.manifest.github_org.lower()
    found: dict[str, list[Outside]] = {}
    for kind, targets in (by_kind if isinstance(by_kind, dict) else {}).items():
        for target, detail in (targets if isinstance(targets, dict) else {}).items():
            outside = _outside(kind, target, detail)
            for name in _outside_names(target, own):
                found.setdefault(name, []).append(outside)
    return {
        name: tuple(sorted(entries, key=lambda o: (o.kind, o.target)))
        for name, entries in found.items()
    }


# --- Paging -----------------------------------------------------------------------


# A cursor is `<page>.<first item>.<tag>`. Its token is rebuilt from the parsed
# integers and compared whole, so any other spelling of a number is refused.
_CURSOR = re.compile(r"([0-9]{1,9})\.([0-9]{1,9})\.([0-9a-f]{16})")

# The fields `page` adds to the response; the frame may not carry them.
PAGING_FIELDS = ("items", "total", "page", "next_page", "oversize")


class CursorRefused(ValueError):
    """A `next_page` that does not belong to this listing of this store."""

    def __init__(self, cursor: str):
        shown = cursor if len(cursor) <= 64 else cursor[:64] + "…"
        super().__init__(
            f"page cursor {shown!r} is not from this listing of the current store "
            "(the store may have changed since): start again without a page cursor"
        )


def page(
    items: list,
    cap_bytes: int,
    cursor: str | None,
    *,
    digest: str,
    key: Callable[[Any], Any],
    frame: dict,
) -> dict:
    """The whole response for one page of `items`: `frame` (the envelope, and
    anything else the tool returns beside the list) plus `items`, `total`, `page`
    and `next_page`.

    Items sort by `key`, with the item's own bytes as the tiebreak, so equal keys
    still order the same on every call. A page holds the whole items that keep the
    response, as `encode` writes it, within `cap_bytes`. An item that does not fit
    alone is a page of its own, flagged `oversize`.

    `cursor` is None for the first page, or a `next_page` from the same listing,
    cap and store `digest`; anything else raises `CursorRefused`. It names the
    first item of its page, so a frame that grows between calls (a notice) cannot
    skip or repeat an item.
    """
    clash = sorted(set(frame) & set(PAGING_FIELDS))
    if clash:
        raise ValueError(f"the frame carries paging fields: {clash}")
    encoded = sorted(((key(item), encode(item), item) for item in items), key=lambda t: t[:2])
    total = len(encoded)
    listing = hashlib.sha256()
    for _, item_bytes, _ in encoded:
        listing.update(len(item_bytes).to_bytes(8, "big") + item_bytes)
    bound = f"{digest}\0{cap_bytes}\0{listing.hexdigest()}"

    def token(number: int, start: int) -> str:
        tag = hashlib.sha256(f"{bound}\0{number}\0{start}".encode("utf-8")).hexdigest()
        return f"{number}.{start}.{tag[:16]}"

    number, start = (1, 0) if cursor is None else _parse(cursor, token)

    def following(end: int) -> str | None:
        return token(number + 1, end) if end < total else None

    def response(end: int) -> dict:
        return {
            **frame,
            "items": [item for _, _, item in encoded[start:end]],
            "total": total,
            "page": number,
            "next_page": following(end),
        }

    def fits(end: int, body: int) -> bool:
        # The items of [start, end) add `body`, their bytes and commas, to `[]`.
        shell = {**response(start), "next_page": following(end)}
        return len(encode(shell)) + body <= cap_bytes

    end, body = start, 0
    if start < total:
        end, body = start + 1, len(encoded[start][1])
        while end < total and fits(end + 1, body + 1 + len(encoded[end][1])):
            body += 1 + len(encoded[end][1])
            end += 1
    result = response(end)
    # Over the cap only when nothing smaller could be sent: one item that does not
    # fit alone, or a frame that is over the cap by itself.
    if len(encode(result)) > cap_bytes:
        result["oversize"] = True
    return result


def _parse(cursor: str, token: Callable[[int, int], str]) -> tuple[int, int]:
    match = _CURSOR.fullmatch(cursor)
    if match is None:
        raise CursorRefused(cursor)
    number, start = int(match[1]), int(match[2])
    if token(number, start) != cursor:
        raise CursorRefused(cursor)
    return number, start
