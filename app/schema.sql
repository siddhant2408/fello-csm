CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE IF NOT EXISTS accounts (
 tenant_id text NOT NULL, id text NOT NULL, name text NOT NULL, csm_id text NOT NULL,
 connected_sources jsonb NOT NULL, arr bigint NOT NULL, created_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,id));
CREATE TABLE IF NOT EXISTS aliases (
 tenant_id text NOT NULL, source text NOT NULL, source_key text NOT NULL, account_id text NOT NULL,
 PRIMARY KEY(tenant_id,source,source_key), FOREIGN KEY(tenant_id,account_id) REFERENCES accounts);
CREATE TABLE IF NOT EXISTS renewals (
 tenant_id text NOT NULL, id text NOT NULL, account_id text NOT NULL,
 due_at timestamptz NOT NULL, known_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,account_id) REFERENCES accounts);
CREATE TABLE IF NOT EXISTS events (
 tenant_id text NOT NULL, id text NOT NULL, account_id text, source text NOT NULL,
 source_key text NOT NULL, occurred_at timestamptz NOT NULL, received_at timestamptz NOT NULL,
 value double precision NOT NULL, quarantined boolean NOT NULL DEFAULT false,
 PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,account_id) REFERENCES accounts,
 CHECK(source IN ('salesforce','gmail','zendesk','product','stripe')));
CREATE INDEX IF NOT EXISTS events_account_clock ON events(tenant_id,account_id,occurred_at,received_at);
-- Source deliveries not yet received at the replay clock; the worker feeds them through ingestion.
CREATE TABLE IF NOT EXISTS inbox (
 tenant_id text NOT NULL, id text NOT NULL, source text NOT NULL, source_key text NOT NULL,
 occurred_at timestamptz NOT NULL, received_at timestamptz NOT NULL, value double precision NOT NULL,
 PRIMARY KEY(tenant_id,id));
CREATE INDEX IF NOT EXISTS inbox_due ON inbox(tenant_id,received_at,id);
CREATE TABLE IF NOT EXISTS replay (
 one boolean PRIMARY KEY DEFAULT true CHECK(one), started_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS models (
 tenant_id text NOT NULL, version text NOT NULL, artifact jsonb NOT NULL, created_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,version));
CREATE TABLE IF NOT EXISTS jobs (
 tenant_id text NOT NULL, id bigint GENERATED ALWAYS AS IDENTITY, event_id text NOT NULL,
 account_id text NOT NULL, as_of timestamptz NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','done','failed')),
 attempts integer NOT NULL DEFAULT 0, available_at timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz, last_error text, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,event_id),
 FOREIGN KEY(tenant_id,account_id) REFERENCES accounts);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(tenant_id,status,available_at);
CREATE TABLE IF NOT EXISTS snapshots (
 tenant_id text NOT NULL, id text NOT NULL, account_id text NOT NULL, renewal_id text,
 as_of timestamptz NOT NULL, model_version text NOT NULL, input_hash text NOT NULL,
 features jsonb NOT NULL, result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,account_id,as_of,model_version,input_hash),
 FOREIGN KEY(tenant_id,account_id) REFERENCES accounts,
 FOREIGN KEY(tenant_id,model_version) REFERENCES models(tenant_id,version));
CREATE INDEX IF NOT EXISTS snapshots_latest ON snapshots(tenant_id,account_id,as_of DESC,created_at DESC);
CREATE TABLE IF NOT EXISTS drafts (
 tenant_id text NOT NULL, snapshot_id text NOT NULL, body jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,snapshot_id),
 FOREIGN KEY(tenant_id,snapshot_id) REFERENCES snapshots(tenant_id,id));
CREATE TABLE IF NOT EXISTS reviews (
 tenant_id text NOT NULL, id bigint GENERATED ALWAYS AS IDENTITY, account_id text NOT NULL,
 csm_id text NOT NULL, reviewed_at timestamptz NOT NULL, PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,account_id) REFERENCES accounts);
CREATE INDEX IF NOT EXISTS reviews_recent ON reviews(tenant_id,account_id,reviewed_at);
CREATE TABLE IF NOT EXISTS users (
 username text PRIMARY KEY, id text NOT NULL UNIQUE, tenant_id text NOT NULL, csm_id text NOT NULL,
 password_hash text NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts (username text NOT NULL, attempted_at timestamptz DEFAULT now());
CREATE INDEX IF NOT EXISTS attempts_lookup ON login_attempts(username,attempted_at);
CREATE OR REPLACE FUNCTION public.fello_login(login_name text, login_password text)
RETURNS TABLE(id text,tenant_id text,csm_id text) LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog, public AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(login_name,1));
 IF (SELECT count(*) FROM public.login_attempts WHERE username=login_name AND attempted_at>now()-interval '15 minutes') >= 10 THEN
   RETURN;
 END IF;
 INSERT INTO public.login_attempts(username) VALUES(login_name);
 RETURN QUERY SELECT u.id,u.tenant_id,u.csm_id FROM public.users u
 WHERE u.username=login_name AND u.password_hash=public.crypt(login_password,u.password_hash);
END $$;
REVOKE ALL ON FUNCTION public.fello_login(text,text) FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
