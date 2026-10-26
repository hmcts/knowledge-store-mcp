"""The library's stages over a store root, in the order its docs give.

    python pipeline.py <root> [--no-index]

Runs inside the frozen environment `generate.py` builds from `requirements.txt`,
so `knowledgestore` and `graphify` here are the pinned releases; `generate.py`
writes the inputs and calls this. It follows `docs/creating-a-store.md` ("Build
the store" onwards) and the build skill's clustering procedure at the pinned
tag: each library stage runs as `python -m knowledgestore <stage>`, and graphify
runs from inside each clone, then merges, then the merged graph is re-clustered
through graphify's Python API (never `graphify cluster-only`).

The hand-written things it writes are what an agent authors in a real build:
the community labels file, and the summaries JSON. Each label and summary is
attached to the community holding a named anchor node, the way an author names a
community by what is in it. The summary texts come from `generate.py`, which
writes every other authored input (topic, deep-dive and answers Markdown) too.

Only the IO edges are stood in for, through the library's own seams:
`fetch_tickets.main(opener=, sleep=, today=)` answers from
`tracker_responses.json`, and `build_minimum_standard_settings.main(runner=)`
answers each `gh api` path from `github_responses.json`. A request either file
does not hold raises, so a stage cannot read an unexpected one as "nothing".

`--no-index` skips the `index` stage and nothing else.

Exits 0 only when every layer in `EXPECTED` (and `EXPECTED_EXTRA`) is present
in the manifest - `index` absent under `--no-index` - and every node in the
committed graph carries a community; otherwise names what is wrong and exits 1,
so `generate.py` commits nothing.
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ORG = "example-org"
STORE_ID = "library-contract-store"

HERE = Path(__file__).resolve().parent
TRACKER_RESPONSES = HERE / "tracker_responses.json"
GITHUB_RESPONSES = HERE / "github_responses.json"

# Layers the manifest must mark present, in the `layers` table: every one
# v0.19.0 names, its 18 `LAYERS` and `deployments`. Kept in step with
# `LIBRARY_LAYERS` in tests/test_library_generation.py, which asserts the same
# list over the committed store. `index` is absent, and must be, under
# `--no-index`.
EXPECTED = (
    "graph",
    "labels",
    "provenance",
    "repository_manifest",
    "context",
    "content_files",
    "history",
    "intent",
    "ticket_descriptions",
    "ticket_tracker",
    "summaries",
    "topics",
    "deep_dives",
    "semantic",
    "explorer",
    "relationships",
    "minimum_standard",
    "index",
    "deployments",
)
# The store's own layers, from `config/store-extra-layers.json`.
EXPECTED_EXTRA = ("answers", "ownership_notes")
INDEX = "index"

# Hand-written community labels: the community holding each anchor node gets
# the label; every other community is "Other <n>". Node ids are graphify's
# scheme (path segments and symbol, lower-cased) behind `merge-graphs`' repository
# prefix, and the json-schema stage's `jsonschema:<file>#<pointer>`.
ANCHORS = (
    ("alpha-lib::src_refund_refund", "Refunds"),
    ("beta-app::src_pay_paymentservice", "Payments"),
    ("alpha-lib::jsonschema:schemas/refund.schema.json#", "Refund schema"),
)

# Clones with no code for graphify to extract: deployment values only.
UNEXTRACTED = ("delta-deploy",)

GRAPH = Path("graphify-out/graph.json")
LABELS = Path("graphify-out/.graphify_labels.json")
MANIFEST = Path("knowledge/store-manifest.json")

# The tracker's answers, as `fetch-tickets` asks after `intent`: the project
# allowlisted, description and comments requested, and the day fixed so an open
# ticket's `checked` date is the same every run. The token is a placeholder.
TRACKER_SETTINGS = {
    "KSB_TRACKER_BASE_URL": "https://tracker.example.invalid",
    "KSB_TRACKER_TOKEN": "fixture-not-a-secret",
    "KSB_TRACKER_PROJECTS": "DEMO",
    "KSB_TRACKER_FETCH_DESCRIPTION": "1",
    "KSB_TRACKER_FETCH_COMMENTS": "1",
}
TRACKER_TODAY = "2026-10-20"

# The stage itself, called in a process of its own because `knowledgestore.config`
# reads the settings at import. The opener answers a URL from the file or raises;
# not an `OSError`, which the stage would record as a failed page and carry on.
FETCH_TICKETS = """\
import json, sys
from knowledgestore import fetch_tickets

