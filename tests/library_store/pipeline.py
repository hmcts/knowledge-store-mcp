"""The library's stages over a store root, in the order its docs give.

    python pipeline.py <root> [--no-index]

Runs inside the frozen environment `generate.py` builds from `requirements.txt`,
so `knowledgestore` and `graphify` here are the pinned releases; `generate.py`
writes the inputs and calls this. It follows `docs/creating-a-store.md` ("Build
the store" onwards) and the build skill's clustering procedure at the pinned
tag: each library stage runs as `python -m knowledgestore <stage>`, and graphify
runs from inside each clone, then merges, then the merged graph is re-clustered
through graphify's Python API (never `graphify cluster-only`).

The one hand-written thing it writes is the community labels file, which in a
real build an agent authors: each label is attached to the community holding a
named anchor node, the way an author names a community by what is in it.

Exits 0 only when every layer in `EXPECTED` is present in the manifest and every
node in the committed graph carries a community; otherwise names what is
missing and exits 1, so `generate.py` commits nothing.
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

# Layers the manifest must mark present, in the `layers` table. Kept in step with
# `test_graph_layers_present` in tests/test_library_generation.py, which asserts
# the same list over the committed store.
EXPECTED = (
    "graph",
    "labels",
    "provenance",
    "history",
    "context",
    "repository_manifest",
    "content_files",
    "intent",
    "ticket_descriptions",
    "relationships",
    "deployments",
)

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
    return {
        **os.environ,
        **_home(root),
        "KSB_ROOT": str(root),
        "KSB_GITHUB_ORG": ORG,
        "KSB_STORE_ID": STORE_ID,
        "KSB_MIN_COMMUNITY_SIZE": "1",
        "KSB_DEPLOY_REPOS": "delta-deploy",
    }


def _run(root: Path, command: list[str], cwd: Path | None = None) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd or root, env=_environment(root), check=True)


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


def verify(root: Path) -> None:
    """Refuse a store missing an expected layer or a community on any node."""
    manifest = json.loads((root / MANIFEST).read_text("utf-8"))
    layers = manifest.get("layers", {})
    missing = [name for name in EXPECTED if not layers.get(name, {}).get("present")]
    if missing:
        raise Refused("layers not present: " + ", ".join(missing))
    with gzip.open(root / GRAPH.with_suffix(".json.gz"), "rt", encoding="utf-8") as stream:
        _clustered(json.load(stream)["nodes"])


# In order. Later layers go in before `manifest`.
STEPS: list[Callable[[Path], None]] = [
    stage("sync"),
    stage("export-history"),
    stage("context"),
    stage("intent"),
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
    stage("source-urls"),
    cluster,
    # Prints a MISMATCH note against graph.json.gz: expected, because the
    # documented order re-gzips the clustered graph only after this records it.
    stage("record-clustering"),
    compress,
    labels,
    stage("manifest"),
]


def main(argv: list[str]) -> int:
    if not argv or argv[1:] not in ([], ["--no-index"]):
        print("usage: pipeline.py <root> [--no-index]", file=sys.stderr)
        return 2
    root = Path(argv[0]).resolve()
    try:
        for step in STEPS:
            step(root)
        verify(root)
    except Refused as refused:
        print(f"pipeline refused: {refused}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as failed:
        print(f"pipeline stopped: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
