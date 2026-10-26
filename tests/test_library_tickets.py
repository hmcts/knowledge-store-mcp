"""The contract with the library: the ticket and answer tools over a store
knowledge-store-builder wrote, read through the query index and, without it,
through the ticket layer (`intent`) and `ticket-descriptions`.

Every expected value is derived by hand from what `library_store/generate.py`
writes, before the store is read:

- Tickets are the keys in commit subjects (`build_intent_index`). DEMO-1 lands on
  alpha-lib's `package.json` and `src/refund.ts` (1 October) and beta-app's
  `package.json` and `src/pay.ts` (5 October); DEMO-2 on alpha-lib's schema
  (2 October). DEMO-4 is only on beta-app's branch `feature/DEMO-4-retry`
  (7 October, `src/pay.ts`), so it is `proposed`. Each link is one commit.
- A record's descriptions (`d`) are the subjects less their key, kept when at
  least 12 characters: "retry" is too short, so DEMO-4 has none. `d` ranks by
  count, then length, ties in history order (alpha-lib before beta-app); the
  subjects (`s`) by count, then text.
- The tracker's answers are `tracker_responses.json`'s, cached by
  `fetch_tickets` with `today` 2026-10-20: DEMO-1 fetched and resolved (on
  2026-10-05) so, being final, it carries no `checked` date; DEMO-2 not in the
  response, so recorded `absent`, checked 2026-10-20.
- Written answers are the authored Markdown, rendered by the library's Markdown
  subset (`build_topic_briefs.markdown_to_html`): `#` is `<h2>`, `##` is `<h3>`,
  and each name in `config/repositories.txt` becomes a link to
  `https://github.com/example-org/<name>` unless it follows a `/`. No ticket
  browse URL is set, so ticket keys stay text.
- `how-refunds-work` is dated by its `date:` and never committed; the store's
  own repository commits `who-approves-refunds` alone, as `pipeline.py` does,
  on 2026-10-19, which `answers-layer` writes with `+00:00` for UTC. Its sha was
  derived by making that commit from the same inputs - the file text,
  `Fixture Author <fixture@example.invalid>`, the date and the message "Who
  approves refunds" - with git configuration off, in a scratch repository.
- Topic briefs and deep dives record no date, so they are dated by
  `sources_as_of`: delta-deploy's 8 October commit, the newest in the estate.
"""

import unittest
from typing import Any

from knowledgestore_mcp import tools_tickets
from library_store import harness
from library_store.generate import EXTRA_LAYER

# 2026-10-08T09:00:00+00:00, delta-deploy's only commit, the newest in the estate.
SOURCES_AS_OF = "2026-10-08T09:00:00Z"

# The store's own commit of docs/answers/who-approves-refunds.md (see above).
WHO_APPROVES_COMMIT = "f6b689b013faa74e0a6cb4db8251a4e960316940"


def link(repo: str) -> str:
    """A repository name as the library's renderer links it."""
    return (
        f'<a href="https://github.com/example-org/{repo}" target="_blank" rel="noopener">{repo}</a>'
    )


ALPHA, BETA = link("alpha-lib"), link("beta-app")

