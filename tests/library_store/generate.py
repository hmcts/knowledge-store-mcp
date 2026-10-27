"""Regenerate `store/` and `no-index/`: stores knowledge-store-builder's own stages
wrote, at the release named in `VERSION`, with the graph graphify `GRAPHIFY`
extracted.

    uv run python tests/library_store/generate.py           # rebuild both
    uv run python tests/library_store/generate.py --check   # rebuild everything elsewhere, compare
    uv run python tests/library_store/generate.py --release v0.18.3 [--out DIR]

The library is not a dependency of this repository: it is not on PyPI, its
internal feed needs credentials CI does not hold, and installing it from git
means building it, which CI's `uv sync --no-build` refuses on purpose. So its
output is committed, and this script is how that output was made. It needs `uv`
and `git` on PATH, and network access: the first run builds the frozen
environment `requirements.txt` names, and every run, `--check` included,
downloads the embedding model (about 90 MB) into the build's own cache.

This script writes the inputs - four git repositories, their bare remotes, the
store's `config/`, one extra layer, and what an agent authors in a real build
(a community summary, a topic brief, a deep dive, two written answers) - into a
fresh temporary directory, then runs `pipeline.py` inside the frozen
environment, which runs the library's real stages in their documented order. It
runs it twice over the same inputs: once whole for `store/`, and once with
`--no-index` for `no-index/`, so the second store records its index absent
exactly as the library does. Everything copied into either is the library's or
graphify's own bytes. Every commit carries a fixed identity and date, so the
shas recorded in `inputs.json` and every byte of both stores are the same on
every run; `--check` proves that by rebuilding and comparing.

Hand-written: this script's clone contents, commit messages and `config/`, the
tracker and GitHub answers (`tracker_responses.json`, `github_responses.json`),
and the agent-authored inputs above. Library output: everything under `store/`
and `no-index/`; never edit those by hand. `inputs.json` is this script's record
of each clone's HEAD.

The pins: `VERSION` and `GRAPHIFY` here; every other package, and the
partitioner, interpreter and git the store was made with, in `requirements.txt`.
Move a pin, regenerate, run `--check`, then the tests.

No library code is replaced at `VERSION`: `pipeline.py` stands in for the IO
edges only, through the library's own seams, and gives `minimum-standard-settings`
a fixed clock through its `now=` seam.

Frozen stores: `frozen/v<release>/` holds `store/` and `no-index/` built by an
older release in `RELEASES`, from the same inputs, so they differ from the main
store only by what that release does; real stores lag the library, and the tests
in `test_library_older_stores` keep the server reading them. Each records its own
`requirements.txt`, compiled the same way as the main one for that tag and run
on the same `PYTHON`. `pipeline.py` skips each stage the release does not have,
reading its own `cli.STAGES`, and gives v0.19.0, which has no `now=` seam, its
fixed clock by rebinding the stage module's `datetime` for the one call: the one
place library code is replaced, and only for that release. `--release` rebuilds
one (into `--out`, by default its own directory); regenerate one only when its
release is dropped from support or its requirements are recompiled. `--check`
covers every one.

CI runs the tests over the committed output and never runs this script.
`COVERAGE.md` is the claim of what those tests cover, and
`test_library_coverage` keeps it honest.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# The release the committed store came from. The tests check the manifest's
# `library_version` against it, so the record cannot drift from the bytes.
VERSION = "0.19.1"
GRAPHIFY = "0.9.93"
# The interpreter the frozen environment was compiled for.
PYTHON = "3.14"

HERE = Path(__file__).resolve().parent
OUT = HERE / "store"
NO_INDEX = HERE / "no-index"
INPUTS = HERE / "inputs.json"
REQUIREMENTS = HERE / "requirements.txt"
PIPELINE = HERE / "pipeline.py"

# Older releases whose stores are kept, frozen, beside the main one: v0.18.3
# writes manifest format 1.3, before `json-schema` and `minimum_standard`;
# v0.19.0 is the release the main store moved from. Each lives under
# `frozen/v<release>/`.
FROZEN = HERE / "frozen"
RELEASES = ("0.18.3", "0.19.0")

ORG = "example-org"
# `inputs.json`'s key for the store repository's commit of the committed answer.
STORE_ANSWERS_COMMIT = "store_answers_commit"
STORE_ID = "library-contract-store"

# Who made every commit. Fixed, with the dates below, so every sha is too.
IDENTITY = {
    "GIT_AUTHOR_NAME": "Fixture Author",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture Author",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}

ALPHA_PACKAGE = '{\n  "name": "@example/alpha-lib",\n  "version": "1.2.0"\n}\n'
# DEMO-7's manifest: alpha-lib depends on the package beta-app publishes, which
# depends on alpha-lib's, so the two repositories form a loop. The dependency is
# on line 5, the last entry, so with no comma.
ALPHA_PACKAGE_WITH_APP = """\
{
  "name": "@example/alpha-lib",
  "version": "1.2.0",
  "dependencies": {
    "beta-app": "^1.0.0"
  }
}
"""
ALPHA_REFUND = """\
export function refund(amount: number): number {
  return -amount;
}

