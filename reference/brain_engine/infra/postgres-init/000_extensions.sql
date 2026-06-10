-- Bootstrap extensions for the local Brain Engine Postgres.
-- The k8s deployment relies on the CNPG cluster bootstrap to enable
-- these; in docker-compose we have to do it ourselves before any of
-- the numbered migrations run.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
