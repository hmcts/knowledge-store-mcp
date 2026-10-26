"""The contract with the library: the graph tools over a store knowledge-store-builder
wrote, read through the query index and through the graph read whole.

Every expected value is derived by hand from what `library_store/generate.py`
writes, before the store is read:

- Node ids are graphify 0.9.93's: the file's path without its extension, then
  the symbol, joined by `_` and lower-cased (`ids.make_id`,
  `extractors/base._file_stem`), so `RefundHandler` in `src/refund.ts` is
  `src_refund_refundhandler` and its method `handle` is
  `src_refund_refundhandler_handle`. `merge-graphs` prefixes each with the
  name of the directory holding the clone's `graphify-out/` (`build.
  prefix_graph_for_global`, `distinct_repo_tags`), the clone's name: `alpha-lib::`.
- graphify labels a class by its name, a function `refund()` and a method
  `.handle()`, and gives code nodes no `metadata.kind`, so their kind is "".
- JSON Schema nodes are the library's `json-schema` stage's:
  `<repo>::jsonschema:<path>#<JSON pointer>`, labelled by the schema's `title`
  or the definition's or property's name, at the line of its `{`.
- Lines are counted in the clone text; urls are `source_urls`' form,
  `https://github.com/example-org/<repo>/blob/<sha>/<path>#L<line>`, with the
  sha from `inputs.json`.
- Dates are the commits' fixed dates, as `git log --format=%cI` prints them
  (UTC as `Z`). `sources_as_of` is the newest, delta-deploy's 8 October.
- Labels and the summary are the hand-written inputs the generator keys by the
  community holding an anchor node. Community *numbers* are graphify's choice,
  so no test names one: each asks a node for its community and reads that.
"""

import unittest
from typing import Any

from knowledgestore_mcp import tools_graph
from library_store import harness
from library_store.generate import SUMMARY_REFUNDS
from library_store.harness import LIBRARY_LAYERS, SHAS

ALPHA = "https://github.com/example-org/alpha-lib/blob/" + SHAS["alpha-lib"]
BETA = "https://github.com/example-org/beta-app/blob/" + SHAS["beta-app"]

# 2026-10-08T09:00:00+00:00, delta-deploy's only commit, the newest in the estate.
SOURCES_AS_OF = "2026-10-08T09:00:00Z"

# alpha-lib's src/refund.ts: `refund` on line 1, `RefundHandler` on 5, `handle` on 6.
REFUND = {
    "id": "alpha-lib::src_refund_refund",
    "label": "refund()",
    "repo": "alpha-lib",
    "kind": "",
    "file": "src/refund.ts",
    "url": f"{ALPHA}/src/refund.ts#L1",
}
REFUND_HANDLER = {
    "id": "alpha-lib::src_refund_refundhandler",
    "label": "RefundHandler",
    "repo": "alpha-lib",
    "kind": "",
    "file": "src/refund.ts",
    "url": f"{ALPHA}/src/refund.ts#L5",
}
HANDLE = {
    "id": "alpha-lib::src_refund_refundhandler_handle",
    "label": ".handle()",
    "repo": "alpha-lib",
    "kind": "",
    "file": "src/refund.ts",
    "url": f"{ALPHA}/src/refund.ts#L6",
}
# beta-app's src/pay.ts on main: `PaymentService` on line 3.
PAYMENT_SERVICE = {
    "id": "beta-app::src_pay_paymentservice",
    "label": "PaymentService",
    "repo": "beta-app",
    "kind": "",
    "file": "src/pay.ts",
    "url": f"{BETA}/src/pay.ts#L3",
}

