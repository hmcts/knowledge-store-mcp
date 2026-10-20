"""The graph tools (spec, Tools): store_info, find_repo, search, node, neighbours and
community.

Every expected value is a literal from DESIGN.md, derived by hand from the fixture's
design. Each store is the synthetic store, built for real and opened through the
real index (or, with `index="none"`, through the real graph fallback); the only
stand-ins are the clock and the cache directory.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from fixture_store.build import MANIFEST, build
from knowledgestore_mcp import results, store, tools_graph
from knowledgestore_mcp.tools_graph import ArgumentRefused

# DESIGN.md 9.2: at this instant the store is 4 whole days old.
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

# DESIGN.md 1: each repository's sha is one hex digit repeated forty times.
SHA_DIGIT = {
    "accounts-web": "1",
    "audit-service": "2",
    "chart-web": "3",
    "gateway-service": "4",
    "infra-modules": "5",
    "ledger-service": "6",
    "notifications-service": "7",
    "payment-apis": "8",
    "payments-api": "9",
    "reporting-worker": "a",
    "shared-lib-java": "b",
    "ui-components": "c",
}

PAY = "src/main/java/com/example/payments/"
LEDGER = "src/main/java/com/example/ledger/"
LIB = "src/main/java/com/example/lib/"


def node(repo: str, local: str, label: str, path: str, line: int, community: int, kind=""):
    """A node as every tool returns it, from DESIGN.md 3.1's literal fields."""
    return {
        "id": f"{repo}::{local}",
        "label": label,
        "repo": repo,
        "kind": kind,
        "file": path,
        "url": f"https://github.com/example-org/{repo}/blob/{SHA_DIGIT[repo] * 40}/{path}#L{line}",
        "community": community,
    }


REFUND_HANDLER = node(
    "payments-api", "RefundHandler", "RefundHandler", PAY + "RefundHandler.java", 9, 0
)
PAYMENT_SERVICE = node(
    "payments-api", "PaymentService", "PaymentService", PAY + "PaymentService.java", 14, 0
)
STRUCTURAL = {
    "id": "ledger-service::com.example.ledger",
    "label": None,
    "repo": "ledger-service",
    "kind": "",
    "file": None,
    "url": None,
    "community": None,
}  # fmt: skip

# DESIGN.md 3.2: PaymentService's neighbours at depth 1, by id.
DEPTH_1 = [
    {
        "depth": 1,
        "node": node("ledger-service", "LedgerClient", "LedgerClient", LEDGER + "LedgerClient.java", 7, 0),
        "via": [{"from": "payments-api::PaymentService", "relation": "calls", "direction": "out"}],
    },
    {
        "depth": 1,
        "node": node("payments-api", "PaymentRepository", "PaymentRepository", PAY + "PaymentRepository.java", 6, 0),
        "via": [{"from": "payments-api::PaymentService", "relation": "calls", "direction": "out"}],
    },
    {
        "depth": 1,
        "node": node("payments-api", "PaymentsController", "PaymentsController", PAY + "PaymentsController.java", 20, 0),
        "via": [{"from": "payments-api::PaymentService", "relation": "calls", "direction": "in"}],
    },
    {
        # The 2-cycle: reached both ways, listed once.
        "depth": 1,
        "node": REFUND_HANDLER,
        "via": [
            {"from": "payments-api::PaymentService", "relation": "calls", "direction": "in"},
            {"from": "payments-api::PaymentService", "relation": "calls", "direction": "out"},
        ],
    },
    {
        "depth": 1,
        "node": node("shared-lib-java", "IdempotencyKey", "IdempotencyKey", LIB + "IdempotencyKey.java", 5, 1),
        "via": [{"from": "payments-api::PaymentService", "relation": "imports", "direction": "out"}],
    },
    {
        "depth": 1,
        "node": node("shared-lib-java", "MoneyAmount", "MoneyAmount", LIB + "MoneyAmount.java", 8, 1),
        "via": [{"from": "payments-api::PaymentService", "relation": "imports", "direction": "out"}],
    },
]  # fmt: skip