export class RefundHandler {
  handle(amount: number): number {
    return refund(amount);
  }
}
"""
# The same file with one trailing comment, so every line above it keeps its number
# and graphify sees no new symbol: a second DEMO-1 commit on one file, so a link
# counts two commits.
ALPHA_REFUND_ROUNDED = ALPHA_REFUND + "// Amounts are refunded in whole pence.\n"
# `credit` extends `money` through `allOf`, so the json-schema stage writes an
# `extends` edge. It shares line 6 with the `$defs` it opens, so every line the
# other nodes sit on is unchanged.
ALPHA_SCHEMA = """\
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.org/schemas/refund.schema.json",
  "title": "Refund",
  "type": "object",
  "$defs": {"credit": {"allOf": [{"$ref": "#/$defs/money"}], "description": "Given back to the customer."},
    "money": {
      "type": "object",
      "properties": {
        "amount": {"type": "number"}
      }
    }
  },
  "properties": {
    "requestedOn": {"type": "string", "format": "date"},
    "total": {"$ref": "#/$defs/money"}
  }
}
"""

# `gamma-service` is a package no clone publishes, so `relationships` records it
# under `unresolved_targets_outside_estate` with its name as the key, which the
# server answers for the repository name `gamma-service`.
BETA_PACKAGE = """\
{
  "name": "beta-app",
  "dependencies": {
    "@example/alpha-lib": "^1.2.0",
    "gamma-service": "^2.0.0"
  }
}
"""
BETA_PAY = """\
import { refund } from "@example/alpha-lib";

export class PaymentService {
  pay(amount: number): number {
    return refund(amount);
  }
}
"""
BETA_PAY_RETRY = """\
import { refund } from "@example/alpha-lib";

export class PaymentService {
  pay(amount: number): number {
    return refund(amount);
  }

  retry(amount: number): number {
    return this.pay(amount);
  }
}
"""
BETA_CODEOWNERS = "* @example-org/payments-team\n"
BETA_SECURITY = "# Security\n\nReport a vulnerability to security@example.org.\n"
BETA_DEPENDABOT = """\
version: 2
updates:
  - package-ecosystem: npm
    directory: /
    schedule:
      interval: weekly
"""
BETA_CODEQL = """\
name: CodeQL
on: [push]
jobs:
  analyse:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: github/codeql-action/init@v3
        with:
          languages: javascript-typescript
      - uses: github/codeql-action/analyze@v3
