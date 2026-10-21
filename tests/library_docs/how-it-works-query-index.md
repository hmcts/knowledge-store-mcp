<!-- Vendored from knowledge-store-builder docs/how-it-works.md at b6cbc5a91fac6895188f5158701022b1fe61359f (#457), the "Query index" section; its tagged query and helper blocks are verbatim. tests/test_index_queries.py holds the server's SQL to the tagged blocks below; refresh this copy, never edit it, when the library's documented queries change. -->

## Query index

`knowledgestore index` (`src/knowledgestore/build_store_index.py`) writes
`knowledge/index/store.sqlite` and its archive `store.sqlite.gz`, so that a reader
opens one file read-only instead of parsing a graph of hundreds of thousands of
nodes. The graph is streamed in and never loaded (the module `store_index` holds
the schema).

Every long node id is stored once. Edges, files and tickets hold integer keys, and
repositories, files and relation names are interned. Repeating the id strings was
measured at five times the size on the largest local store graph.

| Table | Holds |
|---|---|
| `meta` | `index_schema`, the store-relative `graph` path that was read, and the first 16 hex characters of `graph_sha256` and `file_tickets_sha256` |
| `repos`, `files`, `relations` | the interned names; `files` is unique on `(repo, path)`, and a node with no repository has a NULL `files.repo` rather than a repository named `""` |
| `nodes` | `id`, `label` (NULL on a structural node), `kind` in its current form, `file`, `url` and `community` (NULL when absent) |
| `edges` | `source`, `relation`, `target` as integer keys, with an index on `(target, relation, source)` for incoming edges |
| `tickets` | `file`, `ticket`, `count` and `landed`, from `file-tickets.json.gz`, joined to a node through `(repo, path)` |
| `labels` | an FTS5 index over the node labels, with the `nodes` table as its content; an unlabelled node is not in it |

An edge whose endpoint is not a node, and a repeated node id or link, is counted
and reported rather than stored, so the stage's report reconciles: nodes read =
nodes + duplicates, and links read = edges + dangling + duplicates.

**Determinism holds per SQLite build.** Two runs on the same inputs in one
environment give the same bytes in both files, and the archive carries no
timestamp or filename. The database header records the SQLite library version, so
two different SQLite builds can write different bytes from the same store.

`status` reports whether the index is current. It reads `meta` from the plain file
only and never decompresses the archive. When `meta.graph` names the committed
`.gz`, it hashes that file and the ticket layer and compares them with
`meta.graph_sha256` and `meta.file_tickets_sha256`. When the index was built from
the plain graph, which can be gigabytes, `status` says freshness is not checked
rather than read it.

The queries a reader runs against the index are below. Each block is held by
`tests/test_store_index.py`, which runs it as written against a built index and
compares the rows with a hand-written list, so what follows is what the schema
answers. Bind the `?` parameters; never build the SQL from text.

Open the file read-only (`sqlite3.connect("file:store.sqlite?mode=ro", uri=True)`).
A node with no repository has a NULL `files.repo`, so every query that names a
repository joins `repos` with `LEFT JOIN` and reads `coalesce(r.name, '')`: an
inner join would drop that node without an error. `''` therefore means "no
repository" in a parameter as well as in a result.

**`find`** answers nodes whose label matches a full-text search, best match first
(`bm25`, then id, so a tie is stable). `?1` is an FTS5 `MATCH` argument, `?2` a
repository name or NULL, `?3` a kind or NULL, `?4` the limit, where `-1` means no
limit. Columns: `id, label, repo, kind, path, url, community`.

<!-- index-query: find -->
```sql
SELECT n.id, n.label, coalesce(r.name, ''), n.kind, f.path, n.url, n.community
FROM labels
JOIN nodes n ON n.k = labels.rowid
JOIN files f ON f.k = n.file
LEFT JOIN repos r ON r.k = f.repo
WHERE labels MATCH ?1
  AND (?2 IS NULL OR coalesce(r.name, '') = ?2)
  AND (?3 IS NULL OR n.kind = ?3)
ORDER BY bm25(labels), n.id
LIMIT ?4
```

Free text is not a `MATCH` argument. `NEAR`, `OR`, `*`, a column filter and an
unbalanced quote are all FTS5 syntax, and a user's words can hold any of them.
Split the text into words with FTS5's own `unicode61` tokenizer, the one the index
was built with, so `pay_gate` and `feature-gateway` become two words each, as they
are in a label. Quote each word, double any `"` inside it, and end it with `*` for
a prefix match. The words are then ANDed and none is an operator. A Python
tokenizer is not a substitute: SQLite's Unicode tables are its own, and an
imitation disagrees with the index on some characters.

<!-- index-helper: match_argument -->
```python
import sqlite3


def match_argument(text):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE words USING fts5(text, tokenize='unicode61')")
    db.execute("CREATE VIRTUAL TABLE terms USING fts5vocab(words, 'instance')")
    db.execute("INSERT INTO words(text) VALUES (?)", (text,))
    found = [t for (t,) in db.execute("SELECT term FROM terms ORDER BY offset")]
    db.close()
    return " ".join('"' + t.replace('"', '""') + '"*' for t in dict.fromkeys(found))
```

An empty result from `match_argument` has nothing to search for, so do not run
`find` with it. The MCP server also refuses text over 1024 bytes or over 32 words,
because every word is a prefix scan of the label index: bound the text before it
reaches the helper.

**`node`** answers one node by id. Same columns as `find`.

<!-- index-query: node -->
```sql
SELECT n.id, n.label, coalesce(r.name, ''), n.kind, f.path, n.url, n.community
FROM nodes n
JOIN files f ON f.k = n.file
LEFT JOIN repos r ON r.k = f.repo
WHERE n.id = ?1
```

**`neighbours`** answers the edges of one node with the relation and the id at the
other end. `?1` is the node id and `?2` a relation name or NULL. The direction
column is `out` when the node is the source and `in` when it is the target.
Ordered by relation, then the other end's id, then direction.

<!-- index-query: neighbours -->
```sql
SELECT 'out' AS direction, rel.name AS relation, t.id AS other
FROM nodes s
JOIN edges e ON e.source = s.k
JOIN relations rel ON rel.k = e.relation
JOIN nodes t ON t.k = e.target
WHERE s.id = ?1 AND (?2 IS NULL OR rel.name = ?2)
UNION ALL
SELECT 'in', rel.name, s.id
FROM nodes t
JOIN edges e ON e.target = t.k
JOIN relations rel ON rel.k = e.relation
JOIN nodes s ON s.k = e.source
WHERE t.id = ?1 AND (?2 IS NULL OR rel.name = ?2)
ORDER BY 2, 3, 1
```

**`tickets_for_file`** answers the tickets that touched a file, or every file of a
repository. `?1` is the repository (`''` for none) and `?2` the repository-relative
path, or NULL for every file of that repository. `landed` is 1 for a ticket in the
landed history and 0 for a proposed one. Landed first, then ticket id, then path.
Columns: `ticket, count, landed, repo, path`. An unknown repository returns no rows;
it is never read as "no repository". The `CASE` resolves the name to its key once,
so the lookup searches the `(repo, path)` index instead of scanning `files`.

<!-- index-query: tickets_for_file -->
```sql
SELECT t.ticket, t.count, t.landed, coalesce(r.name, ''), f.path
FROM files f
LEFT JOIN repos r ON r.k = f.repo
JOIN tickets t ON t.file = f.k
WHERE f.repo IS CASE WHEN ?1 = '' THEN NULL
                     ELSE coalesce((SELECT k FROM repos WHERE name = ?1), -1) END
  AND (?2 IS NULL OR f.path = ?2)
ORDER BY t.landed DESC, t.ticket, f.path
```

**`files_for_ticket`** answers the files one ticket touched, landed ones first,
then by repository and path. `?1` is the ticket id. Columns:
`repo, path, count, landed`.

<!-- index-query: files_for_ticket -->
```sql
SELECT coalesce(r.name, ''), f.path, t.count, t.landed
FROM tickets t
JOIN files f ON f.k = t.file
LEFT JOIN repos r ON r.k = f.repo
WHERE t.ticket = ?1
ORDER BY t.landed DESC, coalesce(r.name, ''), f.path
```

