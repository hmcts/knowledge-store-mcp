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

from library_store.generate import OUT, REQUIREMENTS

MANIFEST = OUT / "knowledge/store-manifest.json"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text("utf-8"))


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

    def test_graph_layers_present(self):
        """Catches: a library stage that exits 0 and writes nothing, committed as
        a store missing a layer the tool tests then silently skip."""
        layers = _manifest()["layers"]
        # The same list as `EXPECTED` in library_store/pipeline.py, which refuses
        # to finish a build without these; keep the two in step.
        for name in (
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
        ):
            with self.subTest(layer=name):
                self.assertIs(layers[name]["present"], True)


if __name__ == "__main__":
    unittest.main()
