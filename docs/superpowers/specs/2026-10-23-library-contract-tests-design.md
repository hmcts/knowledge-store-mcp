# Library contract tests: every tool over a store the library wrote

## Why

The server reads files another repository writes. Its tests mostly run against
`tests/fixture_store/build.py`, a synthetic store that describes the layout *as
this server understands it*. When the two disagree, the fixture agrees with the
server and the tests stay green. That is how v0.1.1's by-repo bug shipped, and
`tests/library_store/` (real library output, relationships and manifest only) is
what caught it.

Today 5 of the 13 tools run against library output (`find_repo`, `repo_answer`,
`dependents`, `dependencies`, `answers`). The other 8, and every read of the
graph, the index, intent, ticket descriptions, the tracker, labels, summaries,
topics, deep dives and provenance, are checked only against the hand-written
fixture.

The goal: **no missing tests.** Every tool, and every layer field the server
depends on, is exercised against a store the library itself produced, so a change
on either side that breaks the product's expected behaviour fails a test here.

## Goals

1. One generated store, committed under `tests/library_store/store/`, holding
   every layer the server reads, produced by the library's real stages at one
   pinned release, plus the graph produced by a pinned graphify.
2. Tests that call all 13 tools against that store, in both read modes (index
   present; index absent, so the graph fallback), asserting hand-derived values.
3. A written coverage map (rule 2.9): for each tool and each layer field the
   server reads, the test that covers it, falsifiably.
4. A guard that fails when a tool is added to `server.TOOLS` without a contract
   test.
5. Regeneration per library release is one command, and reproducible: two runs
   produce identical bytes.

## Non-goals

- Replacing the synthetic fixture. It holds deliberate traps (damaged FTS,
  escaping paths, newer minors, refusals) the library will never write. It stays.
- Running the generator in CI. The library is not installable under CI's
  `uv sync --no-build`, and generation needs the network for package and model
  downloads. CI runs the tests over the committed output, as today.
- The minimum-standard layer until a library release writes it (see Phasing).
- Hosting or M365 work.

## Anti-goals (a failure even if it works)

- **A hand-written layer file.** Never fake a generated artefact to test its
  consumer (KSB `CLAUDE.md` 2.2). Only *inputs* are hand-written: clone contents,
  commit messages, `config/*`, and the stand-ins for what an agent authors
  (summaries JSON, community labels, topic and deep-dive Markdown, answers
  Markdown).
- **Mocking our own code, or the library's.** Stubs only at the true IO edge,
  through the library's own seams. Never assert a stand-in was called.
- **Expected values computed by the code under test** or read back from the
  generated store. They are derived by hand from the inputs and written as
  literals.
- **Any real store's names, repositories or counts.** This repository is public.
  Invented names only (`repo-a`, `example-org`, `DEMO-1`).

## Constraints

- Library pinned at **v0.18.3** (manifest format 1.3). Graphify pinned at
  **`graphifyy[terraform,sql]==0.9.93`**, the library's floor.
- Graphify 0.9.93 `update` is offline, calls no model, and is deterministic:
  two runs over the same clone gave byte-identical `graph.json`, communities
  included (spike, 2026-10-23).
- Store bytes must be reproducible: fix author and committer dates
  (`GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`) and identities on every synthetic
  commit, so shas, `committed` dates and `sources_as_of` are the same every run.
- Repository conventions: gates (`ruff check`, `ruff format --check`, `pyright`,
  `unittest discover`) chained with `&&`; every test names the break it catches;
  every new test bite-checked against broken code; no secret-looking values in
  any commit (`scan` is required).

## Decided

- **Graph: run graphify for real** (my call, from the spike), following the
  library's documented procedure end to end, not a shortcut through it: per-clone
  `graphify update .`, `merge-inputs`, `merge-graphs`, then `gherkin`,
  `packages`, `deployments`, `source-urls`, then **re-cluster the merged graph**
  through graphify's Python API as `docs/creating-a-store.md` prescribes (never
  `graphify cluster-only`), `record-clustering`, and the deterministic re-gzip.
  `merge-graphs` alone leaves each clone's community ids colliding.
  Community *labels* are agent-authored in the real flow, so they are a
  hand-written input, at the same boundary as summaries.
- **The partitioner is pinned** (my call): the generator's environment pins
  every package graphify's clustering imports, including whether `graspologic`
  is present (Leiden) or not (Louvain), and records the choice beside the
  library version. **The first build task proves the re-clustered graph is
  byte-identical across two runs.** If it is not, stop and bring it back: the
  graph cannot be hand-written, and a non-reproducible store breaks goal 5.
- **Expected values for graph content** (my call, from the rules): node ids
  follow graphify's id scheme (path segments plus the symbol name, lower-cased,
  e.g. `src/refund.ts` `refund` → `src_refund_refund`) and are derived by hand
  from the clone source. Community ids are graphify's choice, not an input, so
  tests never assert a community *number*: they assert membership and content —
  "the community holding node X has label L, holds node Y, and its summary is
  S". The generator keys the authored labels and summaries by community the same
  way a real author does: it reads `summaries extract`'s output (the stage that
  exists for that purpose) and attaches each hand-written text to the community
  holding a named anchor node. The text itself, and which node anchors it, are
  the hand-written inputs.