# The second hop, derived by hand from DESIGN.md 3.2's edges and `build.LINKS`.
DEPTH_2 = [
    {
        "depth": 2,
        "node": node("gateway-service", "PaymentsProxy", "PaymentsProxy", "src/payments-proxy.ts", 10, 0),
        "via": [{"from": "payments-api::PaymentsController", "relation": "calls", "direction": "in"}],
    },
    {
        "depth": 2,
        "node": node("ledger-service", "JournalEntry", "JournalEntry", LEDGER + "JournalEntry.java", 5, 0),
        "via": [{"from": "ledger-service::LedgerClient", "relation": "imports", "direction": "out"}],
    },
    {
        "depth": 2,
        "node": STRUCTURAL,
        "via": [{"from": "ledger-service::LedgerClient", "relation": "contains", "direction": "in"}],
    },
    {
        "depth": 2,
        "node": node("payment-apis", "doc:refunds-runbook", "Refunds runbook", "docs/runbooks/refunds.md", 1, 0),
        "via": [{"from": "payments-api::RefundHandler", "relation": "references", "direction": "in"}],
    },
    {
        "depth": 2,
        "node": node("reporting-worker", "LedgerExtract", "LedgerExtract", "src/main/java/com/example/reporting/LedgerExtract.java", 22, 1),
        "via": [{"from": "ledger-service::LedgerClient", "relation": "calls", "direction": "in"}],
    },
    {
        "depth": 2,
        "node": node("shared-lib-java", "Currency", "Currency", LIB + "Currency.java", 3, 1),
        "via": [{"from": "shared-lib-java::MoneyAmount", "relation": "imports", "direction": "out"}],
    },
]  # fmt: skip

# DESIGN.md 3.3: community 0's 19 members, by id; the structural node is in none.
COMMUNITY_0_IDS = [
    "accounts-web::AccountPage",
    "accounts-web::PaymentForm",
    "accounts-web::feature:pay",
    "accounts-web::scenario:pay-saved-card",
    "gateway-service::AuthFilter",
    "gateway-service::GatewayRouter",
    "gateway-service::PaymentsProxy",
    "gateway-service::RateLimiter",
    "ledger-service::JournalEntry",
    "ledger-service::LedgerClient",
    "ledger-service::LedgerController",
    "ledger-service::LedgerRepository",
    "payment-apis::doc:refunds-runbook",
    "payment-apis::spec:payments",
    "payments-api::PaymentRepository",
    "payments-api::PaymentService",
    "payments-api::PaymentsController",
    "payments-api::RefundHandler",
    "payments-api::infra:main",
]

COMMUNITY_0 = {
    "id": 0,
    "label": "Payments and the ledger",
    "summary": (
        "Card payments enter through the gateway, are taken by payments-api and "
        "posted to the ledger as journal entries; refunds follow the same path back."
    ),
}

SEARCHED_INDEX = "searched 12 repositories in the index layer"
SEARCHED_GRAPH = "searched 12 repositories in the graph layer"


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.cache = self.tmp / "cache"
        patcher = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def session(self, index: str = "gz", edit=None, **options) -> store.Session:
        root = build(
            self.tmp / f"s-{index}-{len(list(self.tmp.iterdir()))}", index=index, **options
        )
        if edit is not None:
            path = root / MANIFEST
            document = json.loads(path.read_text(encoding="utf-8"))
            edit(document)
            path.write_text(json.dumps(document), encoding="utf-8")
        session = store.Session(lambda: store.open_store(root, self.cache), lambda: NOW)
        self.addCleanup(lambda: session.current()[0].close())
        return session

    def both(self) -> tuple[store.Session, store.Session]:
        return self.session("gz"), self.session("none")


def envelope(index: str, layers_used: list[str]) -> dict:
    return {
        "store": {
            "id": "example-store",
            "format": {"major": 1, "minor": 0},
            "library_version": "0.0.0+fixture",
            "sources_as_of": "2026-10-15T23:30:00-02:00",
            "index": index,
            "layers_used": layers_used,
            "notice": None,
        }
    }