"""

# eta-portal holds only a package manifest: a second consumer of the package
# beta-app publishes, on line 5.
ETA_PACKAGE = """\
{
  "name": "eta-portal",
  "version": "0.1.0",
  "dependencies": {
    "beta-app": "^1.0.0"
  }
}
"""

DELTA_VALUES = "replicas: 2\n"
# The values file's first version, which DEMO-3 replaces: a DEMO-5 commit the
# tracker denies, with the file's final content, and so the graph and the
# deployments layer, unchanged.
DELTA_VALUES_FIRST = "replicas: 1\n"
# A setting named for a secret: the scan records a `key-name` finding, a name and
# not a committed value, so on its own it leaves `no_secrets` undetermined.
DELTA_APPLICATION = "password: fixture-not-a-secret\n"
# An empty file named as a private key is: the scan finds it by its name among the
# tracked files and never opens it, so `no_secrets` is `absent` with a `key-file`
# finding that has no line. Empty, so nothing key-like is committed.
DELTA_SIGNING_KEY = ""

# Each repository's commits on `main`, oldest first: (subject, date, files).
# A file mapped to None is deleted. Dates are distinct and in October 2026.
COMMITS = {
    "alpha-lib": [
        (
            "DEMO-1: refund handler",
            "2026-10-01T09:00:00+00:00",
            {"package.json": ALPHA_PACKAGE, "src/refund.ts": ALPHA_REFUND},
        ),
        (
            "DEMO-2: refund schema",
            "2026-10-02T09:00:00+00:00",
            {"schemas/refund.schema.json": ALPHA_SCHEMA},
        ),
        (
            "DEMO-1: refund rounding",
            "2026-10-03T09:00:00+00:00",
            {"src/refund.ts": ALPHA_REFUND_ROUNDED},
        ),
        (
            "DEMO-7: alpha reads app constants",
            "2026-10-04T09:00:00+00:00",
            {"package.json": ALPHA_PACKAGE_WITH_APP},
        ),
    ],
    "beta-app": [
        (
            "DEMO-1: pay via refund",
            "2026-10-05T09:00:00+00:00",
            {"package.json": BETA_PACKAGE, "src/pay.ts": BETA_PAY},
        ),
        (
            "DEMO-3: ownership",
            "2026-10-06T09:00:00+00:00",
            {
                ".github/CODEOWNERS": BETA_CODEOWNERS,
                "SECURITY.md": BETA_SECURITY,
                ".github/dependabot.yml": BETA_DEPENDABOT,
                ".github/workflows/codeql.yml": BETA_CODEQL,
            },
        ),
    ],
    "delta-deploy": [
        (
            "DEMO-5: first production values",
            "2026-10-07T12:00:00+00:00",
            {"ansible/group_vars/prd/beta-app_values.yaml.j2": DELTA_VALUES_FIRST},
        ),
        (
            "DEMO-3: production values",
            "2026-10-08T09:00:00+00:00",
            {
                "ansible/group_vars/prd/beta-app_values.yaml.j2": DELTA_VALUES,
                "config/application.yml": DELTA_APPLICATION,
                "deploy/signing.pem": DELTA_SIGNING_KEY,
            },
        ),
    ],
    "eta-portal": [
        (
            "DEMO-6: portal depends on the app",
            "2026-10-05T12:00:00+00:00",
            {"package.json": ETA_PACKAGE},
        ),
    ],
}
# One branch never merged: its ticket is the proposed one.
BRANCHES = {
    "beta-app": (
        "feature/DEMO-4-retry",
        [("DEMO-4: retry", "2026-10-07T09:00:00+00:00", {"src/pay.ts": BETA_PAY_RETRY})],
    ),
}

REPOSITORIES_TXT = "".join(f"{name}|https://github.com/{ORG}/{name}.git|main\n" for name in COMMITS)

# A layer the store owns, declared in `extra_layers` and kept beside the library's
# relationships files: it must never be read as a repository.
EXTRA_LAYER = "knowledge/relationships/ownership-notes.json"
# The answers-layer output, declared beside it as the docs declare it.
ANSWERS_LAYER = "knowledge/answers.json"
EXTRA_LAYERS_JSON = (
    '{\n  "ownership_notes": {\n'
    f'    "path": "{EXTRA_LAYER}",\n'
    '    "description": "Which team owns each shared library",\n'
    '    "kind": "answers"\n  },\n'
    '  "answers": {\n'
    f'    "path": "{ANSWERS_LAYER}",\n'
    '    "description": "Written journeys and findings.",\n'
    '    "kind": "answers"\n  }\n}\n'
)
EXTRA_LAYER_JSON = (
    '{\n  "who-owns-alpha-lib": {\n'
    '    "title": "Who owns alpha-lib?",\n'
    '    "html": "<p>The platform team publishes alpha-lib for every web application.</p>",\n'
    '    "source": "docs/answers/who-owns-alpha-lib.md"\n  }\n}\n'
)

# Agent-authored inputs. In a real build an agent writes these from the evidence
# the `extract` stages gather; here they are fixed text the tests quote.

# The summary of the community holding `alpha-lib::src_refund_refund`, keyed by
# that anchor; `pipeline.py` re-keys it by community id before `summaries merge`.
SUMMARY_ANCHOR = "alpha-lib::src_refund_refund"
SUMMARY_REFUNDS = (
    "The refund code alpha-lib publishes as the @example/alpha-lib package. "
    "The refund function turns an amount into its negative, and "
    "RefundHandler.handle passes each amount it is given to refund."
)

TOPICS_TXT = "refunds | Refunds | refund, payment\n"
TOPIC_REFUNDS = """\
# Refunds

