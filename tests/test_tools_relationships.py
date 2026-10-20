"""The dependency tools (spec, Tools): repo_answer, dependents and dependencies.

Every expected value is a literal from DESIGN.md 5, derived by hand from the
fixture's declarations. Each store is the synthetic store, built for real and
opened through the real index (or, with `index="none"`, the real graph fallback);
the only stand-ins are the clock and the cache directory.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from fixture_store.build import BY_REPO, MANIFEST, build
from knowledgestore_mcp import results, store, tools_relationships
from knowledgestore_mcp.tools_graph import ArgumentRefused

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

# DESIGN.md 5.3.
TEST_CAP = 4096

SHA_DIGIT = {
    "accounts-web": "1",
    "audit-service": "2",
    "gateway-service": "4",
    "ledger-service": "6",
    "notifications-service": "7",
    "payments-api": "9",
    "reporting-worker": "a",
}

INDEXED = "present"
FALLBACK = "absent: graph read whole"


def url(repo: str, path: str, line: int) -> str:
    return f"https://github.com/example-org/{repo}/blob/{SHA_DIGIT[repo] * 40}/{path}#L{line}"


def edge(other: str, kind: str, ref: str, path: str, examples: list, chart: str = "") -> dict:
    """An edge as a by-repo file holds it, named by its other end. `examples` are
    (line number, line text) pairs, from DESIGN.md 5.1's declarations; each line is
    one reference, as every edge built here has three lines or fewer."""
    count = len(examples)
    return {
        "repository": other,
        "chart": chart,
        "references": count,
        "kinds": {kind: count},
        "refs_pinned": {ref: count},
        "examples": [{"url": url(other, path, n), "line": text} for n, text in examples],
    }


# DESIGN.md 5.2: each edge of the chain and the cycle, written out by hand.
PAYMENTS_ON_LEDGER = edge(
    "payments-api", "maven_artifact", "1.0.0", "pom.xml",
    [(44, "      <artifactId>ledger-client</artifactId>")],
)  # fmt: skip
WORKER_ON_LEDGER = edge(
    "reporting-worker", "maven_artifact", "1.0.0", "pom.xml",
    [(44, "      <artifactId>ledger-client</artifactId>")],
)  # fmt: skip
GATEWAY_ON_PAYMENTS = edge(
    "gateway-service", "npm_package", "^1.2.0", "package.json",
    [(12, '    "@example/payments-client": "^1.2.0",')],
)  # fmt: skip
AUDIT_ON_WORKER = edge(
    "audit-service", "maven_artifact", "0.9.0", "pom.xml",
    [(41, "      <artifactId>reporting-api</artifactId>")],
)  # fmt: skip
ACCOUNTS_ON_GATEWAY = edge(
    "accounts-web", "npm_package", "^2.0.0", "package.json",
    [(14, '    "@example/gateway-client": "^2.0.0",')],
)  # fmt: skip

TF = "infra/main.tf"


def tf_line(module: str, ref: str) -> str:
    pin = f"?ref={ref}" if ref else ""
    return (
        f'  source = "git::https://github.com/example-org/infra-modules.git//modules/{module}{pin}"'
    )


def tf_edge(other: str, ref: str, lines: tuple[int, ...]) -> dict:
    """A Terraform edge on infra-modules: every line is a reference, the first three
    are examples, and the modules cycle network, database, app-service."""
    modules = ("network", "database", "app-service")
    return {
        "repository": other,
        "chart": "",
        "references": len(lines),
        "kinds": {"terraform_module": len(lines)},
        "refs_pinned": {ref: len(lines)},
        "examples": [
            {"url": url(other, TF, n), "line": tf_line(modules[i], ref)}
            for i, n in enumerate(lines[:3])
        ],
    }


# DESIGN.md 5.1: the pins on infra-modules.
ACCOUNTS_ON_INFRA = tf_edge("accounts-web", "", (3, 12))
LEDGER_ON_INFRA = tf_edge("ledger-service", "main", (3, 12))


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


UNKNOWN = {
    "answer": "not in the estate",
    "exact": None,
    "near_misses": [
        {"repository": "payment-apis", "near_miss": True},
        {"repository": "payments-api", "near_miss": True},
    ],
}
NOT_HELD = {
    "answer": "referenced by the estate but not held",
    "exact": None,
    "target": "legacy-network",
    "referenced_by": ["ledger-service", "payments-api"],
}
# DESIGN.md 5.4: the targets `legacy-network` names, per kind.
CHART_OUTSIDE = {
    "kind": "helm_chart",
    "target": "legacy-network",
    "references": 1,
    "referenced_by": ["payments-api"],
}
MODULE_OUTSIDE = {
    "kind": "terraform_module",
    "target": "example-org/legacy-network",
    "references": 3,
    "referenced_by": ["ledger-service", "payments-api"],
}


def known(name: str) -> dict:
    return {"answer": "in the estate", "exact": name, "matched_case_insensitively": False}


SEARCHED_BOTH = "searched 12 repositories in the index and relationships layers"
SEARCHED_ABSENT = "searched 12 repositories in the index layer; the relationships layer is absent"


class RelationshipsTestCase(unittest.TestCase):
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


class DependentsTest(RelationshipsTestCase):
    def test_direct_dependents_are_the_by_repo_file_whole(self):
        """Catches: dependents read from the wrong list (`depends_on`), an edge
        rebuilt rather than returned as the store wrote it, or a list that does not
        say it is complete (DESIGN.md 5.2)."""
        self.assertEqual(
            tools_relationships.dependents(self.session(), "ledger-service"),
            {
                **envelope(INDEXED, ["index", "relationships"]),
                "repository": known("ledger-service"),
                "items": [PAYMENTS_ON_LEDGER, WORKER_ON_LEDGER],
                "total": 2,
                "page": 1,
                "next_page": None,
            },
        )

    def test_ref_prefix_not_keeps_other_pins_and_floating_refs(self):
        """Catches: a floating ref (`""`) dropped because it has no pin, a branch
        pin dropped, or a `v4.` pin kept (DESIGN.md 5.1: keeps accounts-web and
        ledger-service, drops the five that pin v4.)."""
        found = tools_relationships.dependents(
            self.session(), "infra-modules", ref_prefix_not="v4."
        )
        self.assertEqual(found["items"], [ACCOUNTS_ON_INFRA, LEDGER_ON_INFRA])
        self.assertEqual(found["total"], 2)

    def test_an_empty_prefix_keeps_only_the_floating_consumer(self):
        """Catches: a floating ref treated as a pin that starts with the prefix. Every
        pin starts with `""`, so only accounts-web, which floats, is kept: the rule
        that a floating ref always counts as other is only visible here."""
        found = tools_relationships.dependents(self.session(), "infra-modules", ref_prefix_not="")
        self.assertEqual(found["items"], [ACCOUNTS_ON_INFRA])

    def test_kind_filters_the_edges(self):
        """Catches: `kind` ignored, or matched against something other than the
        edge's `kinds`. chart-web's dependents are both helm_chart edges; none is
        a maven_artifact edge, and that empty answer says what was searched."""
        session = self.session()
        charts = tools_relationships.dependents(session, "chart-web", kind="helm_chart")
        self.assertEqual(
            [(e["repository"], e["chart"]) for e in charts["items"]],
            [("accounts-web", "web"), ("notifications-service", "web")],
        )
        none = tools_relationships.dependents(session, "chart-web", kind="maven_artifact")
        self.assertEqual(none["items"], [])
        self.assertEqual(none["searched"], SEARCHED_BOTH)

    def test_an_unlisted_kind_is_refused_not_answered_empty(self):
        """Catches: a misspelt kind answered as "in the estate, zero results", which
        reads as "nothing depends on it"."""
        with self.assertRaises(ArgumentRefused) as raised:
            tools_relationships.dependents(self.session(), "chart-web", kind="helm")
        self.assertIn("helm_chart", str(raised.exception))

    def test_the_three_hop_chain_with_paths_and_the_cycle_once(self):
        """Catches: a walk that stops short of three hops, loses a path, follows the
        cycle audit-service -> reporting-worker, or reports it twice (DESIGN.md
        5.2)."""
        L, P, W, G, A = (
            "ledger-service",
            "payments-api",
            "reporting-worker",
            "gateway-service",
            "audit-service",
        )
        self.assertEqual(
            tools_relationships.dependents(self.session(), L, transitive=True),
            {
                **envelope(INDEXED, ["index", "relationships"]),
                "repository": known(L),
                "max_depth": 4,
                "cycles": [[L, W, A, W]],
                "beyond_max_depth": [],
                "items": [
                    {"repository": P, "depth": 1, "path": [L, P], "edges": [PAYMENTS_ON_LEDGER]},
                    {"repository": W, "depth": 1, "path": [L, W], "edges": [WORKER_ON_LEDGER]},
                    {"repository": A, "depth": 2, "path": [L, W, A], "edges": [AUDIT_ON_WORKER]},
                    {
                        "repository": G,
                        "depth": 2,
                        "path": [L, P, G],
                        "edges": [GATEWAY_ON_PAYMENTS],
                    },
                    {
                        "repository": "accounts-web",
                        "depth": 3,
                        "path": [L, P, G, "accounts-web"],
                        "edges": [ACCOUNTS_ON_GATEWAY],
                    },
                ],
                "total": 5,
                "page": 1,
                "next_page": None,
            },
        )

    def test_a_case_only_name_reads_the_repository_it_matched(self):
        """Catches: the file looked up by the name as asked rather than the name the
        lookup matched, so `Ledger-Service` - an exact match ignoring case (DESIGN.md
        2) - finds no file and answers "zero dependents"."""
        found = tools_relationships.dependents(self.session(), "Ledger-Service")
        self.assertTrue(found["repository"]["matched_case_insensitively"])
        self.assertEqual(found["items"], [PAYMENTS_ON_LEDGER, WORKER_ON_LEDGER])

    def test_a_second_chart_from_the_same_dependent_joins_its_edges(self):
        """Catches: a walk that keeps only the first edge from a predecessor, so a
        repository depending on a target through two charts shows one. The fixture
        has one chart per pair, so a second, `api`, is added to chart-web's file."""
        session = self.session()
        path = session.current()[0].root / BY_REPO / "chart-web.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        second = {**document["depended_on_by"][0], "chart": "api"}
        document["depended_on_by"].insert(0, second)
        path.write_text(json.dumps(document), encoding="utf-8")
        found = tools_relationships.dependents(session, "chart-web", transitive=True)
        accounts = found["items"][0]
        self.assertEqual(accounts["repository"], "accounts-web")
        self.assertEqual([e["chart"] for e in accounts["edges"]], ["api", "web"])
        self.assertEqual(found["total"], 2)

    def test_a_repository_reached_twice_is_listed_once(self):
        """Catches: a walk that lists a repository again when a second branch reaches
        it. infra-modules' seven dependents are all at depth 1, and each one's own
        dependents are among them (audit-service -> reporting-worker, payments-api
        -> gateway-service, ...): nothing is new at depth 2, and no path repeats a
        repository, so there is no cycle (DESIGN.md 5.1, 5.2)."""
        found = tools_relationships.dependents(self.session(), "infra-modules", transitive=True)
        self.assertEqual(
            [(i["repository"], i["depth"]) for i in found["items"]],
            [
                ("accounts-web", 1),
                ("audit-service", 1),
                ("gateway-service", 1),
                ("ledger-service", 1),
                ("notifications-service", 1),
                ("payments-api", 1),
                ("reporting-worker", 1),
            ],
        )
        self.assertEqual((found["cycles"], found["beyond_max_depth"]), ([], []))

    def test_a_shallower_walk_says_what_it_did_not_walk(self):
        """Catches: a depth limit that silently truncates. At max_depth 2 the walk
        stops before accounts-web, and says gateway-service has dependents it did
        not walk; the cycle, met at the boundary, is still reported."""
        found = tools_relationships.dependents(
            self.session(), "ledger-service", transitive=True, max_depth=2
        )
        self.assertEqual(
            [(i["repository"], i["depth"]) for i in found["items"]],
            [("payments-api", 1), ("reporting-worker", 1), ("audit-service", 2), ("gateway-service", 2)],
        )  # fmt: skip
        self.assertEqual(found["beyond_max_depth"], ["gateway-service"])
        self.assertEqual(
            found["cycles"],
            [["ledger-service", "reporting-worker", "audit-service", "reporting-worker"]],
        )

    def test_depth_and_filter_arguments_are_refused_where_they_cannot_apply(self):
        """Catches: a max_depth above 4 walked anyway (spec: max_depth <= 4), or a
        depth or pin filter silently ignored where it has no meaning."""
        session = self.session()
        refused: list[dict[str, Any]] = [
            {"transitive": True, "max_depth": 5},
            {"transitive": True, "max_depth": 0},
            {"transitive": True, "max_depth": True},
            {"max_depth": 2},
            {"transitive": True, "ref_prefix_not": "v4."},
        ]
        for arguments in refused:
            with self.subTest(**arguments), self.assertRaises(ArgumentRefused):
                tools_relationships.dependents(session, "ledger-service", **arguments)

    def test_a_target_outside_the_estate_is_reported_per_kind(self):
        """Catches: a not-held target answered "unknown" or as an empty list; its
        entries merged across kinds; the store's own organisation's repository
        missed, or another organisation's taken for it (DESIGN.md 5.4)."""
        session = self.session()
        self.assertEqual(
            tools_relationships.dependents(session, "legacy-network"),
            {
                **envelope(INDEXED, ["index", "relationships"]),
                "repository": NOT_HELD,
                "items": [CHART_OUTSIDE, MODULE_OUTSIDE],
                "total": 2,
                "page": 1,
                "next_page": None,
            },
        )
        charts = tools_relationships.dependents(session, "legacy-network", kind="helm_chart")
        self.assertEqual(charts["items"], [CHART_OUTSIDE])

    def test_a_filter_that_cannot_apply_to_a_not_held_target_says_so(self):
        """Catches: a pin filter or a walk silently skipped for a target outside the
        estate, so the unfiltered referrers read as the filtered answer."""
        session = self.session()
        pinned = tools_relationships.dependents(session, "legacy-network", ref_prefix_not="v4.")
        self.assertEqual(pinned["items"], [CHART_OUTSIDE, MODULE_OUTSIDE])
        self.assertEqual(
            pinned["not_applied"], {"ref_prefix_not": "targets outside the estate record no pins"}
        )
        walked = tools_relationships.dependents(session, "legacy-network", transitive=True)
        self.assertEqual(walked["items"], [CHART_OUTSIDE, MODULE_OUTSIDE])
        self.assertEqual(list(walked["not_applied"]), ["transitive"])