HOW_REFUNDS_WORK = {
    "layer": "answers",
    "id": "how-refunds-work",
    "store": "library-contract-store",
    "source": "docs/answers/how-refunds-work.md",
    "summary": "How do refunds work?",
    "text": (
        "<h2>How do refunds work?</h2>"
        f"<p>A refund is the negative of the amount paid. {ALPHA}'s <code>refund</code> "
        f"function computes it, and {BETA} calls it through the "
        "<code>@example/alpha-lib</code> package.</p>"
    ),
    "as_of": "2026-10-18",
    "commit": None,
    "score": 1,
}
WHO_APPROVES_REFUNDS = {
    "layer": "answers",
    "id": "who-approves-refunds",
    "store": "library-contract-store",
    "source": "docs/answers/who-approves-refunds.md",
    "summary": "Who approves refunds?",
    "text": (
        "<h2>Who approves refunds?</h2>"
        "<p>The payments team approves refunds over the daily limit. The code does not "
        "enforce an approval: it is a team practice.</p>"
    ),
    "as_of": "2026-10-19T09:00:00+00:00",
    "commit": WHO_APPROVES_COMMIT,
    "score": 1,
}
REFUNDS_TOPIC = {
    "layer": "topics",
    "id": "refunds",
    "store": "library-contract-store",
    "source": "docs/topics/refunds.md",
    # The title `config/topics.txt` gives the topic.
    "summary": "Refunds",
    "text": (
        "<h2>Refunds</h2>"
        "<p><b>A refund is the negative of the amount paid, computed in one place: the "
        f"<code>refund</code> function in {ALPHA}.</b> Every caller in this estate "
        "reaches it, either directly or through <code>RefundHandler</code>.</p>"
        "<h3>Mechanisms</h3>"
        "<ul>"
        f"<li><code>refund(amount)</code> in {ALPHA}'s <code>src/refund.ts</code> returns "
        "<code>-amount</code>. It holds no state and performs no checks.</li>"
        "<li><code>RefundHandler.handle(amount)</code> in the same file passes the amount "
        "straight to <code>refund</code>.</li>"
        f"<li>{BETA}'s <code>PaymentService.pay</code> in <code>src/pay.ts</code> imports "
        "<code>refund</code> from the <code>@example/alpha-lib</code> package, so a "
        f"payment in {BETA} is computed by the library rather than by its own code.</li>"
        f"<li>The refund schema in {ALPHA}, <code>schemas/refund.schema.json</code>, "
        "describes a refund request: a <code>requestedOn</code> date and a "
        "<code>total</code> that refers to the <code>money</code> definition.</li>"
        "</ul>"
        "<h3>Evidence</h3>"
        f"<p>DEMO-1 introduced the handler in {ALPHA} and the call from {BETA}. DEMO-2 "
        "added the schema.</p>"
        "<h3>What this is NOT</h3>"
        "<p>This is not a payment ledger. Nothing in the estate stores a refund, approves "
        "one or sends money: those questions need another source.</p>"
    ),
    "as_of": SOURCES_AS_OF,
    "commit": None,
    "score": 1,
}
ALPHA_DIVE = {
    "layer": "deep_dives",
    "id": "alpha-lib",
    "store": "library-contract-store",
    "source": "docs/deep-dives/alpha-lib.md",
    "summary": "Deep dive: alpha-lib",
    "text": (
        f"<h2>{ALPHA}</h2>"
        f"<p>Measured at {ALPHA} <code>1def2e9d</code>.</p>"
        f"<p><b>{ALPHA} is the estate's refund library.</b> It publishes the "
        f"<code>@example/alpha-lib</code> npm package, which {BETA} depends on, and it "
        "holds the refund schema.</p>"
        "<h3>What it holds</h3>"
        "<ul>"
        "<li><code>src/refund.ts</code>: the <code>refund</code> function and the "
        "<code>RefundHandler</code> class whose <code>handle</code> method calls it.</li>"
        "<li><code>schemas/refund.schema.json</code>: the Refund JSON Schema, with a "
        "<code>money</code> definition that the <code>total</code> property refers to.</li>"
        "<li><code>package.json</code>: the package name and version 1.2.0.</li>"
        "</ul>"
        "<h3>History</h3>"
        "<p>Two commits on <code>main</code>. DEMO-1 added the handler and the package "
        "manifest on 1 October 2026; DEMO-2 added the schema the next day. Nothing has "
        "changed it since.</p>"
        "<h3>Who depends on it</h3>"
        f"<p>{BETA} declares <code>@example/alpha-lib</code> in its "
        "<code>package.json</code> and imports <code>refund</code> in "
        "<code>src/pay.ts</code>. No other repository in the estate refers to it.</p>"
        "<h3>Limits</h3>"
        "<p>This dossier describes the code at the commit above and says nothing about "
        "how the package is released or who maintains it.</p>"
    ),
    "as_of": SOURCES_AS_OF,
    "commit": None,
    "score": 1,
}
# The store's own layer, kept in the relationships directory: written as JSON by
# hand, so its `html` is returned as written, and it records no date or commit.
WHO_OWNS_ALPHA_LIB = {
    "layer": "ownership_notes",
    "id": "who-owns-alpha-lib",
    "store": "library-contract-store",
    "source": "docs/answers/who-owns-alpha-lib.md",
    "summary": "Who owns alpha-lib?",
    "text": "<p>The platform team publishes alpha-lib for every web application.</p>",
    "as_of": None,
    "commit": None,
    "score": 1,
}

# DEMO-1 as `build_intent_index._ticket_record` writes it, as `ticket` returns it.
DEMO_1 = {
    "key": "DEMO-1",
    "proposed": False,
    "branches": [],
    "description": ["refund handler", "pay via refund"],
    "text": {"subjects": ["pay via refund", "refund handler"]},
    "first": "2026-10-01",
    "last": "2026-10-05",
    "repos": ["alpha-lib", "beta-app"],
    "commits": 2,
}
DEMO_2 = {
    "key": "DEMO-2",
    "proposed": False,
    "branches": [],
    "description": ["refund schema"],
    "text": {"subjects": ["refund schema"]},
    "first": "2026-10-02",
    "last": "2026-10-02",
    "repos": ["alpha-lib"],
    "commits": 1,
}