**A refund is the negative of the amount paid, computed in one place: the
`refund` function in alpha-lib.** Every caller in this estate reaches it, either
directly or through `RefundHandler`.

## Mechanisms

- `refund(amount)` in alpha-lib's `src/refund.ts` returns `-amount`. It holds no state and performs no checks.
- `RefundHandler.handle(amount)` in the same file passes the amount straight to `refund`.
- beta-app's `PaymentService.pay` in `src/pay.ts` imports `refund` from the `@example/alpha-lib` package, so a payment in beta-app is computed by the library rather than by its own code.
- The refund schema in alpha-lib, `schemas/refund.schema.json`, describes a refund request: a `requestedOn` date and a `total` that refers to the `money` definition.

## Evidence

DEMO-1 introduced the handler in alpha-lib and the call from beta-app. DEMO-2
added the schema.

## What this is NOT

This is not a payment ledger. Nothing in the estate stores a refund, approves
one or sends money: those questions need another source.
"""

# `{measured}` is the first eight characters of alpha-lib's HEAD, filled in once
# the clone is made, as an author names the commit they read.
DEEP_DIVE_ALPHA = """\
# alpha-lib

Measured at alpha-lib `{measured}`.

**alpha-lib is the estate's refund library.** It publishes the
`@example/alpha-lib` npm package, which beta-app depends on, and it holds the
refund schema.

## What it holds

- `src/refund.ts`: the `refund` function and the `RefundHandler` class whose `handle` method calls it.
- `schemas/refund.schema.json`: the Refund JSON Schema, with a `money` definition that the `total` property refers to and the `credit` definition extends.
- `package.json`: the package name, version 1.2.0 and a dependency on beta-app.

## History

Four commits on `main`. DEMO-1 added the handler and the package manifest on
1 October 2026; DEMO-2 added the schema the next day, DEMO-1 returned on
3 October with a note on rounding, and DEMO-7 added the dependency on beta-app
on 4 October. Nothing has changed it since.

## Who depends on it

beta-app declares `@example/alpha-lib` in its `package.json` and imports
`refund` in `src/pay.ts`. No other repository in the estate refers to it.

## Limits

This dossier describes the code at the commit above and says nothing about how
the package is released or who maintains it.
"""

# Written answers for `answers-layer`. One carries a `date:`; the other has
# none, so it is dated by its commit in the store's own repository (2026-10-19,
# made by `pipeline.py`).
ANSWER_HOW_REFUNDS_WORK = """\
---
date: 2026-10-18
---
# How do refunds work?

A refund is the negative of the amount paid. alpha-lib's `refund` function
computes it, and beta-app calls it through the `@example/alpha-lib` package.
"""
ANSWER_WHO_APPROVES_REFUNDS = """\
# Who approves refunds?

The payments team approves refunds over the daily limit. The code does not
enforce an approval: it is a team practice.
"""
ANSWERS = {
    "docs/answers/how-refunds-work.md": ANSWER_HOW_REFUNDS_WORK,
    "docs/answers/who-approves-refunds.md": ANSWER_WHO_APPROVES_REFUNDS,
}
PRODUCTS_JSON = '{"payments": ["alpha-lib", "beta-app"]}\n'

# Library outputs never committed: the plain graph and index sit beside the
# archives a store commits instead.
NOT_COPIED = ("graphify-out/graph.json", "knowledge/index/store.sqlite")
# Committed although no layer names it: the partitioner record travels with the
# graph so a reader can tell whether its environment reproduces the clustering.
ALSO_COPIED = ("graphify-out/clustering-inputs.json",)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git_env() -> dict[str, str]:
    """git with no user or system configuration, so nothing on this machine -
    a signing key, a hook path, a default branch - reaches a commit."""
    return {
        **os.environ,
        **IDENTITY,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def _git(repo: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env=env or _git_env(),
    )
    return completed.stdout


def _commit(repo: Path, subject: str, date: str, files: dict[str, str]) -> None:
    for rel, text in files.items():
        _write(repo / rel, text)
    _git(repo, "add", "--", *files)
    dated = {**_git_env(), "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    _git(repo, "commit", "-q", "-m", subject, env=dated)


def _sources(tmp: Path) -> dict[str, str]:
    """Each repository built in `sources/`, pushed bare to `remotes/`. Returns
    each one's `main` sha."""
    shas = {}
    for name, commits in COMMITS.items():
        repo = tmp / "sources" / name
        repo.mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
        for subject, date, files in commits:
            _commit(repo, subject, date, files)
        if name in BRANCHES:
            branch, extra = BRANCHES[name]
            _git(repo, "checkout", "-q", "-b", branch)
            for subject, date, files in extra:
                _commit(repo, subject, date, files)
            _git(repo, "checkout", "-q", "main")
        shas[name] = _git(repo, "rev-parse", "HEAD").strip()
        remote = tmp / "remotes" / f"{name}.git"
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(repo), str(remote)],
            check=True,
            env=_git_env(),
        )
    return shas