class UnknownNameTest(RelationshipsTestCase):
    def test_every_tool_answers_an_unknown_name_with_near_misses(self):
        """Catches: a bare empty list for a name the estate does not hold, or a near
        miss returned as the answer - in either mode (spec, Unknown names)."""
        for index, label, base in (("gz", INDEXED, "index"), ("none", FALLBACK, "graph")):
            session = self.session(index)
            searched = f"searched 12 repositories in the {base} and relationships layers"
            empty = {"items": [], "total": 0, "page": 1, "next_page": None}
            for tool, more in (
                (tools_relationships.dependents, {}),
                (tools_relationships.dependencies, {"outside_estate": []}),
            ):
                with self.subTest(index=index, tool=tool.__name__):
                    self.assertEqual(
                        tool(session, "payment-api"),
                        {
                            **envelope(label, [base, "relationships"]),
                            "repository": UNKNOWN,
                            "searched": searched,
                            **more,
                            **empty,
                        },
                    )
            with self.subTest(index=index, tool="repo_answer"):
                self.assertEqual(
                    tools_relationships.repo_answer(session, "payment-api"),
                    {
                        **envelope(label, [base, "relationships"]),
                        "repository": UNKNOWN,
                        "relationships": None,
                        "searched": searched,
                        "items": [],
                        "total": 0,
                    },
                )

    def test_a_repository_with_nothing_to_say_is_in_the_estate_with_zero(self):
        """Catches: payment-apis, whose lists are both empty, answered as unknown or
        as a bare empty list (DESIGN.md 2)."""
        session = self.session()
        for tool in (tools_relationships.dependents, tools_relationships.dependencies):
            with self.subTest(tool=tool.__name__):
                found = tool(session, "payment-apis")
                self.assertEqual(found["repository"], known("payment-apis"))
                self.assertEqual((found["items"], found["total"]), ([], 0))
                self.assertEqual(found["searched"], SEARCHED_BOTH)

    def test_a_not_held_name_is_said_to_be_not_held_by_every_tool(self):
        """Catches: dependencies or repo_answer calling a target the estate
        references "not in the estate" (DESIGN.md 5.4)."""
        session = self.session()
        listing = tools_relationships.dependencies(session, "legacy-network")
        self.assertEqual(listing["repository"], NOT_HELD)
        self.assertEqual((listing["items"], listing["searched"]), ([], SEARCHED_BOTH))
        answer = tools_relationships.repo_answer(session, "legacy-network")
        self.assertEqual(answer["repository"], NOT_HELD)
        self.assertIsNone(answer["relationships"])

    def test_a_repository_with_only_a_by_repo_file_is_in_the_estate(self):
        """Catches: a cloned repository that has no graph node and no ticket - only
        its `by-repo/` file - answered "not in the estate", in either mode
        (DESIGN.md 5.6). The estate is then thirteen."""
        for index, label, base in (("gz", INDEXED, "index"), ("none", FALLBACK, "graph")):
            with self.subTest(index=index):
                session = self.session(index, by_repo_only=True)
                self.assertEqual(
                    tools_relationships.dependencies(session, "docs-site"),
                    {
                        **envelope(label, [base, "relationships"]),
                        "repository": known("docs-site"),
                        "outside_estate": [],
                        "searched": f"searched 13 repositories in the {base} and relationships layers",
                        "items": [],
                        "total": 0,
                        "page": 1,
                        "next_page": None,
                    },
                )

    def test_a_by_repo_name_is_not_counted_when_the_layer_is_absent(self):
        """Catches: by-repo names read although the manifest says the relationships
        layer is absent, so an absent layer still grows the estate."""

        def edit(document):
            document["layers"]["relationships"]["present"] = False

        session = self.session(by_repo_only=True, edit=edit)
        found = tools_relationships.dependencies(session, "docs-site")
        self.assertEqual(found["repository"]["answer"], "not in the estate")


