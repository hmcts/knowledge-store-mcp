"""The graph tools (spec, Tools): `store_info`, `find_repo`, `search`, `node`,
`neighbours` and `community`.

Each is a plain function of `(Session, ...) -> dict`; the server registers them.
Every answer comes from the query index when it is open, and from the graph,
read whole, when it is not: the two sources sit behind one small reader, so a
tool gives the same items either way, and only the envelope says which answered.

Every SQL statement is a constant with bound parameters. Search matches word
prefixes: the caller's text is split into words by FTS5's own `unicode61`
tokenizer, the one the index was built with, and each word becomes a quoted
prefix term. So FTS syntax in the text (`NEAR`, `*`, `OR`, a column filter, an
unbalanced quote) is only ever a word, never an operator. Without the index, the
graph's labels go into an FTS5 table in memory and the same query runs there, so
the two modes match and rank alike by construction rather than by imitation.
"""

import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from typing import Any

from . import results
from .index import IndexHandle
from .results import DEFAULT_MAX_RESPONSE_BYTES, Known
from .store import Notice, Session, Store

DEFAULT_SEARCH_LIMIT = 20
MAX_DEPTH = 2

# The three flags the manifest may record, each of which may be null (unknown).
FLAGS = (("graph", "source_urls"), ("intent", "reach"), ("deployments", "present"))

# A NUL ends a C string inside SQLite's FTS5 parser, and a lone surrogate cannot be
# bound as UTF-8: neither is text a label can hold.
_UNSEARCHABLE = re.compile("[\x00\ud800-\udfff]")


class ArgumentRefused(ValueError):
    """A tool argument this server will not act on; the message says why. It never
    quotes store text, and quotes caller input only as a number."""


# --- Arguments --------------------------------------------------------------------


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise ArgumentRefused(f"{what} must be text")
    if _UNSEARCHABLE.search(value):
        raise ArgumentRefused(f"{what} holds a NUL or a lone surrogate, which no label can")
    return value