INDEXED = "present"
FALLBACK = "absent: graph read whole"

# DESIGN.md 1, as provenance records it.
REPOSITORIES = [
    {"repository": "accounts-web", "sha": "1" * 40, "committed": "2026-10-14T09:12:00+01:00"},
    {"repository": "audit-service", "sha": "2" * 40, "committed": "2026-10-13T16:40:00+01:00"},
    {"repository": "chart-web", "sha": "3" * 40, "committed": "2026-09-30T11:00:00+01:00"},
    {"repository": "gateway-service", "sha": "4" * 40, "committed": "2026-10-15T08:05:00+01:00"},
    {"repository": "infra-modules", "sha": "5" * 40, "committed": "2026-10-02T14:20:00+01:00"},
    {"repository": "ledger-service", "sha": "6" * 40, "committed": "2026-10-16T01:00:00+00:00"},
    {"repository": "notifications-service", "sha": "7" * 40, "committed": "2026-10-10T10:10:00+01:00"},
    {"repository": "payment-apis", "sha": "8" * 40, "committed": "2026-08-21T12:00:00+01:00"},
    {"repository": "payments-api", "sha": "9" * 40, "committed": "2026-10-15T17:45:00+01:00"},
    {"repository": "reporting-worker", "sha": "a" * 40, "committed": "2026-10-15T23:30:00-02:00"},
    {"repository": "shared-lib-java", "sha": "b" * 40, "committed": "2026-09-12T09:00:00+01:00"},
    {"repository": "ui-components", "sha": "c" * 40, "committed": "2026-10-07T15:30:00+01:00"},
]  # fmt: skip


def layers(index_present: bool) -> dict:
    """DESIGN.md 9.4: each layer's `present`, as the manifest records it."""
    return {
        "graph": {"present": True},
        "labels": {"present": True},
        "provenance": {"present": True},
        "repository_manifest": {"present": False},
        "context": {"present": False},
        "history": {"present": False},
        "intent": {"present": True},
        "ticket_descriptions": {"present": True},
        "ticket_tracker": {"present": False},
        "summaries": {"present": True},
        "topics": {"present": True},
        "deep_dives": {"present": True},
        "semantic": {"present": False},
        "explorer": {"present": False},
        "relationships": {"present": True},
        "index": {"present": index_present},
        "deployments": {"present": None},
    }


class StoreInfoTest(ToolTestCase):
    def test_store_info_with_the_index(self):
        """Catches: a missing or wrong provenance row, `stale_days` not read from the
        injected clock (4 at 2026-10-20T12:00Z, DESIGN.md 9.2), a layer's state
        dropped or coerced, or a null flag reported as false (DESIGN.md 9.3)."""
        self.assertEqual(
            tools_graph.store_info(self.session()),
            {
                **envelope(INDEXED, ["provenance"]),
                "stale_days": 4,
                "index": {"schema": "1.0", "fallback": None},
                "repositories": REPOSITORIES,
                "layers": layers(index_present=True),
                "extra_layers": {"faq": {"present": True, "kind": "answers"}},
                "unread": [],
                "flags": {
                    "graph.source_urls": True,
                    "intent.reach": True,
                    "deployments.present": None,
                },
            },
        )

    def test_store_info_without_the_index_says_why_the_graph_answers(self):
        """Catches: a fallback store that looks indexed, or one that does not say why
        it fell back (spec, Start-up step 3: says so in store_info)."""
        self.assertEqual(
            tools_graph.store_info(self.session("none")),
            {
                **envelope(FALLBACK, ["provenance"]),
                "stale_days": 4,
                "index": {
                    "schema": None,
                    "fallback": (
                        "no index in the store (knowledge/index/store.sqlite.gz or store.sqlite)"
                    ),
                },
                "repositories": REPOSITORIES,
                "layers": layers(index_present=False),
                "extra_layers": {"faq": {"present": True, "kind": "answers"}},
                "unread": [],
                "flags": {
                    "graph.source_urls": True,
                    "intent.reach": True,
                    "deployments.present": None,
                },
            },
        )

    def test_a_newer_minor_lists_its_layer_as_present_but_unread(self):
        """Catches: a layer a newer library added that store_info drops, or lists
        without saying this server cannot read it (spec, The one rule)."""
        info = tools_graph.store_info(
            self.session(
                manifest_minor=1, extra={"newer": {"paths": [], "present": True, "digest": None}}
            )
        )
        self.assertEqual(info["store"]["format"], {"major": 1, "minor": 1})
        self.assertEqual(info["unread"], ["newer"])
        self.assertEqual(info["layers"]["newer"], {"present": True})

    def test_flags_are_reported_as_recorded_not_coerced(self):
        """Catches: a recorded `false` flag turned into null, or a missing one into
        false: false means measured and absent, null means unknown."""

        def edit(document):
            document["layers"]["graph"]["source_urls"] = False
            del document["layers"]["intent"]["reach"]
            document["sources_as_of"] = None

        info = tools_graph.store_info(self.session(edit=edit))
        self.assertEqual(
            info["flags"],
            {"graph.source_urls": False, "intent.reach": None, "deployments.present": None},
        )
        self.assertIsNone(info["stale_days"])