class AbsentLayerTest(RelationshipsTestCase):
    def test_every_tool_names_the_absent_layer(self):
        """Catches: an absent relationships layer answered as "nothing depends on
        it" - an empty list that does not say the layer is missing (plan, Task 7)."""

        def edit(document):
            document["layers"]["relationships"]["present"] = False

        session = self.session(edit=edit)
        for tool in (
            tools_relationships.dependents,
            tools_relationships.dependencies,
            tools_relationships.repo_answer,
        ):
            with self.subTest(tool=tool.__name__):
                found = tool(session, "ledger-service")
                self.assertEqual(found["store"]["layers_used"], ["index"])
                self.assertEqual((found["items"], found["total"]), ([], 0))
                self.assertEqual(found["searched"], SEARCHED_ABSENT)
                self.assertEqual(found["repository"], known("ledger-service"))


class DependenciesTest(RelationshipsTestCase):
    def test_dependencies_are_the_depends_on_list(self):
        """Catches: dependencies read from `depended_on_by`, or a kind filter that
        does not apply. payments-api depends on infra-modules (terraform_module),
        ledger-service and shared-lib-java (maven_artifact) - DESIGN.md 5.1."""
        session = self.session()
        found = tools_relationships.dependencies(session, "payments-api")
        self.assertEqual(
            [e["repository"] for e in found["items"]],
            ["infra-modules", "ledger-service", "shared-lib-java"],
        )
        self.assertEqual(found["total"], 3)
        self.assertEqual(
            found["items"][1],
            {
                "repository": "ledger-service",
                "chart": "",
                "references": 1,
                "kinds": {"maven_artifact": 1},
                "refs_pinned": {"1.0.0": 1},
                "examples": [
                    {
                        "url": url("payments-api", "pom.xml", 44),
                        "line": "      <artifactId>ledger-client</artifactId>",
                    }
                ],
            },
        )
        maven = tools_relationships.dependencies(session, "payments-api", kind="maven_artifact")
        self.assertEqual(
            [e["repository"] for e in maven["items"]], ["ledger-service", "shared-lib-java"]
        )

    def test_dependencies_list_the_targets_outside_the_estate(self):
        """Catches: a dependency the store does not hold left out, so a repository's
        list reads as complete without it (spec: targets outside the estate are
        listed). payments-api references the chart `legacy-network` and the estate's
        own `example-org/legacy-network`; `other-org/legacy-network` is audit-service's,
        not payments-api's, and must not appear here although it shares the name
        (DESIGN.md 5.4)."""
        session = self.session()
        payments = tools_relationships.dependencies(session, "payments-api")
        self.assertEqual(payments["outside_estate"], [CHART_OUTSIDE, MODULE_OUTSIDE])
        audit = tools_relationships.dependencies(session, "audit-service")
        self.assertEqual(
            audit["outside_estate"],
            [
                {
                    "kind": "terraform_module",
                    "target": "other-org/legacy-network",
                    "references": 1,
                    "referenced_by": ["audit-service"],
                }
            ],
        )
        modules = tools_relationships.dependencies(session, "payments-api", kind="terraform_module")
        self.assertEqual(modules["outside_estate"], [MODULE_OUTSIDE])
        self.assertEqual(
            tools_relationships.dependencies(session, "ledger-service", kind="helm_chart")[
                "outside_estate"
            ],
            [],
        )