- **Index absent is a second library run** (my call): the same inputs built
  again without the `index` stage, committed as `tests/library_store/no-index/`,
  so its manifest records the index absent exactly as the library would. Deleting
  the index from a copy would edit the library's output and exercise a different
  server path (index declared but missing), which the synthetic fixture already
  covers.
- **Agent-authored layers** (summaries, topics, deep dives, answers): hand-written
  authored input, then the library's real `merge` / `answers-layer` stage.
- **Tracker:** the real `fetch_tickets.main` driven in-process through its
  `opener=` seam with canned Jira-search responses, and `today=` fixed so the
  tracker's `as_of` is reproducible. Nothing else is stubbed. The tracker token
  is an obviously fake placeholder (`fixture-not-a-secret`).
- **Semantic:** the real `knowledgestore semantic` stage with the pinned default
  model, recorded in its manifest (my call). The server reads only its presence,
  so if it proves non-deterministic, fall back to recording that layer as absent
  and say so; never hand-write it.
- **Provenance:** the real `sync`, cloning from local bare repositories so it is
  offline; `config/repositories.txt` carries `https://github.com/example-org/...`
  URLs for the source-URL stage — the builder resolves how sync clones locally
  while the list names GitHub (e.g. a pre-placed clone, which sync fetches).
- **Where it lives:** extend `tests/library_store/generate.py` and its committed
  `store/`; tests split by area into `tests/test_library_*.py`, sharing a
  session helper. The existing `test_library_store.py` assertions are kept.

## The synthetic estate (inputs)

The builder chooses exact contents; each must earn its place by feeding a layer
or a trap the server must handle. At minimum:

- **Three clones**, e.g. `repo-a` (a TypeScript library publishing an npm
  package), `repo-b` (depends on it; code calling across two files so graphify
  makes edges and more than one community), `repo-c` (a deploy-values repo for
  the `deployments` stage).
- **Commits** whose subjects carry ticket keys (`DEMO-1: ...`), at least one key
  on more than one file and repo, and one branch-only (proposed) key, so intent,
  ticket descriptions and the `proposed` path are non-empty.
- **Tracker responses** for some keys; at least one key the tracker denies or
  reports absent, if the stage records those.
- **Authored inputs:** labels for every community; a summary for at least one
  community (60–700 chars; `KSB_MIN_COMMUNITY_SIZE=1`); one topic brief; one deep
  dive stating its repo's short sha; two answers-layer Markdown files, one with
  `date:` front matter and one relying on its commit date.
- **Extra layers:** the existing `ownership_notes` layer inside the
  relationships directory, plus the `answers-layer` output declared in
  `config/store-extra-layers.json`.

## What the tests cover

For every tool, against the library store, with the expected value hand-derived
from the inputs above:

| Tool | Must be shown working on library output |
|---|---|
| `store_info` | every layer's presence as the library reports it; index schema; provenance shas; `stale_days` from `sources_as_of` |
| `find_repo` | in estate (each source: index `repos`, provenance, by-repo); not in estate; outside-estate target |
| `search` | a label hit with node, file, repo, url; `repo=` scope; index and graph-fallback modes agree |
| `node` | a known node's fields; unknown id |
| `neighbours` | relations and targets of a node with edges; both modes |
| `community` | members, label and summary for a summarised community; one without a summary |
| `repo_answer`, `dependents`, `dependencies` | existing assertions, plus `kind=` against the library's `kinds.json` |
| `tickets` | landed tickets for a file; proposed tickets with branches; both modes |
| `ticket` | files, description fields, tracker fields and `as_of`; a key with no tracker record |
| `answers` | topics, deep dives, the answers-layer extra and the relationships-directory extra; dates from `authored`/`as_of` as v0.2.x reads them |
| `minimum_standard` | absent layer reported as absent (until phase 2) |

Plus: the manifest names the pinned library release and format 1.3; the
committed store reproduces byte-for-byte from the generator; a tool added to
`server.TOOLS` without a matching contract test fails.

## Phasing

1. **This change:** pin v0.18.3; every layer except minimum-standard.
2. **When KSB #495 is released** (manifest 1.4, minimum-standard layer): move
   the pin, add minimum-standard inputs and assertions, and regenerate
   `tests/fixture_store/generate_minimum_standard.py` against it (backlog item 4
   of the handover). KSB flags format changes ahead of the pin.

## Left to the builder

- Exact file contents, names and counts of the synthetic estate.
- Whether the generator runs library stages via `uvx --from <git url>
  knowledgestore` or one `uv run --with` environment (the tracker stage must run
  in-process to pass `opener=`).
- Test-module split and helper names.
- How reproducibility is checked (e.g. the generator's `--check` mode that
  regenerates into a temp dir and diffs).

## After this lands

Tell KSB the pinned release and graphify version, so library changes to any
layer read here are flagged before the pin moves.