class FindRepoTest(ToolTestCase):
    def test_the_three_answers_in_both_modes(self):
        """Catches: find_repo substituting a near miss for a name the estate does not
        hold, missing the case-only match, or calling an out-of-estate target
        unknown (DESIGN.md 2, 5.4) - with the index or without it."""
        for session, index, base in ((self.session(), INDEXED, "index"), (self.session("none"), FALLBACK, "graph")):  # fmt: skip
            with self.subTest(index=index):
                self.assertEqual(
                    tools_graph.find_repo(session, "payment-api"),
                    {
                        **envelope(index, [base, "relationships"]),
                        "answer": "not in the estate",
                        "exact": None,
                        "near_misses": [
                            {"repository": "payment-apis", "near_miss": True},
                            {"repository": "payments-api", "near_miss": True},
                        ],
                        "searched": f"searched 12 repositories in the {base} and relationships layers",  # fmt: skip
                    },
                )
                self.assertEqual(
                    tools_graph.find_repo(session, "Ledger-Service"),
                    {
                        **envelope(index, [base]),
                        "answer": "in the estate",
                        "exact": "ledger-service",
                        "matched_case_insensitively": True,
                    },
                )
                self.assertEqual(
                    tools_graph.find_repo(session, "legacy-network"),
                    {
                        **envelope(index, [base, "relationships"]),
                        "answer": "referenced by the estate but not held",
                        "exact": None,
                        "target": "legacy-network",
                        "referenced_by": ["ledger-service", "payments-api"],
                        "searched": f"searched 12 repositories in the {base} and relationships layers",  # fmt: skip
                    },
                )


def body(response: dict) -> dict:
    """A response without what may differ between the indexed and fallback modes: the
    envelope, the sentence naming the layer searched, and the store-bound cursor."""
    return {k: v for k, v in response.items() if k not in ("store", "searched", "next_page")}


