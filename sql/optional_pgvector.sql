-- optional_pgvector.sql
--
-- Purpose:
-- - Enable pgvector extension for optional vector features (casebook embeddings).
-- - Keep `sql/minimal_schema.sql` runnable even when pgvector isn't installed.
--
-- Safe to run multiple times.

CREATE EXTENSION IF NOT EXISTS vector;

