"""The graph read whole: what answers when the query index is absent or unusable.

Every node is built exactly as the library's index builder builds its row, so the
fallback answers as the index would. The source is knowledge-store-builder
`src/knowledgestore/store_index.py` (#457, `feat/query-index` at 3aea1e4):
`_node_rows` for each field, `graph_files.iter_edges` for which key holds the
edges, and `_fill` for which copy of a repeated id survives. Change one, change
this.

One representation is kept: a tuple per node, the edges between them, and an FTS5
index of their labels. The parsed graph JSON they are built from is read without
being cached, so it can be freed once they exist. The FTS5 table is contentless:
it holds the index of the labels and not their text, which the tuples already
carry, and bm25 reads only the index, so it ranks exactly as the index's
external-content `labels` table does.
"""

import sqlite3
import sys
from collections.abc import Iterator
from typing import Any

from .store import Store

# Copied from knowledge-store-builder `src/knowledgestore/kinds.py` `_ALIASES`
# (last changed at a3ed9c5): every kind a store has ever written, mapped to the kind
# written now (library CLAUDE.md 6.5). The index stores the current kind.
LEGACY_KINDS = {
    "feature": "feature",
    "gherkin_feature": "feature",
    "scenario": "scenario",
    "gherkin_scenario": "scenario",
    "ticket": "ticket",
    "jira_ticket": "ticket",
}

# The tokenizer the index's `labels` table declares (index schema 1.0).
TOKENIZER = "unicode61"

# A node as both readers hold it, in this order. `file` is "" for a node with no
# source file, as the index stores it.
Row = tuple[str, str | None, str, str, str, str | None, Any]
# (direction from the asking node, relation, the other node's id)
Edge = tuple[str, str, str]


def as_node(row: Row) -> dict[str, Any]:
    """A node as every tool returns it."""
    node_id, label, repo, kind, file, url, community = row
    return {
        "id": node_id,
        "label": label,
        "repo": repo,
        "kind": kind,
        "file": file or None,
        "url": url,
        "community": community,
    }


def _text(value: Any) -> str | None:
    """`store_index._text`: a label or url that is not text is stored as its text."""
    return None if value is None else str(value)


def _kind(node: dict) -> str:
    """`kinds.node_kind`, as the index stores it."""
    metadata = node.get("metadata")
    raw = metadata.get("kind", "") if isinstance(metadata, dict) else ""
    if isinstance(raw, str):
        return LEGACY_KINDS.get(raw, raw)
    return "" if raw is None else str(raw)


def _row(node: dict) -> Row:
    """`store_index._node_rows`, field for field. A repository and a kind recur on
    many nodes, so each is held once (`sys.intern`) rather than once per node."""
    return (
        node["id"],
        _text(node.get("label")),
        sys.intern(node.get("repo") or ""),
        sys.intern(_kind(node)),
        node.get("source_file") or "",
        _text(node.get("url")),
        node.get("community"),
    )


def _sql_order(value: Any) -> tuple:
    """`value` as SQLite orders it: NULL first, then numbers, then text."""
    if value is None:
        return (0,)
    if isinstance(value, (int, float)):
        return (1, value)
    return (2, str(value))


def _survivor_order(row: Row) -> tuple:
    """`_fill` keeps, of a repeated id, the copy first in `ORDER BY repo, file, kind,
    label, url, community`, whatever order the graph listed them in."""
    _, label, repo, kind, file, url, community = row
    return (repo, file, kind, _sql_order(label), _sql_order(url), _sql_order(community))


def _links(graph: dict) -> Iterator[dict]:
    """`graph_files.iter_edges`: `links`, or `edges` when `links` has none."""
    links = graph.get("links")
    if not isinstance(links, list) or not links:
        links = graph.get("edges")
    for link in links if isinstance(links, list) else []:
        if isinstance(link, dict):
            yield link


class GraphReader:
    """The graph, read whole once per loaded store, answering what the index answers."""

    layer = "graph"

    def __init__(self, graph: Any):
        graph = graph if isinstance(graph, dict) else {}
        raw_nodes = graph.get("nodes")
        rows: dict[str, Row] = {}
        for raw in raw_nodes if isinstance(raw_nodes, list) else []:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            row = _row(raw)
            held = rows.get(row[0])
            if held is None or _survivor_order(row) < _survivor_order(held):
                rows[row[0]] = row
        self._rows = rows
        self._ids = sorted(rows)
        # Each node's edges both ways, once each: the index's edge key is unique, and
        # an edge to an id that is not a node is dropped, as the index drops it.
        edges: dict[str, set[Edge]] = {}
        for link in _links(graph):
            source, target = link.get("source"), link.get("target")
            if source in rows and target in rows:
                # The node's own id strings, not the link's equal copies of them.
                source, target = rows[source][0], rows[target][0]
                relation = sys.intern(_text(link.get("relation") or "") or "")
                edges.setdefault(source, set()).add(("out", relation, target))
                edges.setdefault(target, set()).add(("in", relation, source))
        self._edges = {node_id: sorted(found) for node_id, found in edges.items()}
        self._labels: sqlite3.Connection | None = None

    def repositories(self) -> list[str]:
        return sorted({row[2] for row in self._rows.values() if row[2]})

    def node(self, node_id: str) -> dict[str, Any] | None:
        row = self._rows.get(node_id)
        return as_node(row) if row else None

    def edges(self, node_id: str, relation: str | None) -> list[Edge]:
        found = self._edges.get(node_id, [])
        return [edge for edge in found if relation is None or edge[1] == relation]

    def members(self, community: int) -> list[dict[str, Any]]:
        return [as_node(self._rows[i]) for i in self._ids if self._rows[i][6] == community]

    def search(self, query: str, repo: str | None, kind: str | None) -> list[dict[str, Any]]:
        rows = self._label_index().execute(
            "SELECT rowid, bm25(labels) FROM labels WHERE labels MATCH ?", (query,)
        )
        ranked = sorted((score, self._ids[rowid]) for rowid, score in rows)
        return [
            as_node(self._rows[node_id])
            for _, node_id in ranked
            if (repo is None or self._rows[node_id][2] == repo)
            and (kind is None or self._rows[node_id][3] == kind)
        ]

    def _label_index(self) -> sqlite3.Connection:
        """Built on the first search; a store that is never searched never pays for it."""
        if self._labels is None:
            db = sqlite3.connect(":memory:")
            db.execute(f"CREATE VIRTUAL TABLE labels USING fts5(label, content='', tokenize='{TOKENIZER}')")  # fmt: skip
            db.executemany(
                "INSERT INTO labels(rowid, label) VALUES (?, ?)",
                (
                    (k, self._rows[node_id][1])
                    for k, node_id in enumerate(self._ids)
                    if self._rows[node_id][1] is not None
                ),
            )
            self._labels = db
        return self._labels

    def close(self) -> None:
        if self._labels is not None:
            self._labels.close()


def read(store: Store) -> GraphReader:
    """The store's graph reader, built once per loaded store and closed with it."""
    return store.memo(
        "graph.reader", lambda: GraphReader(store.read("graph")), close=GraphReader.close
    )