class SearchTest(ToolTestCase):
    def test_a_label_is_found_with_its_pinned_url(self):
        """Catches: a search that misses the one node DESIGN.md 4 says matches, or
        returns it without the pinned url (spec: corpus paths as citations)."""
        for session, index, base in ((self.session(), INDEXED, "index"), (self.session("none"), FALLBACK, "graph")):  # fmt: skip
            with self.subTest(index=index):
                self.assertEqual(
                    tools_graph.search(session, "RefundHandler"),
                    {
                        **envelope(index, [base]),
                        "matches": 1,
                        "items": [REFUND_HANDLER],
                        "total": 1,
                        "page": 1,
                        "next_page": None,
                    },
                )

    def test_a_search_ignores_case_in_both_modes(self):
        """Catches: a case-sensitive fallback, where `refundhandler` would miss the
        node the index (whose tokenizer folds case) finds."""
        for session in self.both():
            self.assertEqual(
                tools_graph.search(session, "refundhandler")["items"], [REFUND_HANDLER]
            )

    def test_the_index_ranks_by_bm25_then_id_and_the_fallback_by_id(self):
        """Catches: ignoring the rank, reversing it, or breaking a tie by anything
        but the id. The two-token labels outrank the three-token one, and tie with
        each other, so the id decides (database before network); the fallback
        orders by id alone, which puts app-service first."""
        app = node("infra-modules", "module:app-service", "app-service module", "modules/app-service/main.tf", 1, 1)  # fmt: skip
        database = node("infra-modules", "module:database", "database module", "modules/database/main.tf", 1, 1)  # fmt: skip
        network = node("infra-modules", "module:network", "network module", "modules/network/main.tf", 1, 1)  # fmt: skip
        indexed, fallback = self.both()
        self.assertEqual(tools_graph.search(indexed, "module")["items"], [database, network, app])
        self.assertEqual(tools_graph.search(fallback, "module")["items"], [app, database, network])

    def test_a_limit_cuts_the_ranking_and_says_how_many_matched(self):
        """Catches: a limit that silently hides matches (plan, No silent truncation):
        three labels match `module`, two are returned, and `matches` says three."""
        for session in self.both():
            response = tools_graph.search(session, "module", limit=2)
            self.assertEqual((response["matches"], response["total"]), (3, 2))
            self.assertEqual(len(response["items"]), 2)

    def test_a_kind_filter_keeps_only_that_kind(self):
        """Catches: the kind filter ignored, or applied to the wrong column
        (DESIGN.md 3.1: the scenario node is kind `scenario`)."""
        scenario = node("accounts-web", "scenario:pay-saved-card", "Pay with a saved card", "features/pay.feature", 6, 0, "scenario")  # fmt: skip
        for session in self.both():
            self.assertEqual(
                tools_graph.search(session, "Pay with", kind="scenario")["items"], [scenario]
            )
            empty = tools_graph.search(session, "Pay with", kind="feature")
            self.assertEqual((empty["items"], empty["total"]), ([], 0))
            self.assertIn("searched", empty)

    def test_a_repo_filter_goes_through_the_three_way_answer(self):
        """Catches: an unknown repo filter returning a bare empty list instead of
        "not in the estate" with its near misses (spec, Unknown names), or a
        case-only spelling filtering out every hit."""
        for session, base in ((self.session(), "index"), (self.session("none"), "graph")):
            with self.subTest(base=base):
                unknown = tools_graph.search(session, "module", repo="payment-api")
                self.assertEqual(
                    {k: unknown[k] for k in ("repository", "items", "total", "searched")},
                    {
                        "repository": {
                            "answer": "not in the estate",
                            "exact": None,
                            "near_misses": [
                                {"repository": "payment-apis", "near_miss": True},
                                {"repository": "payments-api", "near_miss": True},
                            ],
                        },
                        "items": [],
                        "total": 0,
                        "searched": f"searched 12 repositories in the {base} and relationships layers",  # fmt: skip
                    },
                )
                held = tools_graph.search(session, "module", repo="legacy-network")
                self.assertEqual(
                    held["repository"]["answer"], "referenced by the estate but not held"
                )
                self.assertEqual(held["items"], [])

                known = tools_graph.search(session, "module", repo="payments-api")
                self.assertEqual(
                    {k: known[k] for k in ("repository", "items", "total", "searched")},
                    {
                        "repository": {
                            "answer": "in the estate",
                            "exact": "payments-api",
                            "matched_case_insensitively": False,
                        },
                        "items": [],
                        "total": 0,
                        "searched": f"searched 12 repositories in the {base} layer",
                    },
                )

                folded = tools_graph.search(session, "module", repo="Infra-Modules")
                self.assertEqual(folded["repository"]["exact"], "infra-modules")
                self.assertTrue(folded["repository"]["matched_case_insensitively"])
                self.assertEqual(len(folded["items"]), 3)

    def test_fts_syntax_in_the_text_is_a_phrase_not_a_query(self):
        """Catches: caller text reaching FTS5 as query syntax, where an unbalanced
        quote or `NEAR(` errors and `OR`, a prefix `*` or a column filter widens
        the query. Each of these, unquoted, either raises or matches; as a phrase
        each matches no label."""
        session = self.session()
        for text in (
            '"',
            'Refund"Handler',
            "NEAR(",
            "NEAR(RefundHandler PaymentService)",
            "RefundHandler OR PaymentService",
            "Refund*",
            "*",
            "-",
            "label:RefundHandler",
            "{label}: RefundHandler",
        ):
            with self.subTest(text=text):
                response = tools_graph.search(session, text)
                self.assertEqual(
                    {k: response[k] for k in ("matches", "items", "total", "searched")},
                    {"matches": 0, "items": [], "total": 0, "searched": SEARCHED_INDEX},
                )

    def test_text_that_is_not_searchable_is_refused_with_the_reason(self):
        """Catches: an empty query matching every label in the fallback while matching
        none in the index, or a NUL or lone surrogate crashing the SQLite bind."""
        for session in self.both():
            for text in ("", "   ", "Refund\x00Handler", "Refund\ud800"):
                with self.subTest(text=text), self.assertRaises(ArgumentRefused):
                    tools_graph.search(session, text)
            for limit in (0, -1):
                with self.subTest(limit=limit), self.assertRaises(ArgumentRefused):
                    tools_graph.search(session, "module", limit=limit)

    def test_a_large_result_pages_under_the_cap(self):
        """Catches: a search response over the cap, or pages that drop or repeat a
        hit. The substring `e` matches most labels in the fallback."""
        session = self.session("none")
        whole = tools_graph.search(session, "e", limit=100)
        self.assertIsNone(whole["next_page"])
        pages = [tools_graph.search(session, "e", limit=100, cap_bytes=2048)]
        while pages[-1]["next_page"] is not None:
            cursor = pages[-1]["next_page"]
            pages.append(tools_graph.search(session, "e", limit=100, cursor=cursor, cap_bytes=2048))  # fmt: skip
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(results.encode(page)), 2048)
        self.assertEqual([i for p in pages for i in p["items"]], whole["items"])


