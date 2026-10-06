-- Backend-only schema. Never add studio to Supabase's exposed API schemas.
CREATE SCHEMA IF NOT EXISTS studio;
REVOKE ALL ON SCHEMA studio FROM PUBLIC, anon, authenticated;
CREATE TABLE IF NOT EXISTS studio.users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
    password TEXT NOT NULL, recovery TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS studio.invites (
    code TEXT PRIMARY KEY, expires DOUBLE PRECISION NOT NULL, used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS studio.jobs (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS studio.images (
    job_id TEXT NOT NULL, filename TEXT NOT NULL, body BYTEA NOT NULL,
    PRIMARY KEY(job_id, filename)
);
CREATE INDEX IF NOT EXISTS jobs_owner_idx ON studio.jobs(owner);
ALTER TABLE studio.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE studio.invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE studio.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE studio.images ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA studio FROM PUBLIC, anon, authenticated;
-- Existing application sessions enforce ownership in the Python backend.
-- Only the server's database connection may access these tables.