def _store_inputs(root: Path, shas: dict[str, str]) -> None:
    """The store's configuration and authored inputs, in the store root; the
    summaries JSON beside it, where `pipeline.py` reads it. `shas` are the clones'
    HEADs, which the deep dive names."""
    _write(root / "config" / "repositories.txt", REPOSITORIES_TXT)
    _write(root / "config" / "store-extra-layers.json", EXTRA_LAYERS_JSON)
    _write(root / "config" / "products.json", PRODUCTS_JSON)
    _write(root / "config" / "topics.txt", TOPICS_TXT)
    _write(root / EXTRA_LAYER, EXTRA_LAYER_JSON)
    _write(root / "docs" / "topics" / "refunds.md", TOPIC_REFUNDS)
    dive = DEEP_DIVE_ALPHA.replace("{measured}", shas["alpha-lib"][:8])
    _write(root / "docs" / "deep-dives" / "alpha-lib.md", dive)
    for rel, text in ANSWERS.items():
        _write(root / rel, text)
    summaries = json.dumps({SUMMARY_ANCHOR: SUMMARY_REFUNDS}, indent=1) + "\n"
    _write(root.parent / "authored" / "summaries-by-anchor.json", summaries)


def _uv_dir(*arguments: str) -> str:
    return subprocess.run(
        ["uv", *arguments], check=True, text=True, stdout=subprocess.PIPE
    ).stdout.strip()


def _isolated_home(tmp: Path) -> dict[str, str]:
    """HOME and the XDG directories inside `tmp`, so nothing in the user's home
    reaches the build; uv keeps its real cache and interpreters, which it would
    otherwise look for under the moved HOME."""
    home = tmp / "home"
    dirs = {
        "HOME": home,
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local/share",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return {
        "UV_CACHE_DIR": _uv_dir("cache", "dir"),
        "UV_PYTHON_INSTALL_DIR": _uv_dir("python", "dir"),
        **{name: str(path) for name, path in dirs.items()},
    }


@dataclass(frozen=True)
class Build:
    """One release's pair of stores: the frozen environment it runs in, and where
    its `store/` and `no-index/` are committed."""

    version: str
    requirements: Path
    store: Path
    no_index: Path

    @property
    def library(self) -> str:
        return f"git+https://github.com/hmcts/knowledge-store-builder@v{self.version}"

    @property
    def name(self) -> str:
        """The stores' directory, relative to this one, for messages."""
        parent = self.store.parent
        return "" if parent == HERE else f"{parent.relative_to(HERE).as_posix()}/"


MAIN = Build(VERSION, REQUIREMENTS, OUT, NO_INDEX)


def frozen(version: str, out: Path | None = None) -> Build:
    """The frozen release `version`, its stores under `out` (by default its own
    directory); its requirements are always its own directory's."""
    home = FROZEN / f"v{version}"
    where = out or home
    return Build(version, home / "requirements.txt", where / "store", where / "no-index")


def _uv_run(requirements: Path) -> list[str]:
    return [
        "uv",
        "run",
        "--no-project",
        "--python",
        PYTHON,
        "--with-requirements",
        str(requirements),
    ]


def _recorded(requirements: Path, field: str) -> str:
    """The `# <field>:` line of a requirements file's header."""
    for line in requirements.read_text("utf-8").splitlines():
        if line.startswith(f"# {field}:"):
            return line.removeprefix(f"# {field}:").strip()
    return "not recorded"


