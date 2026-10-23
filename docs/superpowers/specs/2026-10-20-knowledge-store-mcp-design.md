# knowledge-store-mcp: design

| Kind | Name |
|---|---|
| Distribution | `hmcts-knowledge-store-mcp` |
| Command | `knowledge-store-mcp` |
| Python module | `knowledgestore_mcp` (top-level; not part of the library's package) |

## Problem

Agents answering from a store make five recurring mistakes:

| Mistake | Requirement |
|---|---|
| answer about a similarly named repository that doesn't exist | a near miss is labelled as one, never returned as the answer |
| read a large relationships file whole and lose entries to truncation | lists are complete, counted and explicitly paged |
| cite a corpus path no reader can open | every citation is a URL pinned to the build commit |
| report unmerged work as landed | landed and proposed tickets are kept separate |
| read "nothing found" as "doesn't exist" | an empty result states what was searched |

## Decisions

1. **Separate repository** from `knowledge-store-builder`, because the server has its own dependencies (the MCP SDK), threat model and release cycle.
2. **Stdio only in version 1.** It covers Claude Code and Claude Desktop, plugins on hosted agent platforms, and Claude Agent SDK applications. The HTTP transport and its authentication are deferred.
3. **Read-only, one store per process,** resolved once at start-up (see *Configuration*).


## What the server depends on

Everything the server serves comes from a store built by `knowledge-store-builder`. The contract is specified there:

| Library issue | What the server relies on |
|---|---|
| hmcts/knowledge-store-builder#454: store manifest | `knowledge/store-manifest.json`: `format.major`/`minor`, `store.id`, `store.github_org`, and each layer's measured `present` flag, plus `extra_layers` |
| hmcts/knowledge-store-builder#455: relationships layer | `knowledge/relationships/by-repo/<repo>.json` and `repository-dependencies.json`. Edges carry `references`, `kinds`, `refs_pinned` and `examples`, and targets outside the estate are listed. Kinds come from a parser registry, built-in plus store-registered, and the manifest lists which are present |
| hmcts/knowledge-store-builder#457: query index | `knowledge/index/store.sqlite.gz`, committed gzipped because the plain file can pass GitHub's 100 MB limit. A plain `store.sqlite` is read only when there is no archive. The graph is the fallback when neither exists |
| knowledge-store-builder's minimum-standard layer (`feat/minimum-standard-evidence`, not yet merged) | `knowledge/minimum-standard/summary.json`, stamped `"format": {"layer": "minimum_standard", "major": 1}`, with `notes` and an optional `products` map, and `by-repo/<repo>.json`: seven points, each `present`, `absent` or `not_determinable` with `evidence` and a `reason`, plus `as_of` and `settings_read_at` |
| hmcts/knowledge-store-builder#456: fixture store | a synthetic store attached to each library release, which this repository's CI tests against |

**The one rule the server enforces:** it reads the format major versions it was written for, and refuses any other. A newer minor version is read, and its unknown layers are reported as present but unread.

## Configuration

The server needs exactly one store, and it finds the store in this order. The first rule that resolves wins:

| Order | Source | Use |
|---|---|---|
| 1 | `--store PATH` | an explicit path |
| 2 | the `KNOWLEDGE_STORE_PATH` environment variable | containers and CI, where the store is built in |
| 3 | `--store-id ID`, looked up in `~/.config/knowledge-store/locations` | a plugin. That file is the list of clone paths the library's `knowledge-store` skill records on first clone, and the server takes the entry whose manifest has `store.id = ID` |
| 4 | `${CLAUDE_PLUGIN_ROOT}`, when it contains a store | a plugin that ships the store inside itself |

If none resolves, the server refuses to start and names each place it checked. A store's plugin declares a fixed entry, and every asker's own clone is then found by ID. The entry launches the server itself, as *Installation* describes:

```json
{ "mcpServers": { "knowledge-store": {
    "command": "uvx",
    "args": ["--from", "git+https://github.com/hmcts/knowledge-store-mcp@v0.1.0",
             "knowledge-store-mcp", "--store-id", "example-store"] } } }
```

The server takes no credentials. The only access an asker needs is read access to clone the store repository.

## Installation

**Status: decided, 2026-10-22.** `uvx` at a pinned release tag is the install route, as best practice. Publishing the package to a package index and launching it from there was the alternative and is not taken.

Installing a plugin puts no command on the asker's machine, so the client configuration launches the server itself, pinned to a release tag of this repository:

```json
"knowledge-store": {
  "command": "uvx",
  "args": ["--from", "git+https://github.com/hmcts/knowledge-store-mcp@v0.1.0",
           "knowledge-store-mcp", "--store-id", "<id>"]
}
```

**What the machine needs: `uv`, and nothing else.** `uvx` builds the server into an isolated environment from the tagged source. If no installed Python satisfies the package's `requires-python` (3.11 or newer), uv downloads a managed Python first: automatic downloads are uv's default (`python-downloads = "automatic"`). A machine that forbids them (`--no-python-downloads`, or `UV_PYTHON_DOWNLOADS=never`) needs Python 3.11 or newer installed instead.

**First run and offline use.** The first launch needs network access: uv fetches the tag from GitHub, the dependencies from PyPI and, if needed, a Python, all into uv's cache. Later launches run from that cache. With no network, uv tries to refresh the tag, fails, and falls back to the cached checkout, so the server still starts. Do not add `--offline` to the arguments: with a tag, uv then refuses to resolve the tag at all, even when it is cached (checked with uv 0.12). `uv cache clean` removes the cached environment, and the next launch needs the network again.

**Upgrading: change the tag in the configuration.** Nothing else changes, and nothing needs uninstalling: a new tag is a new cache entry, and the old one is unused.

**Why a tag and not a branch: reproducibility.** A tag names one commit, so every asker on the same configuration runs the same server, and a fault can be reproduced from the configuration alone. A branch moves under them: uv re-fetches the ref on each launch it has network for, so the server changes without the configuration changing, and an asker who was offline keeps an older one. Two askers with identical configuration can then run different code, with nothing to say which.

## The server

### Start-up

`knowledge-store-mcp --store PATH` starts the server:
1. **Resolve the store root** in the order under *Configuration*. Every later path is checked against it after `realpath`, so symlinks and `..` can't escape.
2. **Read the manifest.**
   - A missing manifest, or an unknown **major** version, refuses to start, naming the version found and the versions supported.
   - A newer **minor** version starts, and reports the unknown layers as present but unread.
   - **A layer that carries its own format stamp** (the relationships layer's `format` in `repository-dependencies.json`, the index's `index_schema`) is checked on first use. A missing stamp, another layer name or an unsupported major makes the layer **present but unrecognised**: it is never read, and never parsed on a best-effort basis. The relationships layer is also unrecognised when it is stamped but holds no per-repository files: the manifest may name the layer directory (the server reads `by-repo/` inside it) or `by-repo/` itself, and zero files there means a layout the server cannot read, not an empty estate.
3. **Open the query index read-only,** and read other layers lazily on first use. The server never parses the whole graph into memory when the index is present. Without the index, it falls back to the graph and says so in `store_info`.
   - The store commits the index gzipped, and SQLite cannot open a compressed file. On first use the server decompresses it to `${XDG_CACHE_HOME:-~/.cache}/knowledge-store/<store.id>/<sha256 of the .gz>.sqlite`, then opens that file read-only.
   - The key is the archive's hash, so a rebuilt store gets a new cache entry rather than a stale one. The server removes its other entries for that store id.
   - This cache is the server's only write. It is outside the store, and a failure to write it falls back to the graph, never to a partial file.

### Tools

Every result carries `store`, which holds the format, library version, build date and the layers used. An empty result also says what was searched.

| Tool | Returns | Failure it prevents |
|---|---|---|
| `store_info()` | the format, versions, build date, per-repository provenance (sha, date), and each layer's present/absent state; plus `stale_days` | staleness or a missing layer read as an answer |
| `find_repo(name)` | an exact match, or `{exact: null, near_misses: [...]}` with each candidate labelled as a near miss, never as the answer | a similar name substituted for one that doesn't exist |
| `repo_answer(repo)` | the repository's relationships file (knowledge-store-builder#455), whole, or "layer absent" | reading a large file whole and truncating it |
| `dependents(target, kind?, ref_prefix_not?, page?)` / `dependencies(repo, kind?, page?)` | items, each with `references`, `kinds`, `refs_pinned` and example lines with pinned URLs, plus `total` and `next_page`; never cut without saying so. `ref_prefix_not` keeps only consumers that pin something other than a given ref prefix (for example a tag other than `4.`, or a branch) | an incomplete list read as complete; pin questions not answerable through the tools |
| `search(text, repo?, kind?, limit)` | ranked nodes: id, label, repo, kind, `url` | corpus paths as citations |
| `node(id)`, `neighbours(id, depth≤2, relation?)`, `community(id)` | graph access; each node carries its `url` and each community its summary, dated by the store's `sources_as_of` as `summary_as_of` | the same; written prose presented as current |
| `tickets(repo, path?)` | `tickets` (landed) and `proposed` (with branches) as separate lists, plus the store's `reach` flag | unmerged work treated as landed |
| `minimum_standard(repos?, pattern?, product?)` | per repository, each of the seven points of the GOV.UK minimum standard for publicly accessible systems as `present`, `absent` or `not_determinable`, with its cited evidence (a secret-scan finding as its rule, path and line only, never a value), the record's `as_of` and `settings_read_at`, and `meanings`: `absent` is not evidenced in the repository, `not_determinable` is not `absent`, and a record is true of a point in time. Scoped by comma-separated names, a glob over names, or a product the layer's summary declares, else the whole layer; paged. An unknown name or product gets its near misses. Never a pass, a score or an overall verdict | evidence summed into a verdict; `not_determinable` read as a failure; a dated record presented as current |
| `answers(query, limit)` | written answers: the library's topic briefs and deep dives, plus any `extra_layers` declared with `"kind": "answers"`. Each match is returned with its pinned URL, and dated: `as_of` and `commit` where the entry records them, a brief or deep dive by the store's `sources_as_of`, otherwise null (undated) | an agent rebuilding from raw evidence an answer the store has already written; written analysis presented as current |
| `ticket(key)` | descriptions, branch-only text (`ps`/`pb`), and `proposed`/`branches`; and under `tracker`, apart from that commit-mined text, the `ticket_tracker` layer's record (title, status, resolution, description, comments; written prose, dated by `as_of`: the day it was last checked, or for a resolved record the day it resolved), or null with `tracker_note` saying why when the layer is absent or unrecognised | the same; tracker text quoted as commit text, or the other way round |

**Unknown names, in every tool that takes a repository.** Every such tool returns one of three answers:
- **not in the estate**, with its near misses;
- **in the estate, with zero results**;
- **referenced by the estate but not held** (a target outside the estate).

It never returns a bare empty list. A call such as `dependents("<a name the estate doesn't hold>")` must say so and offer the near misses.

**Multi-hop.** `dependents(target, kind?, transitive: true, max_depth ≤ 4)` returns each dependent with its path from the target, so impact questions don't need repeated calls. A cycle is reported, not followed.

**Output caps and complete lists.** The cap is per response and is set above the largest per-repository file seen in practice. `repo_answer` is paged explicitly when a file exceeds it, and every page reports `total` and `next_page`. A cap never silently truncates.

**Freshness.** On each call the server compares the manifest's `digest` on disk with the one it loaded. The digest is computed from the layers' content, not from file times, so it changes when a rebuild or a pull changes the store, and not when a clone or an identical rebuild touches the files. When it differs, the server reloads before answering, and the result says so. A server never serves an older store than the one on disk. `store_info` reports the manifest's `sources_as_of`, the newest source commit the store reflects.

**Absence semantics.** An empty result never stands alone. It always says something like "searched N repositories in the graph layer; the relationships layer is absent". The counts are the store's own.

An **unrecognised** layer is served as absent, never as empty. Every tool that needs it answers with the reason in place of a result, for example "relationships layer present but format unrecognised (found none, supports 1.x); not read", and `store_info` lists it with that state. A tool never falls back to an empty result for a layer it could not read.

### Security

**Threat model:**
- **The store's structure is trusted; its text is not.** Repository names, ids, edges, ticket keys and URLs are produced deterministically by the library and can be relied on. Commit messages, ticket text, documents and summaries were written by people and are untrusted: the server returns them only as data, never as instructions.
- The caller is an agent that may be steered by that same text.

**Controls:**
- **Read-only.** The server has no write path at all.
- **Path containment.** A path comes from the manifest and the store root only, never from a tool argument. Repository and node ids are looked up, never joined into a path.
- **Output caps.** Each response has a byte cap, and paging stays explicit, so a cap never silently truncates.
- **Store text is data.** Results put store text in data fields, never in instruction-shaped prose, and the server's tool descriptions say that store content is untrusted.
- **No outbound network, and no subprocesses.**
- **Masking is the library's job.** The library masks secrets at build time, and the server re-serves only what the store committed.

### Contract tests

The server's CI downloads the latest library release's fixture store (knowledge-store-builder#456) and runs every tool against it, asserting exact results that were hand-derived from the fixture's design. A scheduled job also runs against the library's `main` fixture, so a contract change is seen before it is released.

### Acceptance

The first consuming store re-runs its evaluation question set through the tools, against a store whose relationships layer (hmcts/knowledge-store-builder#455) comes from **the library stage**, not the store's script, so parity is tested end to end. The comparison arm is the best existing Claude Code arm (E).

**Measured with `knowledgestore answers --compare`.** The server passes when all of these hold:
- **Non-inferior on accuracy.** The lower 95% confidence bound of (MCP − E) is above −0.03.
- **Cheaper.** Mean cost per answer is below E's.
- **Every citation resolves,** and every one is a pinned URL.
- **A nonexistent name** is answered "not in the estate" in every repetition.
- **No `proposed` ticket** is reported as landed.
- **Control questions** score no worse than the no-store arm (A).

## Out of scope for version 1

- the HTTP transport and its authentication;
- writing to a store;
- several stores in one process;
- a chat UI;
- semantic or embedding search beyond the existing token neighbours.

## Public-repository rules

- **Fixtures:** synthetic only.
- **No estate identifiers:** no repository names, ticket keys, cloud identifiers or commit text from any estate.
- **No internal measurements:** none in code, docs, issues or PRs. "One large internal estate" carries the same weight.

## Delivery order

1. Library: #454, #455, #456 and #457, in `knowledge-store-builder`. Each ships in a library minor release and is useful without the server.
2. Server: start-up, tools, security and contract tests, here.
3. First store: acceptance. A second store's question set then runs as a second acceptance check.
