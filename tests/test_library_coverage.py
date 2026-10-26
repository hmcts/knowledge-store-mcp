"""The coverage map, `library_store/COVERAGE.md`, kept honest.

The map names, for every tool, every argument of every tool and every layer
field the server reads, the test that fails if that read breaks. These tests
hold it to `server.TOOLS` and to the test suite as it is: a tool or argument
with no row, a row with no test, or a row citing a test that no longer exists
fails here, so the map cannot claim coverage the suite does not have.

The map cites each library contract in its `WithIndex` form; `both_modes`
makes a `WithoutIndex` twin of every one. The guard checks that the twin exists
and that each form reads the store its name claims.
"""

import re
import unittest
from pathlib import Path

from knowledgestore_mcp import server
from library_store.generate import NO_INDEX, OUT

COVERAGE = Path(__file__).resolve().parent / "library_store" / "COVERAGE.md"

# `module.Class.test_name`, as `loadTestsFromName` resolves it from `tests/`.
TEST_ID = re.compile(r"`(test_\w+\.\w+\.test_\w+)`")
NAME = re.compile(r"^`(\w+)`$")
TOOL_ROW = "—"


def _section(heading: str) -> list[list[str]]:
    """The body rows of the table under `## <heading>`, each as its cells."""
    rows: list[list[str]] = []
    inside = False
    for line in COVERAGE.read_text("utf-8").splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == heading
            continue
        if inside and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells[0] in ("Tool", "Layer field") or set(cells[0]) <= {"-", ":"}:
                continue
            rows.append(cells)
    return rows


def _arguments() -> dict[tuple[str, str], tuple[str, str]]:
    """(tool, argument or `—`) -> (library cell, fixture cell), from the map."""
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for tool, argument, library, fixture in _section("Tools and arguments"):
        tool_name = NAME.match(tool)
        argument_name = NAME.match(argument)
        assert tool_name, f"not a tool name: {tool}"
        key = (tool_name[1], argument_name[1] if argument_name else argument)
        assert key not in found, f"two rows for {key}"
        found[key] = (library, fixture)
    return found


def _library_ids(cell: str) -> list[str]:
    return [found for found in TEST_ID.findall(cell) if found.startswith("test_library_")]


def _loads(name: str) -> unittest.TestCase | None:
    """The one test `name` resolves to, or None for the loader's stand-in for a
    name it could not find. (A `both_modes` class's own name differs from the
    module attribute the map cites, so the method name is what is compared.)"""
    tests = list(unittest.defaultTestLoader.loadTestsFromName(name))
    if (
        len(tests) == 1
        and isinstance(tests[0], unittest.TestCase)
        and type(tests[0]).__name__ != "_FailedTest"
        and tests[0]._testMethodName == name.rsplit(".", 1)[1]
    ):
        return tests[0]
    return None


class CoverageMapTest(unittest.TestCase):
    def test_every_tool_has_a_contract_test(self):
        """Catches: a tool added to `server.TOOLS` - a fourteenth - with no
        contract test against the library's store, or a tool's row left with
        only fixture tests."""
        rows = _arguments()
        for spec in server.TOOLS:
            with self.subTest(tool=spec.name):
                self.assertTrue((spec.name, TOOL_ROW) in rows, "no row for the tool")
                library, _ = rows[(spec.name, TOOL_ROW)]
                self.assertTrue(_library_ids(library), "no library contract test")
        tools = {spec.name for spec in server.TOOLS}
        self.assertEqual({tool for tool, _ in rows} - tools, set(), "rows for no tool")

    def test_every_argument_has_a_row(self):
        """Catches: an argument added to a tool with no test that exercises it, a
        row that names no test - or a fixture test with no reason the library's
        output cannot exercise the argument - or a row left for an argument the
        tool no longer takes."""
        rows = _arguments()
        expected = {(spec.name, param.name) for spec in server.TOOLS for param in spec.params}
        for tool, argument in sorted(expected):
            with self.subTest(tool=tool, argument=argument):
                self.assertTrue((tool, argument) in rows, "no row for the argument")
                library, fixture = rows[(tool, argument)]
                if _library_ids(library):
                    continue
                fixture_ids = TEST_ID.findall(fixture)
                self.assertTrue(fixture_ids, "neither a library nor a fixture test")
                reason = TEST_ID.sub("", fixture).strip(" ;,.:—-")
                self.assertTrue(reason, "a fixture test with no reason")
        stale = {key for key in rows if key[1] != TOOL_ROW} - expected
        self.assertEqual(stale, set(), "rows for arguments no tool takes")

    def test_every_cited_test_exists(self):
        """Catches: a test renamed or deleted while the map still cites it, so the
        map claims coverage the suite no longer has; or a contract cited in one
        read mode whose other mode was lost."""
        cited = sorted(set(TEST_ID.findall(COVERAGE.read_text("utf-8"))))
        self.assertTrue(cited)
        for name in cited:
            with self.subTest(test=name):
                test = _loads(name)
                self.assertIsNotNone(test, "does not resolve to a test")
                if "WithIndex." in name:
                    # Each form must read the store its name claims.
                    self.assertEqual(
                        (getattr(test, "INDEXED", None), getattr(test, "STORE", None)),
                        (True, OUT),
                        "the cited form does not read the indexed store",
                    )
                    twin = _loads(name.replace("WithIndex.", "WithoutIndex."))
                    self.assertIsNotNone(twin, "no WithoutIndex twin")
                    self.assertEqual(
                        (getattr(twin, "INDEXED", None), getattr(twin, "STORE", None)),
                        (False, NO_INDEX),
                        "the twin does not read the store without the index",
                    )


if __name__ == "__main__":
    unittest.main()
