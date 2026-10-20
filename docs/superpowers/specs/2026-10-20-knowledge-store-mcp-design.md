# knowledge-store-mcp: design

**Status:** draft 2. Reviewed by the first consuming store; awaiting owner sign-off.
**Scope:** the server in this repository. The store contract it reads is designed in `knowledge-store-builder` (see *What the server depends on*).

**Names:** each runs from least specific to most.

| Kind | Name |
|---|---|
| Distribution | `hmcts-knowledge-store-mcp` |
| Command | `knowledge-store-mcp` |
| Python module | `knowledgestore_mcp` |

The module is its own top-level package, so builders never inherit its dependencies.

## Why

An A/B evaluation of one large internal estate's store found two things:
- **The store makes coding agents markedly more accurate.**
- **The remaining errors follow a few patterns:**
  - an agent substitutes a similar repository name for one that doesn't exist;
  - an agent reads a large relationships file whole and loses entries when the read is truncated;
  - an agent cites corpus paths that no reader can open;
  - an agent treats unmerged work as landed;
  - an agent reads "nothing found" as "doesn't exist".

The same evaluation showed that guidance alone moves accuracy a long way. A short routing paragraph scored almost as well as the full plugin. So the server's value isn't reach. It's that each of those failures becomes impossible by construction:
- a near miss is labelled;
- a list is complete and counted;
- a citation is a pinned URL;
- a proposed ticket is named as proposed;
- an empty result says what the store covers.

## Decisions taken

1. **Separate repository.** The server has its own dependencies (the MCP SDK), its own threat model, and its own release pace, and adopters find it more easily on its own.
2. **Version 1 is stdio only.** The Claude Agent SDK also launches stdio servers as subprocesses, so a containerised Agent SDK chat with the store built in works over stdio too. Its consumers are Claude Code and Claude Desktop, plus hosted agent platforms that run Claude Code with marketplace plugins: a store plugin can declare the server, so it launches over stdio inside the hosted session.
   - The streamable-HTTP transport, its authentication and its network threat model wait until something needs a network-facing server.
   - Still to confirm, for completeness: whether a plugin-hosting agent platform in use can mount a plugin's stdio server.
3. **Read-only, and one store per process.** The store root is fixed at start-up with `--store PATH`. There is no runtime path argument.

---

## What the server depends on

The store is built by `knowledge-store-builder`, and everything the server serves comes from it. The server holds no credentials and makes no network calls. It needs only a path to a built store.

The contract between the two repositories is designed and built in the library:

| Library issue | What the server relies on |
|---|---|
| hmcts/knowledge-store-builder#454: store manifest | `knowledge/store-manifest.json`: `format.major`/`minor`, `store.id`, `store.github_org`, and each layer's measured `present` flag, plus `extra_layers` |
| hmcts/knowledge-store-builder#455: relationships layer | `knowledge/relationships/by-repo/<repo>.json` and `repository-dependencies.json`. Edges carry `references`, `kinds`, `refs_pinned` and `examples`, and targets outside the estate are listed |
| hmcts/knowledge-store-builder#456: fixture store | a synthetic store attached to each library release, which this repository's CI tests against |

**The one rule the server enforces:** it reads the format major versions it was written for, and refuses any other. A newer minor version is read, and its unknown layers are reported as present but unread.

## Configuration

The only setting is the store's path. A client launches the server like this:

```json
{ "mcpServers": { "knowledge-store": {
    "command": "knowledge-store-mcp",
    "args": ["--store", "/path/to/a/built/store"] } } }
```

A store's plugin can declare this, so an asker installs the plugin and never edits it. The only access an asker needs is read access to clone the store repository.

## The server

### B1. Start-up

`knowledge-store-mcp --store PATH` starts the server:
1. **Resolve the store root.** Every later path is checked against it after `realpath`, so symlinks and `..` can't escape.
2. **Read the manifest.**
   - A missing manifest, or an unknown **major** version, refuses to start, naming the version found and the versions supported.
   - A newer **minor** version starts, and reports the unknown layers as present but unread.
3. **Load the graph once,** and build in-memory indexes: by repository name, by node id, a text index over labels, and edges by kind. Layers load lazily on first use.

### B2. Tools