responses = json.loads(open(sys.argv[1], encoding="utf-8").read())


def opener(url, headers):
    if url not in responses:
        raise LookupError("no canned tracker response for " + url)
    return fetch_tickets.Response(200, json.dumps(responses[url]))


sys.exit(fetch_tickets.main([], opener=opener, sleep=lambda seconds: None, today=sys.argv[2]))
"""

# `minimum-standard-settings` with `gh api` answered from the file: 200 with a
# body, 204 with none, 404 as `gh` reports it (exit 1, GitHub's error JSON on
# stdout). Any other call raises.
#
# The clock: the stage stamps `read_at` with `datetime.now()`, and v0.19.0 gives
# it no seam for that, so the stamp - copied into every minimum-standard file
# and the manifest's digests - would differ on every run. Here, for this one
# call only, the module's `datetime` is rebound to one whose `now()` is fixed,
# and restored afterwards. It stands in for a missing clock seam: a library
# issue has been raised, and this moves to the seam once one is released.
MINIMUM_STANDARD_SETTINGS = """\
import json, subprocess, sys
from datetime import datetime, timezone
from knowledgestore import build_minimum_standard_settings

READ_AT = datetime(2026, 10, 20, tzinfo=timezone.utc)


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return READ_AT.astimezone(tz) if tz is not None else READ_AT

responses = json.loads(open(sys.argv[1], encoding="utf-8").read())


def runner(arguments):
    if len(arguments) != 2 or arguments[0] != "api" or arguments[1] not in responses:
        raise LookupError("no canned gh response for " + repr(arguments))
    answer = responses[arguments[1]]
    if answer["status"] == 404:
        error = {"message": "Not Found", "status": "404"}
        raise subprocess.CalledProcessError(1, ["gh", *arguments], output=json.dumps(error))
    return json.dumps(answer["body"]) if answer["status"] == 200 else ""


original = build_minimum_standard_settings.datetime
build_minimum_standard_settings.datetime = FixedClock
try:
    status = build_minimum_standard_settings.main(runner=runner)
finally:
    build_minimum_standard_settings.datetime = original