class RepoAnswerTest(RelationshipsTestCase):
    def _file(self, session: store.Session, repo: str) -> bytes:
        return (session.current()[0].root / BY_REPO / f"{repo}.json").read_bytes()

    def test_a_file_under_the_cap_is_returned_whole(self):
        """Catches: a file that fits paged anyway, or returned altered. Under the test
        cap, payments-api's file comes back as the store wrote it (DESIGN.md 5.3)."""
        session = self.session()
        found = tools_relationships.repo_answer(session, "payments-api", cap_bytes=TEST_CAP)
        self.assertEqual(found["relationships"], json.loads(self._file(session, "payments-api")))
        self.assertEqual(found["totals"], {"depends_on": 3, "depended_on_by": 1})
        self.assertNotIn("next_page", found)
        self.assertLessEqual(len(results.encode(found)), TEST_CAP)

    def test_the_oversize_file_pages_and_reassembles_to_its_exact_bytes(self):
        """Catches: a file over the cap cut, an edge lost or repeated between pages,
        or a page over the cap. infra-modules' file is over 4096 bytes and each of
        its seven edges is under it (DESIGN.md 5.3): the pages, put back together,
        are the file's bytes as the writer serialises them."""
        session = self.session()
        original = self._file(session, "infra-modules")
        self.assertGreater(len(original), TEST_CAP)
        pages, cursor = [], None
        while True:
            found = tools_relationships.repo_answer(
                session, "infra-modules", cursor, cap_bytes=TEST_CAP
            )
            self.assertLessEqual(len(results.encode(found)), TEST_CAP)
            self.assertNotIn("oversize", found)
            pages.append(found)
            cursor = found["next_page"]
            if cursor is None:
                break
        self.assertGreater(len(pages), 1)
        self.assertEqual({p["total"] for p in pages}, {7})
        self.assertEqual(pages[0]["totals"], {"depends_on": 0, "depended_on_by": 7})
        items = [item for p in pages for item in p["items"]]
        rebuilt = dict(pages[0]["relationships"])
        for name in ("depends_on", "depended_on_by"):
            listed = [i for i in items if i["list"] == name]
            self.assertEqual([i["position"] for i in listed], list(range(len(listed))))
            rebuilt[name] = [i["edge"] for i in listed]
        serialised = json.dumps(rebuilt, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        self.assertEqual(serialised.encode("utf-8"), original)

    def test_a_cursor_from_another_listing_is_refused(self):
        """Catches: a page cursor followed into a listing it was not issued for."""
        session = self.session()
        cursor = tools_relationships.repo_answer(session, "infra-modules", cap_bytes=TEST_CAP)[
            "next_page"
        ]
        self.assertIsNotNone(cursor)
        with self.assertRaises(results.CursorRefused):
            tools_relationships.dependents(session, "infra-modules", cursor=cursor)


if __name__ == "__main__":
    unittest.main()