# alpha-lib's schemas/refund.schema.json: the root `{` on line 1, `money` on 7,
# `requestedOn` on 15, `total` on 16.
SCHEMA_FILE = "schemas/refund.schema.json"
SCHEMA_ID = f"alpha-lib::jsonschema:{SCHEMA_FILE}#"
SCHEMA = {
    "id": SCHEMA_ID,
    "label": "Refund",
    "repo": "alpha-lib",
    "kind": "schema",
    "file": SCHEMA_FILE,
    "url": f"{ALPHA}/{SCHEMA_FILE}#L1",
}
MONEY = {
    "id": f"{SCHEMA_ID}/$defs/money",
    "label": "money",
    "repo": "alpha-lib",
    "kind": "definition",
    "file": SCHEMA_FILE,
    "url": f"{ALPHA}/{SCHEMA_FILE}#L7",
}
REQUESTED_ON = {
    "id": f"{SCHEMA_ID}/properties/requestedOn",
    "label": "requestedOn",
    "repo": "alpha-lib",
    "kind": "field",
    "file": SCHEMA_FILE,
    "url": f"{ALPHA}/{SCHEMA_FILE}#L15",
}
TOTAL = {
    "id": f"{SCHEMA_ID}/properties/total",
    "label": "total",
    "repo": "alpha-lib",
    "kind": "field",
    "file": SCHEMA_FILE,
    "url": f"{ALPHA}/{SCHEMA_FILE}#L16",
}


def known(name: str) -> dict:
    """A repository the graph holds nodes for."""
    return {"answer": "in the estate", "exact": name, "matched_case_insensitively": False}


