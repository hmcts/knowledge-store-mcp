# Deployable artefacts

Six things get released. Three repositories own them. The asker's machine only ever holds three of them, and the server reads the store from local disk with no network access.

![The library, generic plugin and fixture store come from knowledge-store-builder; the MCP server comes from knowledge-store-mcp; store data and the store plugin come from each store repository. On the asker's runtime the store plugin launches the MCP server, which reads a local clone of the store and talks to the agent over stdio, with no network or credentials.](architecture.svg)

Teal marks what the MCP work adds. The dashed teal line is the trust boundary: the server sees only the clone and the agent. Every front door, Claude Code included, answers only through the server. The `knowledge-store` skill keeps cloning, freshness, repair and recording the clone's location, but its direct read of the store is retired and it no longer answers by reading files.

## Who releases what

| Artefact | Released by | Versioned by | Consumed by |
|---|---|---|---|
| ① Library | The maintainer cuts a GitHub release | Git tag (hatch-vcs) | Store builds, pinned in the store's lock file |
| ② Generic plugin | Merging to `main` | Nothing: installs from `main` | Builders, through the marketplace |
| ③ Fixture store | Each ① release ([knowledge-store-builder#456](https://github.com/hmcts/knowledge-store-builder/issues/456)) | The ① tag | ⑥'s contract tests |
| ④ Store data | The store build pushes to its default branch | Commit, with the format declared in `store-manifest.json` ([#454](https://github.com/hmcts/knowledge-store-builder/issues/454)) | ⑥, through the asker's clone |
| ⑤ Store plugin | The store repository's marketplace entry | Its default branch | Askers |
| ⑥ MCP server | A tag on this repository | Git tag | ⑤'s `mcpServers` entry, run with `uvx` at that tag (**decided**, 2026-10-22) |

## Decided

- **Delivery of ⑥.** Running `uvx --from git+https://github.com/hmcts/knowledge-store-mcp@<release> knowledge-store-mcp --store-id <id>` keeps the asker free of a Python install step. It needs `uv` on the host. The owner confirmed it on 2026-10-22, and the spec's [Installation](superpowers/specs/2026-10-20-knowledge-store-mcp-design.md#installation) section sets it out.