sys.exit(status)
"""

# The agent-authored summaries `generate.py` writes, keyed by anchor node id;
# this step re-keys them by the community holding each anchor.
AUTHORED = "authored"
SUMMARIES_BY_ANCHOR = "summaries-by-anchor.json"
SUMMARIES_OUT = "summaries-out.json"

# The store's own repository, which `answers-layer` reads dates from: one commit,
# of the one answer that has no `date:`, at a fixed identity and date.
ANSWERS_DIR = "docs/answers"
COMMITTED_ANSWER = "docs/answers/who-approves-refunds.md"
ANSWER_COMMITTED_AT = "2026-10-19T09:00:00+00:00"
STORE_IDENTITY = {
    "GIT_AUTHOR_NAME": "Fixture Author",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture Author",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": ANSWER_COMMITTED_AT,
    "GIT_COMMITTER_DATE": ANSWER_COMMITTED_AT,
}


class Refused(Exception):
    """The pipeline cannot vouch for what it built."""


def _home(root: Path) -> dict[str, str]:
    """A home and XDG directories inside the build's temp dir, so nothing under
    the user's own (graphify reads `~/.claude` for its installed skill) reaches
    the build."""
    home = root.parent / "home"
    dirs = {
        "HOME": home,
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local/share",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return {name: str(path) for name, path in dirs.items()}


def _environment(root: Path) -> dict[str, str]:
    """The stages' environment: the caller's, less every `KSB_*` setting it
    carries, plus this store's own. An operator's shell can export real
    settings - a tracker URL and token - and they would otherwise reach every
    stage: one guard run sent `fetch-tickets` at a real tracker, stopped only by
    the stub refusing the URL. fastembed's model cache goes under the isolated
    home too; its default is the system temp dir, shared across builds."""
    inherited = {name: value for name, value in os.environ.items() if not name.startswith("KSB_")}
    return {
        **inherited,
        **_home(root),
        "FASTEMBED_CACHE_PATH": str(root.parent / "home" / ".cache" / "fastembed"),
        "KSB_ROOT": str(root),
        "KSB_GITHUB_ORG": ORG,
        "KSB_STORE_ID": STORE_ID,
        "KSB_MIN_COMMUNITY_SIZE": "1",
        "KSB_DEPLOY_REPOS": "delta-deploy",
    }


def _run(
    root: Path, command: list[str], cwd: Path | None = None, extra: dict[str, str] | None = None
) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    env = {**_environment(root), **(extra or {})}
    subprocess.run(command, cwd=cwd or root, env=env, check=True)


def stage(*arguments: str) -> Callable[[Path], None]:
    """A library stage, as `knowledgestore <arguments>` at the store root."""

    def run(root: Path) -> None:
        _run(root, [sys.executable, "-m", "knowledgestore", *arguments])

    run.__name__ = arguments[0]
    return run


def _clones(root: Path) -> list[str]:
    lines = (root / "config/repositories.txt").read_text("utf-8").splitlines()
    return [line.split("|", 1)[0] for line in lines if line and not line.startswith("#")]


def extract(root: Path) -> None:
    """`graphify update .` from inside each clone, never from the store root.

    A clone holding no code gets no graph: graphify exits 1 with "No code files
    found", and the documented shell loop carries on, leaving `merge-inputs` to
    name the repository as declared but not extracted. That is expected of the
    clones in `UNEXTRACTED` and of no other; either surprise refuses."""
    for name in _clones(root):
        clone = root / "repositories" / name
        command = [sys.executable, "-m", "graphify", "update", "."]
        print(f"$ ({name}) {' '.join(command)}", flush=True)
        done = subprocess.run(command, cwd=clone, env=_environment(root))
        extracted = done.returncode == 0 and (clone / GRAPH).is_file()
        if extracted == (name in UNEXTRACTED):
            raise Refused(
                f"{name}: graphify exit {done.returncode}, graph "
                f"{'written' if (clone / GRAPH).is_file() else 'absent'}; "
                f"expected {'none' if name in UNEXTRACTED else 'a graph'}"
            )


def merge_graphs(root: Path) -> None:
    graphs = sorted(
        str(path.relative_to(root)) for path in root.glob("repositories/*/" + str(GRAPH))
    )
    _run(root, [sys.executable, "-m", "graphify", "merge-graphs", *graphs, "--out", str(GRAPH)])


def detect(root: Path) -> None:
    """The detect result, written as the docs write it: graphify's own scan at
    the store root, with `gitignore=False`."""
    (root / "graphify-out").mkdir(exist_ok=True)
    script = (
        "import json; from pathlib import Path; from graphify.detect import detect; "
        "print(json.dumps(detect(Path('.'), gitignore=False)))"
    )
    with (root / "graphify-out/.graphify_detect.json").open("w", encoding="utf-8") as out:
        subprocess.run(
            [sys.executable, "-c", script], cwd=root, env=_environment(root), stdout=out, check=True
        )


def fetch_tickets(root: Path) -> None:
    """The real `fetch-tickets`, through its `opener=` seam."""
    script = [sys.executable, "-c", FETCH_TICKETS, str(TRACKER_RESPONSES), TRACKER_TODAY]
    _run(root, script, extra=TRACKER_SETTINGS)


def minimum_standard_settings(root: Path) -> None:
    """The real `minimum-standard-settings`, through its `runner` seam."""
    _run(root, [sys.executable, "-c", MINIMUM_STANDARD_SETTINGS, str(GITHUB_RESPONSES)])


def json_schema(root: Path) -> None:
    unresolved = root.parent / "unresolved.json"
    _run(
        root,
        [sys.executable, "-m", "knowledgestore", "json-schema", "--unresolved", str(unresolved)],
    )


def cluster(root: Path) -> None:
    """Re-cluster the merged graph through graphify's API, as the build skill's
    "Clustering" section drives it, writing `community` and `community_name`
    onto every node. There is no previous clustering, so nothing to remap.
    The links are written back as read, as graphify's own re-cluster does, so
    a second edge between two nodes is not collapsed."""
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise Refused("PYTHONHASHSEED must be 0 before clustering (the build skill pins it)")
    # graphify is installed in the frozen environment only, never in this
    # repository's own, which is where the type checker runs.
    from graphify.cluster import cluster as partition  # pyright: ignore[reportMissingImports]
    from graphify.cluster import label_communities_by_hub  # pyright: ignore[reportMissingImports]
    from graphify.export import to_json  # pyright: ignore[reportMissingImports]
    from graphify.paths import load_node_link_graph  # pyright: ignore[reportMissingImports]

    path = root / GRAPH
    raw = json.loads(path.read_text("utf-8"))
    graph = load_node_link_graph(raw)
    communities = partition(graph)
    labels = label_communities_by_hub(graph, communities)
    written = to_json(
        graph,
        communities,
        str(path),
        community_labels=labels,
        built_at_commit=raw.get("built_at_commit"),
        original_links=raw.get("links") or raw.get("edges") or [],
    )
    if not written:
        raise Refused("graphify refused to write the clustered graph")


def compress(root: Path) -> None:
    """`gzip -9 -n`, as the docs recompress after clustering."""
    with (root / GRAPH).with_suffix(".json.gz").open("wb") as out:
        subprocess.run(["gzip", "-9", "-n", "-c", str(root / GRAPH)], stdout=out, check=True)


def _clustered(nodes: list[dict]) -> list[dict]:
    """`nodes`, once every one is known to carry an int community."""
    unclustered = [node["id"] for node in nodes if not isinstance(node.get("community"), int)]
    if not nodes or unclustered:
        raise Refused(f"{len(unclustered)} of {len(nodes)} nodes have no community")
    return nodes


def _community(nodes: list[dict], node_id: str) -> int:
    found = [node["community"] for node in nodes if node["id"] == node_id]
    if len(found) != 1:
        raise Refused(f"anchor {node_id!r} matched {len(found)} nodes")
    return found[0]


def labels(root: Path) -> None:
    """The authored community labels, keyed by the community holding each anchor."""
    nodes = _clustered(json.loads((root / GRAPH).read_text("utf-8"))["nodes"])
    named: dict[int, str] = {}
    for node_id, text in ANCHORS:
        community = _community(nodes, node_id)
        if community in named:
            raise Refused(f"anchors {named[community]!r} and {text!r} share a community")
        named[community] = text
    every = sorted({node["community"] for node in nodes})
    body = {str(c): named.get(c, f"Other {c}") for c in every}
    (root / LABELS).write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")


def summaries_merge(root: Path) -> None:
    """The authored summaries, keyed by the community holding each anchor, merged
    by the library's `summaries merge` as an authoring agent's `out` file is."""
    authored = root.parent / AUTHORED
    by_anchor = json.loads((authored / SUMMARIES_BY_ANCHOR).read_text("utf-8"))
    nodes = _clustered(json.loads((root / GRAPH).read_text("utf-8"))["nodes"])
    keyed: dict[str, str] = {}
    for node_id, text in by_anchor.items():
        community = str(_community(nodes, node_id))
        if community in keyed:
            raise Refused(f"two authored summaries for the community holding {node_id!r}")
        keyed[community] = text
    out = authored / SUMMARIES_OUT
    out.write_text(json.dumps(keyed, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    _run(root, [sys.executable, "-m", "knowledgestore", "summaries", "merge", str(out)])


def answers_layer(root: Path) -> None:
    """The store's own repository, then `answers-layer`, which dates each answer by
    its `date:` or else its last commit. Only the answer with no `date:` is
    committed, so the other is dated by its front matter and has no commit."""
    git = {**STORE_IDENTITY, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    _run(root, ["git", "init", "-q", "-b", "main"], extra=git)
    _run(root, ["git", "add", "--", COMMITTED_ANSWER], extra=git)
    _run(root, ["git", "commit", "-q", "-m", "Who approves refunds"], extra=git)
    _run(
        root,
        [
            sys.executable,
            "-m",
            "knowledgestore",
            "answers-layer",
            "--markdown",
            ANSWERS_DIR,
            "--out",
            "knowledge/answers.json",
        ],
    )


def verify(root: Path, index: bool = True) -> None:
    """Refuse a store missing an expected layer or a community on any node, or,
    built without its index, one that has an index."""
    manifest = json.loads((root / MANIFEST).read_text("utf-8"))
    layers = manifest.get("layers", {})
    extra = manifest.get("extra_layers", {})
    wanted = [name for name in EXPECTED if index or name != INDEX]
    missing = [name for name in wanted if not layers.get(name, {}).get("present")]
    missing += [name for name in EXPECTED_EXTRA if not extra.get(name, {}).get("present")]
    if missing:
        raise Refused("layers not present: " + ", ".join(missing))
    if not index and layers.get(INDEX, {}).get("present") is not False:
        raise Refused("built with --no-index, but the manifest does not record the index absent")
    with gzip.open(root / GRAPH.with_suffix(".json.gz"), "rt", encoding="utf-8") as stream:
        _clustered(json.load(stream)["nodes"])


# In order. Later layers go in before `manifest`.
STEPS: list[Callable[[Path], None]] = [
    stage("sync"),
    stage("export-history"),
    stage("context"),
    stage("intent"),
    # After `intent`, which discovers the tickets it asks about.
    fetch_tickets,
    extract,
    stage("merge-inputs", "--strict"),
    merge_graphs,
    detect,
    stage("content-set"),
    stage("gherkin"),
    # `packages` before `json-schema`, the build skill's order.
    stage("packages"),
    json_schema,
    stage("deployments"),
    stage("relationships"),
    # After `relationships` and before `index`, as the docs order them.
    minimum_standard_settings,
    stage("minimum-standard"),
    stage("source-urls"),
    cluster,
    # Prints a MISMATCH note against graph.json.gz: expected, because the
    # documented order re-gzips the clustered graph only after this records it.
    stage("record-clustering"),
    compress,
    labels,
    stage("summaries", "extract"),
    summaries_merge,
    stage("topics", "extract"),
    stage("topics", "merge"),
    stage("deepdive", "extract", "alpha-lib"),
    stage("deepdive", "merge"),
    answers_layer,
    stage("semantic"),
    stage("explorer"),
    stage("index"),
    stage("manifest"),
]


def main(argv: list[str]) -> int:
    if not argv or argv[1:] not in ([], ["--no-index"]):
        print("usage: pipeline.py <root> [--no-index]", file=sys.stderr)
        return 2
    root = Path(argv[0]).resolve()
    index = argv[1:] != ["--no-index"]
    try:
        for step in STEPS:
            if step.__name__ == INDEX and not index:
                print("$ (skipped: --no-index) knowledgestore index", flush=True)
                continue
            step(root)
        verify(root, index)
    except Refused as refused:
        print(f"pipeline refused: {refused}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as failed:
        print(f"pipeline stopped: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