class NodeTest(ToolTestCase):
    def test_a_node_carries_its_url(self):
        """Catches: a node returned without its pinned url or its community."""
        for session, index, base in ((self.session(), INDEXED, "index"), (self.session("none"), FALLBACK, "graph")):  # fmt: skip
            with self.subTest(index=index):
                self.assertEqual(
                    tools_graph.node(session, "payments-api::RefundHandler"),
                    {**envelope(index, [base]), "node": REFUND_HANDLER},
                )

    def test_a_structural_node_is_tolerated(self):
        """Catches: failing on a node with no label, no source file and no url
        (DESIGN.md 3.1; library CLAUDE.md 6.4)."""
        for session in self.both():
            self.assertEqual(
                tools_graph.node(session, "ledger-service::com.example.ledger")["node"],
                STRUCTURAL,
            )

    def test_an_unknown_id_is_null_and_says_what_was_searched(self):
        """Catches: an unknown id raising, or answering with nothing said, or an id
        shaped like SQL reaching the query."""
        for session, base in ((self.session(), "index"), (self.session("none"), "graph")):
            for missing in ("payments-api::Nope", "' OR 1=1 --"):
                response = tools_graph.node(session, missing)
                self.assertIsNone(response["node"])
                self.assertEqual(response["searched"], f"searched 12 repositories in the {base} layer")  # fmt: skip