def _running_git() -> str:
    """This machine's git, which made every commit and which the library's stages
    ran: how it writes a date (`%cI` gives UTC as `Z` on 2.55) reaches the store."""
    done = subprocess.run(["git", "--version"], text=True, stdout=subprocess.PIPE)
    return done.stdout.strip().removeprefix("git version ") or "unknown"


def _running_interpreter(tmp: Path, requirements: Path) -> str:
    script = (
        "import platform, sys; print(platform.python_version(), sys.platform, platform.machine())"
    )
    done = subprocess.run(
        [*_uv_run(requirements), "python", "-c", script],
        text=True,
        stdout=subprocess.PIPE,
        env={**os.environ, **_isolated_home(tmp)},
    )
    return done.stdout.strip() or "unknown"


def _pipeline(tmp: Path, root: Path, requirements: Path, *flags: str) -> None:
    """Run `pipeline.py` in the frozen environment. git's `insteadOf` sends every
    `https://github.com/example-org/` URL to the bare remotes, so the real `sync`
    clones offline from the URLs `config/repositories.txt` names."""
    env = {
        **_git_env(),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{(tmp / 'remotes').as_uri()}/.insteadOf",
        "GIT_CONFIG_VALUE_0": f"https://github.com/{ORG}/",
        "PYTHONHASHSEED": "0",
        **_isolated_home(tmp),
    }
    command = [*_uv_run(requirements), "python", str(PIPELINE), str(root), *flags]
    subprocess.run(command, cwd=root, env=env, check=True)


def _copied(root: Path) -> list[str]:
    """Store-relative files to commit: every path of every layer the manifest
    marks present (but the plain graph and index), plus `ALSO_COPIED`."""
    manifest = json.loads((root / "knowledge/store-manifest.json").read_text("utf-8"))
    tables = [manifest["layers"], manifest.get("extra_layers") or {}]
    wanted = {"knowledge/store-manifest.json", *ALSO_COPIED}
    for table in tables:
        for layer in table.values():
            if not layer.get("present"):
                continue
            for rel in layer.get("paths") or [layer.get("path")]:
                if rel:
                    wanted.add(rel.rstrip("/"))
    files = set()
    for rel in wanted - set(NOT_COPIED):
        path = root / rel
        if path.is_dir():
            files.update(
                found.relative_to(root).as_posix() for found in path.rglob("*") if found.is_file()
            )
        elif path.is_file():
            files.add(rel)
    return sorted(files)


# The answer `pipeline.py` commits in the store's own repository.
COMMITTED_ANSWER = "docs/answers/who-approves-refunds.md"
assert COMMITTED_ANSWER in ANSWERS


def _answer_commit(root: Path) -> str:
    """The store repository's last commit of `COMMITTED_ANSWER`: made from the
    inputs with a fixed identity and date, so the same on every run."""
    return _git(root, "log", "-1", "--format=%H", "--", COMMITTED_ANSWER).strip()


def _library_version(root: Path) -> str:
    manifest = json.loads((root / "knowledge/store-manifest.json").read_text("utf-8"))
    return manifest.get("library_version") or ""


def build(tmp: Path, release: Build) -> tuple[dict[Path, Path], dict[str, str], str]:
    """Inputs and every stage, in `tmp`, twice, in `release`'s frozen environment:
    the whole pipeline, and the same inputs without `index`. Returns each built
    root by the directory it is committed to, each clone's `main` sha, and the
    store's own commit of the answer dated by its commit, which both runs make
    identically. Refuses a store whose manifest names another release."""
    shas = _sources(tmp)
    built = {}
    for target, parent, flags in (
        (release.store, tmp / "full", ()),
        (release.no_index, tmp / "bare", ("--no-index",)),
    ):
        root = parent / "store"
        root.mkdir(parents=True)
        _store_inputs(root, shas)
        _pipeline(tmp, root, release.requirements, *flags)
        stamped = _library_version(root)
        if stamped != release.version:
            raise subprocess.CalledProcessError(
                1, "pipeline", f"built by {stamped!r}, not {release.version}"
            )
        built[target] = root
    commits = {_answer_commit(root) for root in built.values()}
    if len(commits) != 1 or "" in commits:
        raise subprocess.CalledProcessError(1, "git log", f"answer commits differ: {commits}")
    return built, shas, commits.pop()


