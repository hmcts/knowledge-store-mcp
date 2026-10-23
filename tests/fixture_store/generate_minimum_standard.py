"""Make the fixture's minimum-standard records with knowledge-store-builder's own
writer, and check them against the literals `build.py` holds (DESIGN.md 11).

    uv run python tests/fixture_store/generate_minimum_standard.py <library src>

`<library src>` is the `src/` directory of a knowledge-store-builder checkout that
has the minimum-standard stage. The library is not a dependency of this repository
(see `tests/library_store/generate.py` for why), so this is run by hand and its
output transcribed; CI never runs it. It writes three synthetic clones to a
temporary directory, runs `build_minimum_standard.evidence_for` over each, and
supplies git's two answers - the last commit, and the tracked files - through the
writer's own `run=` seam, so each `as_of` is the commit DESIGN.md 1 pins. It exits
0 when the records and the settings snapshot equal `build.py`'s, and otherwise
prints the writer's records so they can be transcribed.

The clones' files are the only hand-written part. The secret-scan finding comes
from a made-up value in `config/application.yml`; the writer records its rule,
path and line, and the value reaches no file of the fixture.
"""

import importlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixture_store.build import (  # noqa: E402
    MINIMUM_STANDARD_RECORDS,
    MINIMUM_STANDARD_SETTINGS,
    REPOSITORIES,
)

# name -> (every tracked file and its text, the content-set paths the scan reads).
CLONES: dict[str, tuple[dict[str, str], list[str]]] = {
    "payments-api": (
        {
            ".github/CODEOWNERS": "* @example-org/payments-team\n",
            "SECURITY.md": "# Security\n\nReport a vulnerability to security@example.org.\n",
            "config/application.yml": (
                "server:\n  port: 8080\ndatabase:\n  password: Zq8vPLm3Rt7xKw2N\n"
            ),
            "docs/threat-model.md": "# Threat model\n",
            ".github/workflows/codeql.yml": "steps:\n  - uses: github/codeql-action/init@v3\n",
            ".github/workflows/secrets.yml": "steps:\n  - uses: gitleaks/gitleaks-action@v2\n",
            ".github/dependabot.yml": "version: 2\n",
            "src/main/java/com/example/payments/RefundHandler.java": "class RefundHandler {}\n",
        },
        ["config/application.yml", "src/main/java/com/example/payments/RefundHandler.java"],
    ),
    "ledger-service": (
        {
            "CODEOWNERS": "# owners to be agreed\n*\n",
            "src/main/java/com/example/ledger/LedgerClient.java": "class LedgerClient {}\n",
        },
        ["src/main/java/com/example/ledger/LedgerClient.java"],
    ),
    "reporting-worker": (
        {
            ".github/CODEOWNERS": "/src/ @example-org/reporting-team\n",
            "SECURITY.md": "# Security\n\nAsk the reporting team.\n",
            ".github/workflows/build.yml": (
                "steps:\n  - uses: github/codeql-action/analyze@v3\n"
                "  - uses: gitleaks/gitleaks-action@v2\n"
            ),
            "renovate.json": "{}\n",
        },
        [],
    ),
}


def records(library_src: Path, work: Path) -> dict[str, dict]:
    sys.path.insert(0, str(library_src))
    writer = importlib.import_module("knowledgestore.build_minimum_standard")
    contract = importlib.import_module("knowledgestore.minimum_standard")
    snapshot = {**MINIMUM_STANDARD_SETTINGS, "format": contract.format_stamp()}
    made = {}
    for name, (files, content) in sorted(CLONES.items()):
        clone = work / name
        for rel, text in files.items():
            (clone / rel).parent.mkdir(parents=True, exist_ok=True)
            (clone / rel).write_text(text, encoding="utf-8")
        sha, date = REPOSITORIES[name]
        tracked = sorted(files)

        def run(_clone, *args, sha=sha, date=date, tracked=tracked) -> str:
            if args[0] == "log":
                return f"{sha}\x1f{date}\n"
            if args[0] == "ls-files":
                return "\0".join(tracked) + "\0"
            raise AssertionError(f"git {args[0]} is not one the writer was expected to run")

        record = writer.evidence_for(clone, content, snapshot, run)
        made[name] = {k: v for k, v in record.items() if k != "format"}
    return made


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        made = records(Path(sys.argv[1]).resolve(), Path(tmp))
    if made == MINIMUM_STANDARD_RECORDS:
        print(f"build.py's {len(made)} minimum-standard records are the writer's own")
        return 0
    print(json.dumps(made, indent=2, sort_keys=True))
    print("build.py's records differ from the writer's: transcribe the above", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