class NeighboursTest(ToolTestCase):
    def test_depth_one_lists_each_neighbour_once_with_how_it_was_reached(self):
        """Catches: a neighbour reached both ways (the 2-cycle) listed twice, an
        incoming edge missed, or a direction reversed (DESIGN.md 3.2)."""
        for session, index, base in ((self.session(), INDEXED, "index"), (self.session("none"), FALLBACK, "graph")):  # fmt: skip
            with self.subTest(index=index):
                self.assertEqual(
                    tools_graph.neighbours(session, "payments-api::PaymentService"),
                    {
                        **envelope(index, [base]),
                        "node": PAYMENT_SERVICE,
                        "items": DEPTH_1,
                        "total": 6,
                        "page": 1,
                        "next_page": None,
                    },
                )

    def test_depth_two_visits_the_cycle_once_and_never_the_start(self):
        """Catches: a walk that loops on the 2-cycle, or returns the start node as
        its own neighbour (DESIGN.md 3.2)."""
        for session in self.both():
            response = tools_graph.neighbours(session, "payments-api::PaymentService", depth=2)
            self.assertEqual(response["items"], DEPTH_1 + DEPTH_2)
            self.assertEqual(response["total"], 12)

    def test_a_relation_filter_follows_only_that_relation(self):
        """Catches: the relation filter applied to the first hop only, or ignored.
        Following `calls` alone, the second hop reaches PaymentsProxy and
        LedgerExtract; unfiltered, it would also reach JournalEntry (imports), the
        structural node (contains) and the runbook (references)."""
        for session in self.both():
            response = tools_graph.neighbours(
                session, "payments-api::PaymentService", depth=2, relation="calls"
            )
            self.assertEqual(
                [(i["depth"], i["node"]["id"]) for i in response["items"]],
                [
                    (1, "ledger-service::LedgerClient"),
                    (1, "payments-api::PaymentRepository"),
                    (1, "payments-api::PaymentsController"),
                    (1, "payments-api::RefundHandler"),
                    (2, "gateway-service::PaymentsProxy"),
                    (2, "reporting-worker::LedgerExtract"),
                ],
            )

    def test_a_node_reached_at_depth_one_is_not_listed_again_at_depth_two(self):
        """Catches: a walk that forgets the first hop, so a depth-1 node reached again
        from another depth-1 node is listed twice. From JournalEntry, the
        structural node is a depth-1 neighbour and also contains LedgerClient and
        LedgerController, the other two."""
        for session in self.both():
            response = tools_graph.neighbours(session, "ledger-service::JournalEntry", depth=2)
            self.assertEqual(
                [(i["depth"], i["node"]["id"]) for i in response["items"]],
                [
                    (1, "ledger-service::LedgerClient"),
                    (1, "ledger-service::LedgerController"),
                    (1, "ledger-service::com.example.ledger"),
                    (2, "ledger-service::LedgerRepository"),
                    (2, "payments-api::PaymentService"),
                    (2, "reporting-worker::LedgerExtract"),
                ],
            )

    def test_depth_above_two_is_refused_with_the_reason(self):
        """Catches: a walk deeper than the spec's `depth<=2`, or a refusal that does
        not say why."""
        session = self.session()
        for depth in (3, 0, True):
            with self.subTest(depth=depth), self.assertRaises(ArgumentRefused) as raised:
                tools_graph.neighbours(session, "payments-api::PaymentService", depth=depth)
            self.assertIn("depth", str(raised.exception))
            self.assertIn("1 or 2", str(raised.exception))

    def test_an_unknown_start_or_relation_says_what_was_searched(self):
        """Catches: a bare empty list for an id or relation the store does not hold."""
        for session in self.both():
            missing = tools_graph.neighbours(session, "payments-api::Nope")
            self.assertEqual((missing["node"], missing["items"]), (None, []))
            self.assertIn("searched", missing)
            other = tools_graph.neighbours(session, "payments-api::PaymentService", relation="x")
            self.assertEqual((other["node"], other["items"]), (PAYMENT_SERVICE, []))
            self.assertIn("searched", other)


