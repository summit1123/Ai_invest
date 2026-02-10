from __future__ import annotations

import unittest
from pathlib import Path


class SqlSchemaTests(unittest.TestCase):
    def test_minimal_schema_does_not_require_pgvector(self) -> None:
        text = Path("sql/minimal_schema.sql").read_text(encoding="utf-8")
        self.assertNotIn("CREATE EXTENSION IF NOT EXISTS vector", text)

    def test_optional_pgvector_schema_exists(self) -> None:
        text = Path("sql/optional_pgvector.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", text)

    def test_schema_v1_1_contains_core_tables(self) -> None:
        text = Path("sql/schema_v1_1.sql").read_text(encoding="utf-8")
        for token in (
            "CREATE TABLE IF NOT EXISTS events",
            "CREATE TABLE IF NOT EXISTS decisions",
            "CREATE TABLE IF NOT EXISTS orders",
            "CREATE TABLE IF NOT EXISTS fills",
            "CREATE TABLE IF NOT EXISTS reconciliation_checks",
            "CREATE TABLE IF NOT EXISTS ledger_entries",
            "CREATE TABLE IF NOT EXISTS decision_outcomes",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