Every result carries `store`, which holds the format, library version, build date and the layers used. An empty result also says what was searched.

| Tool | Returns | Failure it prevents |
|---|---|---|
| `store_info()` | the format, versions, build date, per-repository provenance (sha, date), and each layer's present/absent state; plus `stale_days` | staleness or a missing layer read as an answer |
| `find_repo(name)` | an exact match, or `{exact: null, near_misses: [...]}` with each candidate labelled as a near miss, never as the answer | a similar name substituted for one that doesn't exist |
| `repo_answer(repo)` | the repository's relationships file (knowledge-store-builder#455), whole, or "layer absent" | reading a large file whole and truncating it |
| `dependents(target, kind?, ref_prefix_not?, page?)` / `dependencies(repo, kind?, page?)` | items, each with `references`, `kinds`, `refs_pinned` and example lines with pinned URLs, plus `total` and `next_page`; never cut without saying so. `ref_prefix_not` keeps only consumers that pin something other than a given ref prefix (for example a tag other than `4.`, or a branch) | an incomplete list read as complete; pin questions not answerable through the tools |
| `search(text, repo?, kind?, limit)` | ranked nodes: id, label, repo, kind, `url` | corpus paths as citations |
| `node(id)`, `neighbours(id, depth≤2, relation?)`, `community(id)` | graph access; each node carries its `url` and each community its summary | the same |
| `tickets(repo, path?)` | `tickets` (landed) and `proposed` (with branches) as separate lists, plus the store's `reach` flag | unmerged work treated as landed |
| `ticket(key)` | descriptions, branch-only text (`ps`/`pb`), and `proposed`/`branches` | the same |

**Unknown names, in every tool that takes a repository.** Every such tool returns one of three answers:
- **not in the estate**, with its near misses;
- **in the estate, with zero results**;
- **referenced by the estate but not held** (a target outside the estate).

It never returns a bare empty list. A call such as `dependents("<a name the estate doesn't hold>")` must say so and offer the near misses.

**Multi-hop.** `dependents(target, kind?, transitive: true, max_depth ≤ 4)` returns each dependent with its path from the target, so impact questions don't need repeated calls. A cycle is reported, not followed.

**Output caps and complete lists.** The cap is per response and is set above the largest per-repository file seen in practice. `repo_answer` is paged explicitly when a file exceeds it, and every page reports `total` and `next_page`. A cap never silently truncates.

**Absence semantics.** An empty result never stands alone. It always says something like "searched N repositories in the graph layer; the relationships layer is absent". The counts are the store's own.

### B3. Security

**Threat model:**
- The store is semi-trusted: its text comes from commit messages and documents that anyone could write.
- The caller is an agent that may be steered by that same text.

**Controls:**
- **Read-only.** The server has no write path at all.
- **Path containment.** A path comes from the manifest and the store root only, never from a tool argument. Repository and node ids are looked up, never joined into a path.
- **Output caps.** Each response has a byte cap, and paging stays explicit, so a cap never silently truncates.
- **Store text is data.** Results put store text in data fields, never in instruction-shaped prose, and the server's tool descriptions say that store content is untrusted.
- **No outbound network, and no subprocesses.**
- **Masking is the library's job.** The library masks secrets at build time, and the server re-serves only what the store committed.

### B4. Contract tests

The server's CI downloads the latest library release's fixture store (knowledge-store-builder#456) and runs every tool against it, asserting exact results that were hand-derived from the fixture's design. A scheduled job also runs against the library's `main` fixture, so a contract change is seen before it is released.

### B5. Acceptance

The first consuming store re-runs its evaluation question set through the tools, against a store whose relationships layer (#455) comes from **the library stage**, not the store's script, so parity is tested end to end. The comparison arm is the best existing Claude Code arm (E).

**Measured with `knowledgestore answers --compare`.** The server passes when all of these hold:
- **Non-inferior on accuracy.** The lower 95% confidence bound of (MCP − E) is above −0.03. "At least equal" would flip on noise near the ceiling.
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

1. Library: #454, #455 and #456, in `knowledge-store-builder`. Each ships in a library minor release and is useful without the server.
2. Server: B1–B4, here.
3. First store: B5 acceptance.