def _inputs_json(shas: dict[str, str], answer_commit: str) -> str:
    recorded = {"shas": dict(sorted(shas.items())), STORE_ANSWERS_COMMIT: answer_commit}
    return json.dumps(recorded, indent=1) + "\n"


def _clear(directory: Path) -> None:
    """Empty `directory` file by file, so a file the library no longer writes
    does not survive a regeneration."""
    if not directory.exists():
        return
    for path in sorted(directory.rglob("*"), reverse=True):
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()


def _export(root: Path, target: Path) -> None:
    _clear(target)
    for rel in _copied(root):
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / rel, destination)


def _differences(built: Path, committed: Path) -> list[str]:
    def listing(directory: Path) -> set[str]:
        if not directory.exists():
            return set()
        return {
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
        }

    ours, theirs = listing(built), listing(committed)
    differing = sorted(ours ^ theirs)
    for rel in sorted(ours & theirs):
        if (built / rel).read_bytes() != (committed / rel).read_bytes():
            differing.append(rel)
    return sorted(differing)


USAGE = "usage: generate.py [--check | --release <tag> [--out <dir>]]"


def _check(tmp: Path, release: Build) -> list[str]:
    """Build `release` in `tmp` and name each file that differs from what is
    committed, `inputs.json` included; print the interpreter and git beside the
    recorded ones when any does."""
    built, shas, answer_commit = build(tmp, release)
    differing = []
    for target, root in built.items():
        fresh = tmp / "out" / target.name
        _export(root, fresh)
        where = target.relative_to(HERE).as_posix()
        differing += [f"{where}/{rel}" for rel in _differences(fresh, target)]
    if not INPUTS.is_file() or INPUTS.read_text("utf-8") != _inputs_json(shas, answer_commit):
        differing.append(f"{INPUTS.name} (from {release.library})")
    for rel in differing:
        print(f"differs: {rel}")
    if differing:
        print(f"recorded interpreter: {_recorded(release.requirements, 'interpreter')}")
        print(f"this build's interpreter: {_running_interpreter(tmp, release.requirements)}")
        print(f"recorded git: {_recorded(release.requirements, 'git')}")
        print(f"this build's git: {_running_git()}")
    else:
        print(f"{release.name}store/ and {release.name}no-index/ reproduce from {release.library}")
    return differing


def _write_release(tmp: Path, release: Build) -> int:
    """Build `release` and write its two stores. A frozen release writes no
    `inputs.json`: its inputs are the main store's, so it refuses unless its clones
    and answer commit are the ones recorded there."""
    built, shas, answer_commit = build(tmp, release)
    recorded = _inputs_json(shas, answer_commit)
    if release is not MAIN and (not INPUTS.is_file() or INPUTS.read_text("utf-8") != recorded):
        print(f"generation failed, nothing written: {INPUTS.name} differs", file=sys.stderr)
        return 1
    for target, root in built.items():
        _export(root, target)
    if release is MAIN:
        INPUTS.write_text(recorded, encoding="utf-8")
    for target in built:
        print(f"wrote {target} from {release.library}")
    return 0


def _release(argv: list[str]) -> Build | None:
    """The release `--release <tag> [--out <dir>]` names, or None if it is not one
    of `RELEASES` or the arguments are not that shape."""
    if len(argv) not in (2, 4) or argv[0] != "--release":
        return None
    if len(argv) == 4 and argv[2] != "--out":
        return None
    version = argv[1].removeprefix("v")
    if version not in RELEASES:
        return None
    return frozen(version, Path(argv[3]).resolve() if len(argv) == 4 else None)


def main(argv: list[str]) -> int:
    check = argv == ["--check"]
    release = MAIN if not argv else _release(argv)
    if not check and release is None:
        print(USAGE, file=sys.stderr)
        print(f"frozen releases: {', '.join(RELEASES)}", file=sys.stderr)
        return 2
    if release is not None and not release.requirements.is_file():
        print(f"no frozen requirements at {release.requirements}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name).resolve()
        try:
            if not check:
                assert release is not None
                return _write_release(tmp, release)
            differing = []
            for index, each in enumerate([MAIN, *(frozen(version) for version in RELEASES)]):
                differing += _check(tmp / str(index), each)
        except subprocess.CalledProcessError as failed:
            print(f"generation failed, nothing written: {failed}", file=sys.stderr)
            return 1
    return 1 if differing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
