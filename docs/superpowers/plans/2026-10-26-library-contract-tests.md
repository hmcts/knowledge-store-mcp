# Library Contract Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every one of the server's 13 tools is tested against a store that knowledge-store-builder v0.19.0 and graphify 0.9.93 actually wrote, in both index modes.

**Architecture:** `tests/library_store/generate.py` (stdlib, run by hand) builds a tiny invented estate in a temp dir and runs `tests/library_store/pipeline.py` inside a pinned `uv run --with` environment holding the library and graphify. `pipeline.py` runs the library's real stages in its documented order, stubbing only the IO edges through the library's own seams (`fetch_tickets.main(opener=, today=)`, the `runner` of `minimum-standard-settings`). The output is committed twice: `store/` (full) and `no-index/` (same inputs, no `index` stage). Tests call the tools against both through one harness and assert hand-derived literals.

**Tech Stack:** Python 3.11 stdlib `unittest`, `uv`, knowledge-store-builder v0.19.0, `graphifyy[terraform,sql]==0.9.93`.

**Spec:** `docs/superpowers/specs/2026-10-23-library-contract-tests-design.md`. Read it first; its Anti-goals bind every task.

## Global Constraints

- Library `git+https://github.com/hmcts/knowledge-store-builder@v0.19.0`, package `hmcts-knowledge-store-builder`, extras `[semantic,deploy]`; graphify `graphifyy[terraform,sql]==0.9.93`; every other package frozen in `tests/library_store/requirements.txt`.
- Library docs at that tag are the procedure: `docs/creating-a-store.md` ("Build the store" through "Build the query index") and `skills/knowledge-store-build/SKILL.md` ("Clustering: `cluster-only` does not persist its result"). Read them with `git -C ../knowledge-store-builder show v0.19.0:<path>`.
- Invented names only: org `example-org`, store id `library-contract-store`, repos `alpha-lib`, `beta-app`, `delta-deploy`, ticket keys `DEMO-n`. No real store, estate, repo name or count anywhere (public repo).
- Never hand-write a layer file. Hand-written = clone contents, commit messages, `config/*`, tracker/GitHub API responses, and agent-authored inputs (labels, summaries JSON, topic/deep-dive/answers Markdown).
- Never mock this repo's code or the library's; never assert a stand-in was called.
- Expected values are literals derived from the inputs, written before reading the output. Git shas are the one exception: the generator records each input clone's HEAD sha in `tests/library_store/inputs.json` (input-side, from `git rev-parse` on the clones it made) and tests read them from there, never from the store.
- Every test's docstring opens `Catches:` and names the production break; every new test is bite-checked (break the code, see it fail, restore, confirm by `shasum`, clear `__pycache__` or run with `python -B`).
- Fake secret-looking values are exactly `fixture-not-a-secret`.
- Gates, chained with `&&` into every commit, never piped:
  `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run python -m unittest discover -s tests`
- Stage explicit paths; never `git add -A`. Delete with `find … -delete` (a hook blocks `rm -r`).
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A library stage exits 0 but writes nothing (e.g. `fetch-tickets` without `KSB_TRACKER_BASE_URL`/`KSB_TRACKER_TOKEN`) — the generator must refuse to commit a store whose manifest lacks any expected layer (Task 1 Step 6, Task 2 Step 5).
2. Re-clustering leaves a node without `community` — the generator must refuse (Task 1 Step 6).
3. A regeneration that picks up a previous run's output — the generator always builds in a fresh temp dir and `--check` names each differing file (Task 1 Step 7).
4. The re-clustered graph differs between runs (environment drift) — `--check` fails and names `graphify-out/graph.json.gz` (Task 1 Step 7); if it cannot be made identical, stop and report.
5. A server tool silently mis-reads a v0.19.0 shape (renamed minimum-standard items, json-schema kinds) — each such shape has a test that names it (Tasks 4 and 5).

---

### Task 1: Pinned pipeline through the graph, relationships and manifest; reproducibility gate

