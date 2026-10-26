"""The generated store is what it claims to be: the pinned library release, a
clustered graph, and every layer the generator promises.

These read `library_store/store/` as an artefact, not through the server: they
check the generation itself, so a store regenerated wrongly fails here before
any tool test can describe the wrong thing. Expected values are literals from
the generator's inputs and the frozen environment it runs in.
"""

import gzip
import json
import re
import unittest

from pathlib import Path

from library_store.generate import NO_INDEX, OUT, REQUIREMENTS

MANIFEST = "knowledge/store-manifest.json"

# Every layer v0.19.0's manifest names: its 18 `LAYERS` and `deployments`.
LIBRARY_LAYERS = (
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


def _manifest(store: Path = OUT) -> dict:
    return json.loads((store / MANIFEST).read_text("utf-8"))


class LibraryGenerationTest(unittest.TestCase):
    def test_manifest_is_the_pinned_release_and_format(self):
        """Catches: the store regenerated from another library release, or at
        another manifest format, without the record moving - every contract test
        would then describe a release nobody pinned."""
        manifest = _manifest()
        self.assertEqual(manifest["library_version"], "0.19.0")
        self.assertEqual(manifest["format"], {"major": 1, "minor": 4})

    def test_every_node_has_a_community(self):
        """Catches: a re-clustering that did not persist (the documented
        `cluster-only` failure), leaving nodes with no community, so `community`
        and every summary keyed by community id read a partial graph."""
        with gzip.open(OUT / "graphify-out/graph.json.gz", "rt", encoding="utf-8") as stream:
            nodes = json.load(stream)["nodes"]
        self.assertTrue(nodes)
        missing = [node["id"] for node in nodes if not isinstance(node.get("community"), int)]
        self.assertEqual(missing, [])

    def test_the_partitioner_is_recorded(self):
        """Catches: a store clustered in an environment whose partitioner differs
        from the one the frozen requirements resolve - the communities would not
        reproduce, and nothing would say why."""
        header = REQUIREMENTS.read_text("utf-8")
        named = re.search(r"^# partitioner: (\w+)$", header, re.MULTILINE)
        assert named is not None, "requirements.txt names no partitioner"
        record = json.loads((OUT / "graphify-out/clustering-inputs.json").read_text("utf-8"))
        self.assertEqual(record["partitioner"], named.group(1))
        self.assertEqual(named.group(1), "louvain")

    def test_every_library_layer_is_present(self):
        """Catches: a library stage that exits 0 and writes nothing (fetch-tickets
        with no tracker settings, a merge that skipped every entry), committed as
        a store missing a layer the tool tests then silently skip; and a manifest
        that stops naming a layer at all."""
        manifest = _manifest()
        layers = manifest["layers"]
        # The same names as `EXPECTED` in library_store/pipeline.py, which refuses
        # to finish a build without them: v0.19.0's 18 layers plus `deployments`.
        # Keep the two in step.
        self.assertEqual(sorted(layers), sorted(LIBRARY_LAYERS))
        for name in LIBRARY_LAYERS:
            with self.subTest(layer=name):
                self.assertIs(layers[name]["present"], True)
        for name in ("ownership_notes", "answers"):
            with self.subTest(extra_layer=name):
                self.assertIs(manifest["extra_layers"][name]["present"], True)

    def test_no_index_store_is_the_same_store_without_its_index(self):
        """Catches: the no-index store built from other inputs, or with another
        stage skipped, so the graph-fallback tests compare against a store that
        differs from `store/` in more than the index."""
        full, bare = _manifest(), _manifest(NO_INDEX)
        self.assertIs(full["layers"]["index"]["present"], True)
        self.assertIs(bare["layers"]["index"]["present"], False)
        self.assertEqual(sorted(bare["layers"]), sorted(full["layers"]))
        for name, layer in full["layers"].items():
            if name == "index":
                continue
            with self.subTest(layer=name):
                self.assertIs(bare["layers"][name]["present"], layer["present"])
        self.assertEqual(
            {name: layer["present"] for name, layer in bare["extra_layers"].items()},
            {name: layer["present"] for name, layer in full["extra_layers"].items()},
        )


if __name__ == "__main__":
    unittest.main()