class CommunityTest(ToolTestCase):
    def test_a_community_carries_its_label_summary_and_members(self):
        """Catches: a community without its summary, a member without its url, the
        structural node counted as a member, or a member of the other community
        (DESIGN.md 3.3: 19 members)."""
        for session, index, base in ((self.session(), INDEXED, "index"), (self.session("none"), FALLBACK, "graph")):  # fmt: skip
            with self.subTest(index=index):
                response = tools_graph.community(session, 0)
                self.assertEqual(response["store"], envelope(index, [base, "labels", "summaries"])["store"])  # fmt: skip
                self.assertEqual(response["community"], COMMUNITY_0)
                self.assertEqual([m["id"] for m in response["items"]], COMMUNITY_0_IDS)
                self.assertEqual(response["total"], 19)
                self.assertIn(REFUND_HANDLER, response["items"])

    def test_an_unknown_community_is_null_and_says_what_was_searched(self):
        """Catches: inventing a community, or a bare empty list."""
        for session in self.both():
            response = tools_graph.community(session, 7)
            self.assertEqual((response["community"], response["items"]), (None, []))
            self.assertIn("searched", response)

    def test_members_page_under_the_cap_and_reassemble(self):
        """Catches: a community response over the cap, or pages that drop or repeat
        a member (DESIGN.md 3.3: community 1 has 21)."""
        session = self.session()
        pages = [tools_graph.community(session, 1, cap_bytes=4096)]
        while pages[-1]["next_page"] is not None:
            cursor = pages[-1]["next_page"]
            pages.append(tools_graph.community(session, 1, cursor=cursor, cap_bytes=4096))
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(results.encode(page)), 4096)
        members = [m["id"] for p in pages for m in p["items"]]
        self.assertEqual(len(members), 21)
        self.assertEqual(members, sorted(set(members)))


class BothModesTest(ToolTestCase):
    def test_every_tool_gives_the_same_items_with_or_without_the_index(self):
        """Catches: the fallback answering differently from the index in anything
        but the envelope and the layer it names (plan, Fallback without the index),
        or two calls on one store giving different bytes (plan, Deterministic)."""
        indexed, fallback = self.both()
        calls: list[tuple[str, Any]] = [
            ("find_repo payments-api", lambda s: tools_graph.find_repo(s, "payments-api")),
            ("find_repo payment-api", lambda s: tools_graph.find_repo(s, "payment-api")),
            ("search RefundHandler", lambda s: tools_graph.search(s, "RefundHandler")),
            ("search repo", lambda s: tools_graph.search(s, "Pay with", repo="accounts-web")),
            ("search none", lambda s: tools_graph.search(s, "nothing-matches")),
            ("node", lambda s: tools_graph.node(s, "payments-api::RefundHandler")),
            (
                "node structural",
                lambda s: tools_graph.node(s, "ledger-service::com.example.ledger"),
            ),  # fmt: skip
            ("neighbours 1", lambda s: tools_graph.neighbours(s, "payments-api::PaymentService")),
            (
                "neighbours 2",
                lambda s: tools_graph.neighbours(s, "ledger-service::LedgerClient", depth=2),
            ),  # fmt: skip
            ("community 0", lambda s: tools_graph.community(s, 0)),
            ("community 1", lambda s: tools_graph.community(s, 1)),
        ]
        for name, call in calls:
            with self.subTest(call=name):
                with_index, without = call(indexed), call(fallback)
                self.assertEqual(body(with_index), body(without))
                self.assertEqual(with_index["store"]["index"], INDEXED)
                self.assertEqual(without["store"]["index"], FALLBACK)
                self.assertEqual(results.encode(call(indexed)), results.encode(with_index))
                self.assertEqual(results.encode(call(fallback)), results.encode(without))

    def test_a_reload_notice_reaches_the_tool_result(self):
        """Catches: a tool that takes the store without the session's notice, so a
        reload goes unmentioned (spec, Freshness)."""
        session = self.session()

        def unreadable() -> None:
            root = session.current()[0].root
            (root / MANIFEST).write_text("{", encoding="utf-8")

        unreadable()
        response = tools_graph.node(session, "payments-api::RefundHandler")
        self.assertEqual(response["store"]["notice"], "reload deferred: manifest unreadable")
        self.assertEqual(response["node"], REFUND_HANDLER)


if __name__ == "__main__":
    unittest.main()