def _count(value: object, what: str, allowed: str, ok: Callable[[int], bool]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not ok(value):
        raise ArgumentRefused(f"{what} must be {allowed}")
    return value


# --- The search query ---------------------------------------------------------------

# The tokenizer the index's `labels` table declares (index schema 1.0).
_TOKENIZER = "unicode61"


def _query(text: str) -> str | None:
    """The FTS5 query for `text`: each distinct word, as `unicode61` splits and folds
    it, quoted and starred, so every word must begin some word of the label. None
    when `text` holds no word. SQLite tokenizes, not Python: its Unicode tables are
    its own, and an imitation would disagree with the index on some characters."""
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(f"CREATE VIRTUAL TABLE words USING fts5(text, tokenize='{_TOKENIZER}')")
        db.execute("CREATE VIRTUAL TABLE terms USING fts5vocab(words, instance)")
        db.execute("INSERT INTO words(text) VALUES (?)", (text,))
        terms = [term for (term,) in db.execute("SELECT term FROM terms ORDER BY offset")]
    words = dict.fromkeys(terms)
    if not words:
        return None
    return " ".join('"' + word.replace('"', '""') + '"*' for word in words)


# --- The two readers --------------------------------------------------------------

# One node as every tool returns it.
Node = dict[str, Any]
# (direction from the asking node, relation, the other node's id)
Edge = tuple[str, str, str]

_NODE_SELECT = (
    "SELECT n.id, n.label, n.kind, r.name, f.path, n.url, n.community"
    " FROM nodes n JOIN files f ON f.k = n.file JOIN repos r ON r.k = f.repo"
)


def _indexed_node(row: tuple) -> Node:
    node_id, label, kind, repo, path, url, community = row
    return {
        "id": node_id,
        "label": label,
        "repo": repo,
        "kind": kind,
        # A structural node's file row has an empty path: it has no source file.
        "file": path or None,
        "url": url,
        "community": community,
    }


class _Indexed:
    """The graph as the query index holds it. Read with constant, bound SQL."""

    layer = "index"

    def __init__(self, connection: sqlite3.Connection):
        self._db = connection

    def node(self, node_id: str) -> Node | None:
        row = self._db.execute(_NODE_SELECT + " WHERE n.id = ?", (node_id,)).fetchone()
        return _indexed_node(row) if row else None

    def edges(self, node_id: str, relation: str | None) -> list[Edge]:
        rows = self._db.execute(
            "SELECT 'out', rel.name, t.id FROM nodes s"
            " JOIN edges e ON e.source = s.k JOIN relations rel ON rel.k = e.relation"
            " JOIN nodes t ON t.k = e.target"
            " WHERE s.id = ?1 AND (?2 IS NULL OR rel.name = ?2)"
            " UNION ALL "
            "SELECT 'in', rel.name, s.id FROM nodes t"
            " JOIN edges e ON e.target = t.k JOIN relations rel ON rel.k = e.relation"
            " JOIN nodes s ON s.k = e.source"
            " WHERE t.id = ?1 AND (?2 IS NULL OR rel.name = ?2)",
            (node_id, relation),
        )
        return [(direction, name, other) for direction, name, other in rows]

    def members(self, community: int) -> list[Node]:
        rows = self._db.execute(_NODE_SELECT + " WHERE n.community = ? ORDER BY n.id", (community,))
        return [_indexed_node(row) for row in rows]

    def search(self, query: str, repo: str | None, kind: str | None) -> list[Node]:
        # The id tiebreak is load-bearing although the fixture cannot show it: there,
        # rowids follow id order, so SQLite's own order for a tie looks the same.
        rows = self._db.execute(
            "SELECT n.id, n.label, n.kind, r.name, f.path, n.url, n.community"
            " FROM labels JOIN nodes n ON n.k = labels.rowid"
            " JOIN files f ON f.k = n.file JOIN repos r ON r.k = f.repo"
            " WHERE labels MATCH ?1 AND (?2 IS NULL OR r.name = ?2)"
            " AND (?3 IS NULL OR n.kind = ?3)"
            " ORDER BY bm25(labels), n.id",
            (query, repo, kind),
        )
        return [_indexed_node(row) for row in rows]


def _graph_node(raw: dict) -> Node:
    metadata = raw.get("metadata")
    kind = metadata.get("kind", "") if isinstance(metadata, dict) else ""
    return {
        "id": raw["id"],
        "label": raw.get("label"),
        "repo": raw.get("repo"),
        "kind": kind or "",
        "file": raw.get("source_file") or None,
        "url": raw.get("url"),
        "community": raw.get("community"),
    }


class _InMemory:
    """The graph read whole, for a store whose index is absent or unusable. Built
    once per loaded store."""

    layer = "graph"

    def __init__(self, graph: Any):
        raw_nodes = graph.get("nodes") if isinstance(graph, dict) else None
        raw_links = graph.get("links") if isinstance(graph, dict) else None
        self._nodes: dict[str, Node] = {}
        for raw in raw_nodes or []:
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                self._nodes[raw["id"]] = _graph_node(raw)
        self._ids = sorted(self._nodes)
        # Each node's edges, both ways, once each: the index's edge key is unique.
        edges: dict[str, set[Edge]] = {}
        for link in raw_links or []:
            if not isinstance(link, dict):
                continue
            source, relation, target = link.get("source"), link.get("relation"), link.get("target")
            if source in self._nodes and target in self._nodes and isinstance(relation, str):
                edges.setdefault(source, set()).add(("out", relation, target))
                edges.setdefault(target, set()).add(("in", relation, source))
        self._edges = {node_id: sorted(found) for node_id, found in edges.items()}
        self._labels: sqlite3.Connection | None = None

    def node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def edges(self, node_id: str, relation: str | None) -> list[Edge]:
        found = self._edges.get(node_id, [])
        return [edge for edge in found if relation is None or edge[1] == relation]

    def members(self, community: int) -> list[Node]:
        return [self._nodes[i] for i in self._ids if self._nodes[i]["community"] == community]

    def search(self, query: str, repo: str | None, kind: str | None) -> list[Node]:
        rows = self._label_index().execute(
            "SELECT rowid, bm25(labels) FROM labels WHERE labels MATCH ?", (query,)
        )
        ranked = sorted((score, self._ids[rowid]) for rowid, score in rows)
        found = [self._nodes[node_id] for _, node_id in ranked]
        return [
            node
            for node in found
            if (repo is None or node["repo"] == repo) and (kind is None or node["kind"] == kind)
        ]

    def _label_index(self) -> sqlite3.Connection:
        """The labelled nodes in an FTS5 table in memory, as the index's `labels`
        holds them, built on the first search. bm25 reads only the table's own
        statistics, so the same labels rank the same here as in the index."""
        if self._labels is None:
            db = sqlite3.connect(":memory:")
            db.execute(f"CREATE VIRTUAL TABLE labels USING fts5(label, tokenize='{_TOKENIZER}')")
            db.executemany(
                "INSERT INTO labels(rowid, label) VALUES (?, ?)",
                (
                    (k, self._nodes[node_id]["label"])
                    for k, node_id in enumerate(self._ids)
                    if isinstance(self._nodes[node_id]["label"], str)
                ),
            )
            self._labels = db
        return self._labels


Reader = _Indexed | _InMemory


def _reader(store: Store) -> Reader:
    if isinstance(store.index, IndexHandle):
        return _Indexed(store.index.connection)
    return _in_memory(store)


def _in_memory(store: Store) -> _InMemory:
    return store.memo("graph.in_memory", lambda: _InMemory(store.layer("graph")))


# --- Responses ----------------------------------------------------------------------


def _respond(
    store: Store,
    notice: Notice | None,
    layers_used: list[str],
    *,
    graph_read_whole: bool = False,
    **fields: Any,
) -> dict[str, Any]:
    """The envelope and `fields`. `graph_read_whole` marks an answer the graph gave
    although the index is open, because the index failed mid-call."""
    envelope = results.envelope(store, layers_used, notice)
    if graph_read_whole:
        envelope["store"]["index"] = results.INDEX_ABSENT
    return {**envelope, **fields}


def _paged(
    store: Store,
    notice: Notice | None,
    layers_used: list[str],
    items: list,
    *,
    key: Callable[[Any], Any],
    cursor: str | None,
    cap_bytes: int,
    graph_read_whole: bool = False,
    **fields: Any,
) -> dict[str, Any]:
    """One page of `items`, framed by the envelope and `fields`. An empty list
    says what was searched."""
    frame = _respond(store, notice, layers_used, graph_read_whole=graph_read_whole, **fields)
    if not items and "searched" not in frame:
        frame["searched"] = results.searched(store, layers_used)
    return results.page(
        items, cap_bytes, cursor, digest=store.manifest.digest, key=key, frame=frame
    )


def _repository_layers(store: Store, base: str, answer: Any) -> list[str]:
    """The layers a repository answer read: the relationships registry is consulted
    only for a name the estate does not hold, and only when it is present."""
    if isinstance(answer, Known):
        return [base]
    entry = store.manifest.layers.get("relationships")
    if isinstance(entry, dict) and entry.get("present") is True:
        return [base, "relationships"]
    return [base]


# --- The tools ----------------------------------------------------------------------


def store_info(session: Session) -> dict[str, Any]:
    """The store's format, versions, provenance, layer states and flags, as recorded;
    and `stale_days` from the session's clock."""
    store, notice = session.current()
    found = store.manifest
    if isinstance(store.index, IndexHandle):
        row = store.index.connection.execute(
            "SELECT value FROM meta WHERE key = 'index_schema'"
        ).fetchone()
        index = {"schema": row[0] if row else None, "fallback": None}
    else:
        index = {"schema": None, "fallback": store.index.reason}
    return _respond(
        store,
        notice,
        ["provenance"],
        stale_days=session.stale_days(),
        index=index,
        repositories=_provenance(store.layer("provenance")),
        layers={
            name: {"present": entry.get("present") if isinstance(entry, dict) else None}
            for name, entry in found.layers.items()
        },
        extra_layers={
            name: {
                "present": entry.get("present") if isinstance(entry, dict) else None,
                "kind": entry.get("kind") if isinstance(entry, dict) else None,
            }
            for name, entry in found.extra_layers.items()
        },
        unread=list(found.unread),
        flags={f"{layer}.{flag}": _flag(found.layers.get(layer), flag) for layer, flag in FLAGS},
    )


def _flag(entry: Any, flag: str) -> Any:
    return entry.get(flag) if isinstance(entry, dict) else None


def _provenance(document: Any) -> list[dict[str, Any]]:
    repositories = document.get("repositories") if isinstance(document, dict) else None
    if not isinstance(repositories, dict):
        return []
    rows = []
    for name in sorted(repositories):
        entry = repositories[name]
        entry = entry if isinstance(entry, dict) else {}
        rows.append(
            {"repository": name, "sha": entry.get("sha"), "committed": entry.get("committed")}
        )
    return rows


def find_repo(session: Session, name: str) -> dict[str, Any]:
    """The three-way answer for a repository name: an exact match, near misses
    (each labelled as one, never as the answer), or a target the estate references
    and does not hold."""
    store, notice = session.current()
    base = _reader(store).layer
    answer = results.repository(store, name)
    layers_used = _repository_layers(store, base, answer)
    fields = answer.as_dict()
    if not isinstance(answer, Known):
        fields["searched"] = results.searched(store, layers_used)
    return _respond(store, notice, layers_used, **fields)


def search(
    session: Session,
    text: str,
    repo: str | None = None,
    kind: str | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    cursor: str | None = None,
    *,
    cap_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Nodes whose label has a word beginning with each word of `text`, case and
    Latin accents ignored, best first: by FTS5 `bm25`, then id. The first `limit`
    are returned, paged under the cap; `matches` counts every node that matched.
    If the index's full-text table cannot be read, the graph answers and
    `fallback` says why."""
    query = _query(_text(text, "search text"))
    if query is None:
        raise ArgumentRefused("search text holds no word to match")
    limit = _count(limit, "limit", "a whole number of 1 or more", lambda n: n >= 1)
    if kind is not None:
        kind = _text(kind, "kind")
    store, notice = session.current()
    reader = _reader(store)
    layers_used = [reader.layer]
    fields: dict[str, Any] = {}
    if repo is not None:
        answer = results.repository(store, _text(repo, "repo"))
        fields["repository"] = answer.as_dict()
        if not isinstance(answer, Known):
            layers_used = _repository_layers(store, reader.layer, answer)
            return _paged(
                store,
                notice,
                layers_used,
                [],
                key=str,
                cursor=cursor,
                cap_bytes=cap_bytes,
                matches=0,
                **fields,
            )
        repo = answer.name
    try:
        found = reader.search(query, repo, kind)
    except sqlite3.Error as error:
        if not isinstance(reader, _Indexed):
            raise
        # A damaged full-text table: the graph still holds every label.
        reader = _in_memory(store)
        layers_used = [reader.layer]
        fields["fallback"] = f"the index could not be searched ({error}): graph read whole"
        found = reader.search(query, repo, kind)
    ranked = found[:limit]
    rank = {item["id"]: position for position, item in enumerate(ranked)}
    return _paged(
        store,
        notice,
        layers_used,
        ranked,
        key=lambda item: rank[item["id"]],
        cursor=cursor,
        cap_bytes=cap_bytes,
        matches=len(found),
        graph_read_whole="fallback" in fields,
        **fields,
    )


def node(session: Session, id: str) -> dict[str, Any]:
    """One node by id, with its pinned `url`; null, with what was searched, when the
    store holds no such id."""
    node_id = _text(id, "id")
    store, notice = session.current()
    reader = _reader(store)
    found = reader.node(node_id)
    fields: dict[str, Any] = {"node": found}
    if found is None:
        fields["searched"] = results.searched(store, [reader.layer])
    return _respond(store, notice, [reader.layer], **fields)


def neighbours(
    session: Session,
    id: str,
    depth: int = 1,
    relation: str | None = None,
    cursor: str | None = None,
    *,
    cap_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Every node within `depth` hops (1 or 2) of `id`, along edges either way,
    each listed once at the hop that first reaches it, with every edge that did.
    The start node is never its own neighbour, so a cycle is visited once."""
    node_id = _text(id, "id")
    depth = _count(
        depth,
        "depth",
        f"1 or 2: neighbours walks at most {MAX_DEPTH} hops",
        lambda n: 1 <= n <= MAX_DEPTH,
    )
    if relation is not None:
        relation = _text(relation, "relation")
    store, notice = session.current()
    reader = _reader(store)
    start = reader.node(node_id)
    items = _walk(reader, node_id, depth, relation) if start is not None else []
    return _paged(
        store,
        notice,
        [reader.layer],
        items,
        key=lambda item: (item["depth"], item["node"]["id"]),
        cursor=cursor,
        cap_bytes=cap_bytes,
        node=start,
    )


def _walk(reader: Reader, start: str, depth: int, relation: str | None) -> list[dict[str, Any]]:
    seen = {start}
    frontier = [start]
    items: list[dict[str, Any]] = []
    for hop in range(1, depth + 1):
        reached: dict[str, list[dict[str, str]]] = {}
        for here in frontier:
            for direction, name, other in reader.edges(here, relation):
                if other not in seen:
                    reached.setdefault(other, []).append(
                        {"from": here, "relation": name, "direction": direction}
                    )
        for other in sorted(reached):
            found = reader.node(other)
            if found is None:
                continue
            via = sorted(reached[other], key=lambda v: (v["from"], v["relation"], v["direction"]))
            items.append({"depth": hop, "node": found, "via": via})
        seen.update(reached)
        frontier = sorted(reached)
    return items


def community(
    session: Session,
    id: int,
    cursor: str | None = None,
    *,
    cap_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    """One community: its label and summary, and its member nodes by id, paged. The
    summary is store text, returned as data."""
    community_id = _count(id, "community id", "a whole number", lambda n: True)
    store, notice = session.current()
    reader = _reader(store)
    members = reader.members(community_id)
    label = _entry(store.layer("labels"), community_id)
    summary = _entry(store.layer("summaries"), community_id)
    layers_used = [reader.layer] + [
        name for name in ("labels", "summaries") if store.layer(name) is not None
    ]
    held = members or label is not None or summary is not None
    header = {"id": community_id, "label": label, "summary": summary} if held else None
    return _paged(
        store,
        notice,
        layers_used,
        members,
        key=lambda item: item["id"],
        cursor=cursor,
        cap_bytes=cap_bytes,
        community=header,
    )


def _entry(document: Any, community_id: int) -> Any:
    """A community's entry in a file keyed by its id as text. `_metadata` cannot be
    asked for: an int never formats to it."""
    return document.get(str(community_id)) if isinstance(document, dict) else None