def known(name: str) -> dict:
    """A repository the graph holds nodes for."""
    return {"answer": "in the estate", "exact": name, "matched_case_insensitively": False}


class TicketsContract(harness.Contract):
    def _store(self, layers_used: list[str]) -> dict[str, Any]:
        """The envelope every answer carries in this mode."""
        return {
            "id": "library-contract-store",
            "format": {"major": 1, "minor": 4},
            "library_version": "0.19.0",
            "sources_as_of": SOURCES_AS_OF,
            "index": "present" if self.INDEXED else "absent: graph read whole",
            "layers_used": sorted(layers_used),
            "notice": None,
        }

    def _ticket_files(self) -> list[str]:
        """The layer `ticket` reads a key's files from in this mode."""
        return ["index"] if self.INDEXED else ["intent"]

    def _repo_files(self) -> list[str]:
        """The layers `tickets` reads a repository's files from in this mode: the
        graph for the estate's names when there is no index."""
        return ["index"] if self.INDEXED else ["graph", "intent"]

    # --- tickets ------------------------------------------------------------------

    def test_tickets_for_a_file_list_the_landed_ticket(self):
        """Catches: the ticket layer's per-file `tickets` map, or the index's ticket
        rows, read from the wrong key or keyed by the wrong repository, so the
        ticket whose commit made alpha-lib's `src/refund.ts` is not listed, is
        listed as proposed, or its commit count is lost; or `intent.reach`
        not read, so a landed list the library vouches for is flagged as doubtful."""
        found = tools_tickets.tickets(self.session, "alpha-lib", "src/refund.ts")
        self.assertEqual(
            found,
            {
                "store": self._store(self._repo_files()),
                "repository": known("alpha-lib"),
                "reach": True,
                "reach_note": None,
                "totals": {"landed": 1, "proposed": 0},
                "landed": [{"ticket": "DEMO-1", "path": "src/refund.ts", "commits": 1}],
                "proposed": [],
                "total": 1,
                "page": 1,
                "next_page": None,
            },
        )

    def test_tickets_keep_a_branch_only_ticket_apart_with_its_branch(self):
        """Catches: the ticket layer's per-file `proposed` map, or the index's
        `landed` flag, not read, so DEMO-4 - only ever on beta-app's
        `feature/DEMO-4-retry` - is listed as landed or not at all; or its
        branches not read from the library's ticket-descriptions record."""
        found = tools_tickets.tickets(self.session, "beta-app", "src/pay.ts")
        self.assertEqual(
            found,
            {
                "store": self._store([*self._repo_files(), "ticket_descriptions"]),
                "repository": known("beta-app"),
                "reach": True,
                "reach_note": None,
                "totals": {"landed": 1, "proposed": 1},
                "landed": [{"ticket": "DEMO-1", "path": "src/pay.ts", "commits": 1}],
                "proposed": [
                    {
                        "ticket": "DEMO-4",
                        "path": "src/pay.ts",
                        "commits": 1,
                        "branches": ["feature/DEMO-4-retry"],
                    }
                ],
                "total": 2,
                "page": 1,
                "next_page": None,
            },
        )

    # --- ticket -------------------------------------------------------------------

    def test_ticket_has_its_files_record_and_tracker_record(self):
        """Catches: a key's files read from the wrong layer or column, so DEMO-1
        is not shown on both repositories; the library's ticket-descriptions
        fields (`d`, `s`, `first`, `last`, `repos`, `n`) mis-read; or the
        tracker's cached record - title, status, resolution, description,
        comments - read from the wrong keys, or dated by anything but the day it
        resolved, since a resolved record carries no `checked` date."""
        found = tools_tickets.ticket(self.session, "DEMO-1")
        self.assertEqual(
            found,
            {
                "store": self._store(
                    [*self._ticket_files(), "ticket_descriptions", "ticket_tracker"]
                ),
                "ticket": DEMO_1,
                "tracker": {
                    "key": "DEMO-1",
                    "state": "fetched",
                    "as_of": "2026-10-05",
                    "text": {
                        "title": "Refund customers",
                        "status": "Done",
                        "resolution": "Fixed",
                        "description": "Let staff refund a payment.",
                        "comments": ["Refunds over the daily limit need a second approver."],
                    },
                    "comments_truncated": False,
                },
                "tracker_note": None,
                "reach": True,
                "reach_note": None,
                "totals": {"landed": 4, "proposed": 0},
                "landed": [
                    {"repository": "alpha-lib", "path": "package.json", "commits": 1},
                    {"repository": "alpha-lib", "path": "src/refund.ts", "commits": 1},
                    {"repository": "beta-app", "path": "package.json", "commits": 1},
                    {"repository": "beta-app", "path": "src/pay.ts", "commits": 1},
                ],
                "proposed": [],
                "total": 4,
                "page": 1,
                "next_page": None,
            },
        )

    def test_ticket_the_tracker_records_absent(self):
        """Catches: `fetch-tickets`' `absent` outcome not recognised, so DEMO-2 -
        which the tracker did not return - is reported as a record in a shape the
        server does not read, or as fetched with no text; or its `checked` date
        not read, so the answer does not say when the tracker was asked."""
        found = tools_tickets.ticket(self.session, "DEMO-2")
        self.assertEqual(found["ticket"], DEMO_2)
        self.assertEqual(
            found["tracker"],
            {
                "key": "DEMO-2",
                "state": "absent",
                "as_of": "2026-10-20",
                "text": {},
                "comments_truncated": False,
            },
        )
        self.assertIsNone(found["tracker_note"])
        self.assertEqual(
            found["landed"],
            [{"repository": "alpha-lib", "path": "schemas/refund.schema.json", "commits": 1}],
        )
        self.assertEqual(found["proposed"], [])

    def test_a_key_no_layer_holds(self):
        """Catches: an unknown key answered with another ticket's files or records,
        or as anything but nothing, with what was searched."""
        found = tools_tickets.ticket(self.session, "DEMO-9")
        # The ticket layer is named as searched with or without the index.
        searched = "intent, ticket_descriptions and ticket_tracker layers"
        if self.INDEXED:
            searched = "index, " + searched
        self.assertEqual(
            found,
            {
                "store": self._store(
                    [*self._ticket_files(), "ticket_descriptions", "ticket_tracker"]
                ),
                "ticket": None,
                "tracker": None,
                "tracker_note": "the ticket_tracker layer holds no record of this ticket",
                "reach": True,
                "reach_note": None,
                "searched": f"searched 3 repositories in the {searched}",
                "totals": {"landed": 0, "proposed": 0},
                "landed": [],
                "proposed": [],
                "total": 0,
                "page": 1,
                "next_page": None,
            },
        )

    # --- answers ------------------------------------------------------------------

    def test_answers_from_every_library_answer_layer(self):
        """Catches: the library's topic briefs, deep dives or answers-layer file
        read from the wrong keys (`title`, `html`, `source`); the answers layer's
        `as_of` or `commit` not read, so the dated, uncommitted answer and the
        committed one lose their dates; or briefs and dives dated by anything but
        `sources_as_of`."""
        found = tools_tickets.answers(self.session, "refund")
        self.assertEqual(
            found,
            {
                "store": self._store(["answers", "deep_dives", "ownership_notes", "topics"]),
                "matches": 4,
                "items": [ALPHA_DIVE, HOW_REFUNDS_WORK, REFUNDS_TOPIC, WHO_APPROVES_REFUNDS],
                "total": 4,
                "page": 1,
                "next_page": None,
            },
        )

    def test_an_answer_query_nothing_matches(self):
        """Catches: the `_metadata` block the library writes into the briefs and
        deep-dives files counted as an answer, so an empty answer overstates what
        was searched (5 answers: one brief, one dive, two written answers and one
        ownership note)."""
        found = tools_tickets.answers(self.session, "invoice")
        self.assertEqual(
            found,
            {
                "store": self._store(["answers", "deep_dives", "ownership_notes", "topics"]),
                "matches": 0,
                "searched": "searched 5 answers in the answers, deep_dives, ownership_notes "
                "and topics layers",
                "items": [],
                "total": 0,
                "page": 1,
                "next_page": None,
            },
        )

    def test_answers_from_the_extra_layer_in_the_relationships_directory(self):
        """Catches: the store's extra layer kept beside the library's relationships
        files not read as answers, or read with a date it does not record."""
        self.assertEqual(EXTRA_LAYER, "knowledge/relationships/ownership-notes.json")
        found = tools_tickets.answers(self.session, "owns")
        self.assertEqual(found["matches"], 1)
        self.assertEqual(found["items"], [WHO_OWNS_ALPHA_LIB])


TicketsWithIndex, TicketsWithoutIndex = harness.both_modes(TicketsContract)


if __name__ == "__main__":
    unittest.main()