class GraphContract(harness.Contract):
    def _store(self, layers_used: list[str]) -> dict[str, Any]:
        """The envelope every answer carries in this mode."""
        return {
            "id": "library-contract-store",
            "format": {"major": 1, "minor": 4},
            "library_version": "0.19.0",
            "sources_as_of": SOURCES_AS_OF,
            "index": "present" if self.INDEXED else "absent: graph read whole",
            "layers_used": layers_used,
            "notice": None,
        }

    def _searched(self, *layers: str) -> str:
        return f"searched 3 repositories in the {' and '.join(layers)} layer" + (
            "s" if len(layers) > 1 else ""
        )

    def _without_community(self, found: dict | None) -> dict:
        """`found` less its community, which is graphify's number: checked to be one."""
        assert found is not None, "no node"
        found = dict(found)
        self.assertIsInstance(found.pop("community"), int)
        return found

    def _community_of(self, node_id: str) -> int:
        found = tools_graph.node(self.session, node_id)["node"]
        assert found is not None, f"no node {node_id}"
        return found["community"]

    # --- store_info -------------------------------------------------------------

    def test_store_info_reports_the_library_store(self):
        """Catches: a manifest field read from the wrong key or re-formatted, a
        library layer reported unread as if a newer minor had added it, the index
        schema not read from the library's `meta` table, provenance shas or dates
        mis-read, or `stale_days` not counted from `sources_as_of`."""
        info = tools_graph.store_info(self.session)
        layers = {name: {"present": True} for name in LIBRARY_LAYERS}
        if self.INDEXED:
            index = {"schema": "1.0", "fallback": None}
        else:
            layers["index"] = {"present": False}
            index = {
                "schema": None,
                "fallback": "no index in the store (knowledge/index/store.sqlite.gz or store.sqlite)",
            }
        self.assertEqual(
            info,
            {
                "store": self._store(["provenance"]),
                "stale_days": 18,
                "index": index,
                "layers": layers,
                "extra_layers": {
                    "answers": {"present": True, "kind": "answers"},
                    "ownership_notes": {"present": True, "kind": "answers"},
                },
                "unread": [],
                "flags": {
                    "graph.source_urls": True,
                    "intent.reach": True,
                    "deployments.present": True,
                },
                "items": [
                    {
                        "repository": "alpha-lib",
                        "sha": SHAS["alpha-lib"],
                        "committed": "2026-10-03T09:00:00Z",
                    },
                    {
                        "repository": "beta-app",
                        "sha": SHAS["beta-app"],
                        "committed": "2026-10-06T09:00:00Z",
                    },
                    {
                        "repository": "delta-deploy",
                        "sha": SHAS["delta-deploy"],
                        "committed": "2026-10-08T09:00:00Z",
                    },
                ],
                "total": 3,
                "page": 1,
                "next_page": None,
            },
        )

    # --- find_repo --------------------------------------------------------------

    def test_find_repo_knows_each_clone(self):
        """Catches: a clone the library graphed, or one holding only deployment
        values (delta-deploy), answered as not in the estate or as holding no
        graph nodes, because the index's or the graph's repositories are read
        from the wrong field."""
        for name in ("alpha-lib", "beta-app", "delta-deploy"):
            with self.subTest(name=name):
                found = tools_graph.find_repo(self.session, name)
                self.assertEqual(found, {"store": self._store([self.reader]), **known(name)})

    def test_find_repo_answers_a_target_outside_the_estate(self):
        """Catches: the registry's `unresolved_targets_outside_estate` not read,
        so a package beta-app depends on and no clone holds is answered as
        unknown, or an unknown name given a near miss it does not resemble."""
        layers = [self.reader, "relationships"]
        searched = self._searched(*sorted(layers))
        self.assertEqual(
            tools_graph.find_repo(self.session, "gamma-service"),
            {
                "store": self._store(sorted(layers)),
                "answer": "referenced by the estate but not held",
                "exact": None,
                "target": "gamma-service",
                "referenced_by": ["beta-app"],
                "searched": searched,
            },
        )
        self.assertEqual(
            tools_graph.find_repo(self.session, "zeta-unknown"),
            {
                "store": self._store(sorted(layers)),
                "answer": "not in the estate",
                "exact": None,
                "near_misses": [],
                "searched": searched,
            },
        )

    # --- search -------------------------------------------------------------------

    def test_search_finds_a_class_with_its_file_and_link(self):
        """Catches: a node's repository, file or url read from the wrong field of
        the library's graph or index, or a link not pinned to the built sha."""
        found = tools_graph.search(self.session, "RefundHandler")
        self.assertEqual(found["store"], self._store([self.reader]))
        self.assertEqual(found["matches"], 1)
        self.assertEqual(
            [self._without_community(item) for item in found["items"]], [REFUND_HANDLER]
        )

    def test_search_scoped_to_a_repository(self):
        """Catches: `repo=` not matched against the repository merge-graphs
        recorded, so a scoped search finds another repository's nodes, or none."""
        found = tools_graph.search(self.session, "PaymentService", repo="beta-app")
        self.assertEqual(found["repository"], known("beta-app"))
        self.assertEqual(
            [self._without_community(item) for item in found["items"]], [PAYMENT_SERVICE]
        )
        other = tools_graph.search(self.session, "RefundHandler", repo="beta-app")
        self.assertEqual((other["matches"], other["items"]), (0, []))
        self.assertEqual(other["searched"], self._searched(self.reader))
        alpha = tools_graph.search(self.session, "RefundHandler", repo="alpha-lib")
        self.assertEqual([item["id"] for item in alpha["items"]], [REFUND_HANDLER["id"]])

    # --- node ---------------------------------------------------------------------

    def test_node_by_its_merged_id(self):
        """Catches: an id looked up without merge-graphs' `<repo>::` prefix, or a
        node's fields read from the wrong keys; and an unknown id answered as
        anything but null with what was searched."""
        found = tools_graph.node(self.session, REFUND_HANDLER["id"])
        self.assertEqual(found["store"], self._store([self.reader]))
        self.assertEqual(self._without_community(found["node"]), REFUND_HANDLER)

        missing = tools_graph.node(self.session, "alpha-lib::src_refund_no_such_node")
        self.assertEqual(
            missing,
            {
                "store": self._store([self.reader]),
                "node": None,
                "searched": self._searched(self.reader),
            },
        )

    # --- neighbours ---------------------------------------------------------------

    def test_neighbours_follow_a_calls_edge(self):
        """Catches: edges read from the wrong key, or their direction flipped, so
        the method that calls `refund` is not shown calling it."""
        found = tools_graph.neighbours(self.session, HANDLE["id"], relation="calls")
        self.assertEqual(self._without_community(found["node"]), HANDLE)
        items = found["items"]
        self.assertEqual(
            [(item["depth"], item["via"]) for item in items],
            [(1, [{"from": HANDLE["id"], "relation": "calls", "direction": "out"}])],
        )
        self.assertEqual(self._without_community(items[0]["node"]), REFUND)

    # --- community ----------------------------------------------------------------

    def test_community_has_its_label_members_and_summary(self):
        """Catches: labels or summaries keyed differently from the graph's
        community ids, so the community holding `refund` loses its authored label
        or summary, or its members are read from the wrong field; or a summary
        dated other than by `sources_as_of`."""
        refunds = self._community_of(REFUND["id"])
        found = tools_graph.community(self.session, refunds)
        self.assertEqual(
            found["community"],
            {
                "id": refunds,
                "label": "Refunds",
                "summary": SUMMARY_REFUNDS,
                "summary_as_of": SOURCES_AS_OF,
            },
        )
        members = [item["id"] for item in found["items"]]
        self.assertIn(REFUND_HANDLER["id"], members)
        self.assertIn(REFUND["id"], members)
        self.assertEqual(
            found["store"]["layers_used"], sorted([self.reader, "labels", "summaries"])
        )

    def test_a_community_without_a_summary(self):
        """Catches: a community with no authored summary given another's, or a
        date for a summary it does not have."""
        payments = self._community_of(PAYMENT_SERVICE["id"])
        self.assertNotEqual(payments, self._community_of(REFUND["id"]))
        found = tools_graph.community(self.session, payments)
        self.assertEqual(
            found["community"],
            {"id": payments, "label": "Payments", "summary": None, "summary_as_of": None},
        )
        self.assertIn(PAYMENT_SERVICE["id"], [item["id"] for item in found["items"]])

    # --- the json-schema layer ------------------------------------------------------

    def test_kind_filters_to_the_json_schema_kinds(self):
        """Catches: `metadata.kind` not read, so `kind=` cannot find the json-schema
        stage's schema, definition and field nodes - or finds graphify's code
        nodes, which carry no kind."""
        for text, kind, expected in (
            ("Refund", "schema", SCHEMA),
            ("money", "definition", MONEY),
            ("requestedOn", "field", REQUESTED_ON),
        ):
            with self.subTest(kind=kind):
                found = tools_graph.search(self.session, text, kind=kind)
                self.assertEqual(
                    [self._without_community(item) for item in found["items"]], [expected]
                )

    def test_a_schema_contains_its_definition_and_fields(self):
        """Catches: the json-schema stage's `contains` and `references` edges not
        read, so a schema shows none of its parts, or a field does not lead to
        the definition its `$ref` names."""
        contains = tools_graph.neighbours(self.session, SCHEMA_ID, relation="contains")
        self.assertEqual(self._without_community(contains["node"]), SCHEMA)
        via = [{"from": SCHEMA_ID, "relation": "contains", "direction": "out"}]
        self.assertEqual(
            [
                (item["depth"], self._without_community(item["node"]), item["via"])
                for item in contains["items"]
            ],
            [(1, MONEY, via), (1, REQUESTED_ON, via), (1, TOTAL, via)],
        )
        references = tools_graph.neighbours(self.session, TOTAL["id"], relation="references")
        self.assertEqual(
            [(item["node"]["id"], item["via"]) for item in references["items"]],
            [(MONEY["id"], [{"from": TOTAL["id"], "relation": "references", "direction": "out"}])],
        )

    def test_no_graphify_keyword_node_remains_for_the_schema(self):
        """Catches, after a graphify upgrade and regeneration: graphify's own nodes
        for a recognised schema file - its JSON keywords, or the file itself -
        left beside the json-schema layer's. At 0.9.93 graphify emits no node for
        a schema document (`json_config._is_config_json`), so today this pins
        that state."""
        for text, expected in (
            ("$defs", []),
            ("properties", []),
            ("type", []),
            ("schema", []),
            # The file's own name: only the json-schema stage's schema node.
            ("refund", [SCHEMA_ID]),
        ):
            with self.subTest(text=text):
                found = tools_graph.search(self.session, text, repo="alpha-lib", limit=100)
                from_schema = [item["id"] for item in found["items"] if item["file"] == SCHEMA_FILE]
                self.assertEqual(from_schema, expected)


GraphWithIndex, GraphWithoutIndex = harness.both_modes(GraphContract)


if __name__ == "__main__":
    unittest.main()