**Files:**
- Create: `tests/library_store/pipeline.py` (runs inside the pinned env; imports `knowledgestore`, `graphify`)
- Create: `tests/library_store/requirements.txt` (frozen env)
- Create: `tests/library_store/inputs.json` (written by the generator)
- Modify: `tests/library_store/generate.py` (rewrite; keep `VERSION`, `OUT`, `EXTRA_LAYER` names)
- Modify: `tests/library_store/store/**` (regenerated)
- Create: `tests/test_library_generation.py`
- Modify: `tests/test_library_store.py` (literals that change at v0.19.0)

**Interfaces:**
- Produces: `generate.py` constants `VERSION = "0.19.0"`, `GRAPHIFY = "0.9.93"`, `OUT = HERE / "store"`, `NO_INDEX = HERE / "no-index"` (used from Task 2), `EXTRA_LAYER`, `INPUTS = HERE / "inputs.json"`; CLI `uv run python tests/library_store/generate.py [--check]`. `inputs.json` = `{"shas": {"alpha-lib": "<40 hex>", ...}}`.
- Produces: `pipeline.py` CLI `python pipeline.py <root> [--no-index]`, exit 0 only when every expected layer is present.

- [ ] **Step 1: Freeze the environment.** `uv pip compile` an input of the two pinned requirements (library from the git tag with `[semantic,deploy]`, graphify pin) into `requirements.txt` with hashes off. Note in its header comment whether `graspologic` resolved (Leiden) or not (Louvain). Do not add or remove `graspologic` by hand.
- [ ] **Step 2: Write the estate inputs in `generate.py`.** Three clones, each a git repo committed with fixed identity and `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` per commit (distinct dates in October 2026):
  - `alpha-lib`: TypeScript, `package.json` `@example/alpha-lib` 1.2.0; `src/refund.ts` with `export function refund(...)` and `export class RefundHandler` calling it; `schemas/refund.schema.json` (a JSON Schema with `$id`, one `$defs` definition, a property with `"format": "date"` and a `$ref` to the definition). Commits `DEMO-1: refund handler`, `DEMO-2: refund schema`.
  - `beta-app`: depends on `@example/alpha-lib` `^1.2.0`, plus one dependency the estate does not hold whose name `relationships` resolves to a repository under `example-org` (read the library's outside-estate rule in `relationships.py` to choose the coordinate, e.g. a Maven or npm package named for `gamma-service`), so the registry records an unresolved target outside the estate; `src/pay.ts` importing `refund`, `PaymentService.pay` calling it; `.github/CODEOWNERS`, `SECURITY.md` with a contact, `.github/dependabot.yml`, `.github/workflows/codeql.yml` (`github/codeql-action/init@v3`). Commits `DEMO-1: pay via refund`, `DEMO-3: ownership`. One extra branch `feature/DEMO-4-retry` with a commit `DEMO-4: retry` that is not merged (the proposed key).
  - `delta-deploy`: `ansible/group_vars/prd/beta-app_values.yaml.j2` with `replicas: 2`, and `config/application.yml` holding `password: fixture-not-a-secret`. Commit `DEMO-3: production values`.
  - Bare copies under `<tmp>/remotes/<name>.git`; `config/repositories.txt` lines `name|https://github.com/example-org/<name>.git|main`; git's `url.<file:///tmp/remotes/>.insteadOf=https://github.com/example-org/` passed through `GIT_CONFIG_COUNT/KEY_0/VALUE_0` in the stage env so the real `sync` clones offline. If the library's `git_env` strips that, stop and report rather than working around it.
  - Record each clone's HEAD sha into `inputs.json`.
- [ ] **Step 3: `pipeline.py`, source and graph half,** in this order, each via `knowledgestore.cli.main([...])` or `subprocess` of the env's `knowledgestore`, env `KSB_ROOT`, `KSB_GITHUB_ORG=example-org`, `KSB_STORE_ID=library-contract-store`, `KSB_MIN_COMMUNITY_SIZE=1`, `KSB_DEPLOY_REPOS=delta-deploy`: `sync`, `export-history`, `context`, `intent`; per clone `graphify update .` (cwd the clone); `merge-inputs --strict`; `graphify merge-graphs repositories/*/graphify-out/graph.json --out graphify-out/graph.json`; write `graphify-out/.graphify_detect.json` from graphify's detect API as the docs say, then `content-set`; `gherkin`; `json-schema --unresolved <tmp>/unresolved.json`; `packages`; `deployments`; `relationships`; `source-urls`; cluster via `graphify.cluster.cluster()` + `label_communities_by_hub()` writing `community` and `community_name` onto every node (no previous clustering, so no remap); `record-clustering`; `gzip -9 -n` the graph. Labels: after clustering, write `graphify-out/.graphify_labels.json` keyed by the community holding each anchor node — `alpha-lib` `refund` → `"Refunds"`, `beta-app` `PaymentService` → `"Payments"`, the json-schema `schema` node → `"Refund schema"`; any other community gets `"Other <n>"`. Keep the existing `ownership_notes` extra layer input. Finish with `manifest`.
- [ ] **Step 4: Copy out.** `generate.py` copies from the temp root into `OUT`: every path of every layer the manifest marks present, except the plain `graphify-out/graph.json` and `knowledge/index/store.sqlite` (a store commits the archives), plus `graphify-out/clustering-inputs.json` and the extra layers. Clear `OUT` file-by-file first (keep `_clear`).
- [ ] **Step 5: Write the failing tests** in `tests/test_library_generation.py`, reading `OUT` as an artefact:
  - `test_manifest_is_the_pinned_release_and_format`: `library_version == "0.19.0"`, `format == {"major": 1, "minor": 4}`. Catches: store regenerated from another release without the record moving.
  - `test_every_node_has_a_community`: every node in `graph.json.gz` has an int `community`. Catches: a clustering that did not persist.
  - `test_the_partitioner_is_recorded`: `clustering-inputs.json` names the partitioner noted in `requirements.txt`'s header.
  - `test_graph_layers_present`: manifest `present` true for `graph`, `labels`, `provenance`, `history`, `context`, `repository_manifest`, `content_files`, `intent`, `ticket_descriptions`, `relationships`; `deployments.present` true.
- [ ] **Step 6: Implement until they pass.** `pipeline.py` exits non-zero, naming the layer, if any layer in Step 5's list is not present or any node lacks a community; `generate.py` then writes nothing.
- [ ] **Step 7: Reproducibility.** `generate.py --check` builds into a fresh temp dir and compares every file with `OUT` byte-for-byte, printing each differing path and exiting 1. Run `generate.py` then `generate.py --check` twice. Expected: exit 0 both times. If the graph differs, stop and report (spec: Decided, partitioner).
- [ ] **Step 8: Update `tests/test_library_store.py`** for v0.19.0: `VERSION` import unchanged; NPM edge `examples[0].url` is now the github URL to `beta-app/package.json` at `inputs.json` sha (derive the line number by hand from the `package.json` text). Run the gates; all pass.
- [ ] **Step 9: Commit** `tests/library_store/{generate.py,pipeline.py,requirements.txt,inputs.json,store}` and both test files.

### Task 2: The remaining layers, and the no-index store

**Files:**
- Modify: `tests/library_store/pipeline.py`, `tests/library_store/generate.py`
- Create: `tests/library_store/tracker_responses.json`, `tests/library_store/github_responses.json` (canned IO; hand-written)
- Create: `tests/library_store/no-index/**`
- Modify: `tests/test_library_generation.py`

**Interfaces:**
- Consumes: Task 1's `pipeline.py` order and env.
- Produces: `store/` with every layer present except none; `no-index/` identical inputs, no `index` stage.

- [ ] **Step 1: Write the failing tests:** `test_every_library_layer_is_present` — for `store/`, manifest `present` is true for all 19 library layers (`graph` … `minimum_standard`, `index`) and `ticket_tracker`; extra layers `ownership_notes` and `answers` present. `test_no_index_store_is_the_same_store_without_its_index` — `no-index/` manifest has `index.present` false and every other layer's `present` equal to `store/`'s. Run: fail.
- [ ] **Step 2: Tracker.** After `intent`, call `knowledgestore.fetch_tickets.main([], opener=<reads tracker_responses.json>, sleep=lambda s: None, today="2026-10-20")` with `KSB_TRACKER_BASE_URL=https://tracker.example.invalid`, `KSB_TRACKER_TOKEN=fixture-not-a-secret`, `KSB_TRACKER_PROJECTS=DEMO`. Responses (Jira-search shape the stage parses): `DEMO-1` summary `Refund customers`, status `Done`, resolution `Fixed`, description `Let staff refund a payment.`, one comment; `DEMO-3` status `In Progress`; `DEMO-2` absent from the response. Match the response to the request URL the opener receives; an unexpected URL raises.
- [ ] **Step 3: Minimum standard.** `minimum-standard-settings` through its `runner` seam with `github_responses.json` (per `gh api` path): `alpha-lib` not archived, default branch `main` protected, secret scanning enabled, Dependabot alerts enabled; `beta-app` same but branch not protected; `delta-deploy` settings unreadable (runner raises the not-found error the stage expects); org `.github` repository not public. Then `minimum-standard`. Write `config/products.json` `{"payments": ["alpha-lib", "beta-app"]}` first.
- [ ] **Step 4: Authored layers.** `summaries extract`; author `{<community of alpha-lib refund>: "<2–4 sentences, 60–700 chars, naming refund and RefundHandler>"}`; `summaries merge`. `config/topics.txt` `refunds | Refunds | refund, payment`; `docs/topics/refunds.md` ≥800 chars; `topics merge`. `deepdive extract alpha-lib`; `docs/deep-dives/alpha-lib.md` ≥800 chars containing alpha-lib's short sha; `deepdive merge`. `docs/answers/how-refunds-work.md` with `date: 2026-10-18` front matter and `docs/answers/who-approves-refunds.md` without (committed in the store's own git repo with a fixed date `2026-10-19`); `answers-layer --markdown docs/answers --out knowledge/answers.json` and declare `answers` in `config/store-extra-layers.json` beside `ownership_notes`. Then `semantic`, `explorer`, `index`, `manifest`. Write every authored text as a literal in `generate.py`; tests will quote them.
- [ ] **Step 5: No-index.** `generate.py` runs `pipeline.py` a second time into a second temp root with `--no-index` (skips only `index`) and copies to `NO_INDEX` the same way. `pipeline.py` refuses (exit 1, names the layer) if any expected layer is absent.
- [ ] **Step 6:** Run `generate.py`, `generate.py --check` (now covering both dirs; if `semantic` is not reproducible, drop that stage, record why in `generate.py`'s docstring, and expect `semantic.present` false). Run gates; pass.
- [ ] **Step 7: Commit** the named files and both store dirs.

### Task 3: Test harness and the graph tools

**Files:**
- Create: `tests/library_store/harness.py`
- Create: `tests/test_library_graph.py`

**Interfaces:**
- Produces: `harness.LibraryStoreCase(unittest.TestCase)` with class attribute `STORE: Path` and `self.session` (built as `test_library_store.py` builds it, `NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)`); `harness.both_modes(mixin) -> tuple[type, type]` returning `(WithIndex, WithoutIndex)` concrete test classes for a mixin, so every assertion runs against `OUT` and `NO_INDEX`. `harness.SHAS: dict[str, str]` from `inputs.json`.

- [ ] **Step 1: Write failing tests** as a mixin run in both modes (names are the contract; derive every literal from Task 1/2 inputs):
  - `store_info`: index reported (`meta.index_schema` 1.x) in `store/`, absent with the fallback reason in `no-index/`; `provenance` shas equal `SHAS`; `stale_days` from `sources_as_of` (= latest input commit date) at `NOW`; every layer's presence as in Task 2.
  - `find_repo`: each of the three repos "in the estate" with its sha as `cloned_at`; `gamma-service` "not in the estate" with the registry's outside-estate reference from `beta-app`; `zeta-unknown` "not in the estate" with no reference.
  - `search`: `RefundHandler` → node in `alpha-lib`, file `src/refund.ts`, url `https://github.com/example-org/alpha-lib/blob/<sha>/src/refund.ts#L<line>`; `repo="beta-app"` scope finds `PaymentService` and not `RefundHandler`.
  - `node`: the `RefundHandler` id (derive from graphify's scheme plus `merge-graphs`' repository prefix; confirm the prefix rule from graphify source, not the output) returns label, kind, repo, file, url; an unknown id returns the not-found answer.
  - `neighbours`: `PaymentService.pay` has a `calls` edge to `refund`.
  - `community`: the community holding `refund` has label `Refunds`, holds `RefundHandler`, and its summary is Task 2's text with `summary_as_of` = `sources_as_of` date; the `Payments` community has no summary.
  - json-schema: `search(kind="schema")` finds the refund schema node with `metadata.format == "json-schema"`; `search(kind="field")` finds the `format: date` property; `neighbours` of the schema shows `contains` to its definition and the field's `references` to the definition; no graphify keyword node remains for `schemas/refund.schema.json` (search for a JSON keyword such as `$defs` scoped to `alpha-lib` returns no node from that file).
- [ ] **Step 2:** Run `uv run python -m unittest tests.test_library_graph -v`. Expected: failures only where the server mis-reads library output. For each, keep the test, fix `src/knowledgestore_mcp/` minimally, and bite-check the test against the unfixed code. A disagreement that is a library choice, not a server bug, goes to the controller to raise with KSB — do not "fix" it in the test.
- [ ] **Step 3:** Gates pass. **Step 4: Commit** `harness.py`, the test file, and any `src/` fix.

### Task 4: Ticket and answer tools

**Files:**
- Create: `tests/test_library_tickets.py`

**Interfaces:**
- Consumes: `harness.both_modes`, `harness.SHAS`.

- [ ] **Step 1: Write failing tests** (both modes):
  - `tickets` on `alpha-lib` `src/refund.ts`: `DEMO-1` landed; on `beta-app` a file touched by `DEMO-4`'s branch: `DEMO-4` proposed with branch `feature/DEMO-4-retry`.
  - `ticket("DEMO-1")`: files in both `alpha-lib` and `beta-app`; tracker title `Refund customers`, status `Done`, resolution `Fixed`, description and one comment, `as_of` `2026-10-20`.
  - `ticket("DEMO-2")`: files listed, tracker reports the key absent (as the server words an absent record).
  - `ticket("DEMO-9")`: not found.
  - `answers("refund")`: items from `topics` (`refunds`), `deep_dives` (`alpha-lib`), `answers` (`how-refunds-work` `as_of` `2026-10-18`; `who-approves-refunds` `as_of` `2026-10-19` with its commit) and none from `ownership_notes`; `answers("owns")` still returns `who-owns-alpha-lib` from `ownership_notes`. Topic and dive dates are `sources_as_of` (v0.2.x behaviour; `authored_as_of` is a later backlog item — do not implement it here).
- [ ] **Step 2:** Run; fix server mis-reads as in Task 3 Step 2. **Step 3:** Gates. **Step 4: Commit.**

### Task 5: Relationships and minimum standard

**Files:**
- Modify: `tests/test_library_store.py` (move into the harness, both modes; keep every existing assertion)
- Create: `tests/test_library_minimum_standard.py`

- [ ] **Step 1: Write failing tests:**
  - relationships, both modes: existing assertions; `dependents("alpha-lib", kind="npm_package")` returns `beta-app`; `kind="no-such-kind"` is refused naming the library's `kinds.json` keys; `delta-deploy` has no edges and is in the estate.
  - minimum standard, both modes: `minimum_standard()` lists the three repos and product `payments` → `alpha-lib`, `beta-app`; for `beta-app` the point holding branch protection is not `present` and cites `branch_protection`; the point holding code scanning cites `code_scanning_workflow` as `present` with path `.github/workflows/codeql.yml`; `delta-deploy`'s secret point carries a finding with `rule`, `path` `config/application.yml` and `line` 2 and never the value; `delta-deploy`'s settings-based items are `not_determinable` with reason naming the unreadable setting; `unmaintained` is `not_determinable` (not archived) citing the last commit; `repos=["alpha-lib"]` returns only `alpha-lib`; `repos="alpha-lib,beta-app"` still accepted. Derive which item maps to which of the seven points from the library's `minimum_standard.py` at v0.19.0, not from the output.
- [ ] **Step 2:** Run; fix server mis-reads as in Task 3 Step 2 (renamed hygiene items are the likeliest). **Step 3:** Gates. **Step 4: Commit.**

### Task 6: Coverage map and the new-tool guard

**Files:**
- Create: `tests/library_store/COVERAGE.md`
- Create: `tests/test_library_coverage.py`

- [ ] **Step 1:** Write `COVERAGE.md`: one table row per tool in `server.TOOLS` and per layer field from the spec's "What the tests cover", each naming the test id(s) (`module.Class.test_name`) that would fail if that read broke — falsifiable, rule 2.9.
- [ ] **Step 2: Write failing tests:** `test_every_tool_has_a_contract_test` — every `ToolSpec.name` in `server.TOOLS` appears in a `COVERAGE.md` row with at least one test id. Catches: a 14th tool added without a contract test. `test_every_cited_test_exists` — every test id in `COVERAGE.md` resolves via `unittest.defaultTestLoader.loadTestsFromName`. Catches: a renamed test leaving the map claiming coverage it no longer has. Bite-check both (drop a row; misspell an id).
- [ ] **Step 3:** Gates. **Step 4: Commit.**

### Task 7: Minimum-standard fixture moved to v0.19.0

**Files:**
- Modify: `tests/fixture_store/generate_minimum_standard.py`, `tests/fixture_store/build.py` (minimum-standard literals), `tests/fixture_store/DESIGN.md` (section 11), `tests/test_tools_minimum_standard.py` as needed, `src/knowledgestore_mcp/tools_minimum_standard.py` if needed

- [ ] **Step 1:** Check out library v0.19.0 into a scratch worktree (`git -C ../knowledge-store-builder worktree add <scratch>/ksb-v0.19.0 v0.19.0`) and run `uv run python tests/fixture_store/generate_minimum_standard.py <scratch>/ksb-v0.19.0/src`. Expected: exit 1 with the writer's records (items renamed).
- [ ] **Step 2:** Update the clones in `generate_minimum_standard.py` only where an item needs a new input (`secret_scanning_workflow`, `vulnerability_alerts`, `branch_protection` via the settings snapshot), then transcribe the writer's records into `build.py` and DESIGN.md 11. Re-run: exit 0.
- [ ] **Step 3:** Run the suite. Where `test_tools_minimum_standard.py` fails, decide per failure whether the literal moved (update it) or the server mis-reads (fix `src/`, bite-check). Gates pass.
- [ ] **Step 4:** Remove the scratch worktree (`git worktree remove`). **Commit.**

### Task 8: End-to-end run, docs, handoff

**Files:**
- Modify: `tests/library_store/generate.py` docstring, `README.md` (the testing/contributing section that describes `library_store`), `docs/architecture.md` only if it names the test stores

- [ ] **Step 1: End-to-end (rule 2.4).** Start the real server over stdio on `tests/library_store/store` (as `tests/test_stdio.py` does) and drive `tests/fixture_store/sweep.py`'s `CALLS` (adjusted to Task 1's names) through it; save the output to the scratchpad and read every answer: each non-empty where the inputs say it should be, no host path in any answer, no `fixture-not-a-secret` in any answer. Repeat on `no-index/`.
- [ ] **Step 2:** Docs: how to regenerate, what is hand-written vs library output, the pins, and that `COVERAGE.md` is the coverage claim. Gates pass. **Commit.**
- [ ] **Step 3:** Controller (not a subagent) tells KSB the pins landed and lists any server fix that came from a library shape.
