# Contract tests against the library

Moved from the repository README. Run the commands below from the repository root.

`tests/library_store/` holds two stores that knowledge-store-builder v0.19.0's own stages wrote, over a tiny invented estate (`example-org`: `alpha-lib`, `beta-app`, `delta-deploy`, `eta-portal`). `store/` has the query index; `no-index/` is the same inputs built without it. The `tests/test_library_*.py` tests call every tool against both and compare with values derived by hand from the inputs.

- **Regenerate** with `uv run python tests/library_store/generate.py`, then confirm the output reproduces with `uv run python tests/library_store/generate.py --check`. The generator needs `uv`, `git` and network access the first time.
- **Hand-written:** the inputs in `generate.py` (clone contents, commit messages, `config/`, agent-authored summaries and answers) and the tracker and GitHub answers in `tracker_responses.json` and `github_responses.json`. **Library output:** everything under `store/` and `no-index/`. Never edit those by hand.
- **Pins:** library v0.19.0 and graphify 0.9.93 in `generate.py`; every other package, and the partitioner, interpreter and git the stores were made with, in `requirements.txt`.
- **One stand-in:** `pipeline.py` rebinds the clock `minimum-standard-settings` reads, because v0.19.0 has no seam for it. This moves to the library's `now=` seam when v0.19.1 ships.
- **Coverage:** `tests/library_store/COVERAGE.md` is the claim of what is covered, and `test_library_coverage` fails when a tool, argument or cited test is missing from it.
- **CI** runs the tests over the committed output. It never runs the generator.
