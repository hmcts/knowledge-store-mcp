"""The contract with the library: the dependency tools over a store that
knowledge-store-builder's own `relationships` and `manifest` stages wrote.

`library_store/store/` is the library's output, committed, and
`library_store/generate.py` is how it was made and remakes it. The synthetic
fixture describes the layout as this server understands it; this store is the
layout as the library writes it, so a disagreement between the two fails here.
Every expected value is derived by hand from the two clones `generate.py` writes:
beta-app's `package.json` depends on `@example/alpha-lib` at `^1.2.0`, which
alpha-lib publishes.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from knowledgestore_mcp import results, store, tools_graph, tools_relationships, tools_tickets
from library_store.generate import EXTRA_LAYER, OUT, VERSION

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

# beta-app -> alpha-lib, as each end's by-repo file names the other end.
NPM_EDGE = {
    "chart": "",
    "examples": [{"line": '"@example/alpha-lib": "^1.2.0"', "url": None}],
    "kinds": {"npm_package": 1},
    "references": 1,
    "refs_pinned": {"^1.2.0": 1},
    "refs_unpinned": 0,
}


def known(name: str) -> dict:
    return {
        "answer": "in the estate",
        "exact": name,
        "matched_case_insensitively": False,
        "graph_nodes": 0,
        "cloned_at": None,
        "note": "in the estate, but the graph holds no nodes for it",
    }


class LibraryStoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = Path(tmp.name).resolve() / "cache"
        patcher = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session = store.Session(lambda: store.open_store(OUT, cache), lambda: NOW)
        self.addCleanup(lambda: self.session.current()[0].close())

    def test_the_store_is_the_library_release_and_layout_it_claims(self):
        """Catches: the committed store regenerated from another release without the
        record moving, or a library that stops naming the layer by its directory -
        either way the tests below would no longer describe what they say."""
        manifest = json.loads((OUT / "knowledge/store-manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["library_version"], VERSION)
        relationships = manifest["layers"]["relationships"]
        self.assertEqual(relationships["paths"], ["knowledge/relationships/"])
        self.assertIs(relationships["present"], True)

    def test_dependents_and_dependencies_return_the_library_edge(self):
        """Catches: the per-repository directory not found in the library's layout,
        so alpha-lib's consumer is answered as none - an empty list "in the
        estate" - and beta-app as depending on nothing."""
        found = tools_relationships.dependents(self.session, "alpha-lib")
        self.assertEqual(found["repository"], known("alpha-lib"))
        self.assertEqual(found["items"], [{**NPM_EDGE, "repository": "beta-app"}])
        self.assertEqual(found["total"], 1)

        depends = tools_relationships.dependencies(self.session, "beta-app")
        self.assertEqual(depends["items"], [{**NPM_EDGE, "repository": "alpha-lib"}])
        self.assertEqual(depends["totals"], {"depends_on": 1, "outside_estate": 0})

        walk = tools_relationships.dependents(self.session, "alpha-lib", transitive=True)
        self.assertEqual(
            [(item["repository"], item["path"]) for item in walk["items"]],
            [("beta-app", ["alpha-lib", "beta-app"])],
        )
        answer = tools_relationships.repo_answer(self.session, "beta-app")
        self.assertEqual(answer["totals"], {"depends_on": 1, "depended_on_by": 0})

    def test_only_by_repo_files_are_repositories(self):
        """Catches: every `.json` in the layer directory read as a repository, so
        `kinds`, `repository-dependencies` and the store's own extra layer join the
        estate and inflate every `searched` count."""
        loaded = self.session.current()[0]
        self.assertEqual(sorted(results.by_repo_files(loaded)), ["alpha-lib", "beta-app"])
        for name in ("kinds", "repository-dependencies", Path(EXTRA_LAYER).stem):
            with self.subTest(name=name):
                found = tools_graph.find_repo(self.session, name)
                self.assertEqual(found["answer"], "not in the estate")
        unknown = tools_relationships.dependents(self.session, "gamma-service")
        self.assertTrue(unknown["searched"].startswith("searched 2 repositories "))

    def test_an_extra_layer_in_the_layer_directory_is_read_as_its_own_layer(self):
        """Catches: the store's extra layer lost to the relationships layer - not
        served by `answers` - when it sits in the same directory."""
        found = tools_tickets.answers(self.session, "owns", 5)
        self.assertEqual(
            [(item["layer"], item["id"], item["summary"]) for item in found["items"]],
            [("ownership_notes", "who-owns-alpha-lib", "Who owns alpha-lib?")],
        )


if __name__ == "__main__":
    unittest.main()
