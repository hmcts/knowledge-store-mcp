-- The query index schema 1.0, copied verbatim from the `DDL` constant in
-- hmcts/knowledge-store-builder src/knowledgestore/store_index.py at commit
-- a839a4e18791d77d46f46999c8b0c33d1b13311b (#457, branch feat/query-index).
-- Do not edit the statements below: they are the library's, and the synthetic
-- store is only useful while it is built with the schema the library writes.
-- Task 10 replaces this copy with the library's released fixture.

CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID;
CREATE TABLE repos(k INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE files(k INTEGER PRIMARY KEY, repo INTEGER NOT NULL REFERENCES repos(k),
                   path TEXT NOT NULL, UNIQUE(repo, path));
CREATE TABLE relations(k INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE nodes(k INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, label TEXT,
                   kind TEXT NOT NULL, file INTEGER NOT NULL REFERENCES files(k),
                   url TEXT, community INTEGER);
CREATE TABLE edges(source INTEGER NOT NULL, relation INTEGER NOT NULL, target INTEGER NOT NULL,
                   PRIMARY KEY(source, relation, target)) WITHOUT ROWID;
CREATE INDEX edges_in ON edges(target, relation, source);
CREATE TABLE tickets(file INTEGER NOT NULL REFERENCES files(k), ticket TEXT NOT NULL,
                     count INTEGER NOT NULL, landed INTEGER NOT NULL,
                     PRIMARY KEY(file, ticket)) WITHOUT ROWID;
CREATE INDEX tickets_by_id ON tickets(ticket, landed, file);
CREATE VIRTUAL TABLE labels USING fts5(label, content='nodes', content_rowid='k',
                                       tokenize='unicode61');
