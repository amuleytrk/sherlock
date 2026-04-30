"""Tests for the MSSQL/PostgreSQL system tagging in the indexer."""
from __future__ import annotations

from pathlib import Path

from indexer.run import _system_for


def test_designs_postgres_dir_tagged_postgres():
    p = Path("/Users/x/plans/work/designs/postgres/pgSystemFlow.md")
    assert _system_for(p) == "postgres"


def test_postgresql_in_filename_tagged_postgres():
    assert _system_for(Path("/x/plans/work/user-docs/postgresqlTransition.md")) == "postgres"


def test_postgres_device_mgmt_apis_tagged_postgres():
    p = Path("/x/plans/work/api-specs/postgresDeviceMgmtApis.md")
    assert _system_for(p) == "postgres"


def test_pg_system_prefix_tagged_postgres():
    assert _system_for(Path("/x/plans/work/designs/platform/pgSystemFlow.md")) == "postgres"


def test_data_migration_pg_tagged_postgres():
    assert _system_for(Path("/x/plans/work/designs/postgres/dataMigrationPg.md")) == "postgres"


def test_general_design_doc_tagged_both():
    """General system docs should NOT be tagged postgres just because they
    mention PG once. Only explicit pg-marked paths get filtered."""
    assert _system_for(Path("/x/plans/work/designs/platform/systemFlow.md")) == "both"


def test_repo_code_tagged_both():
    """Service code is shared regardless of DB era; tag it both so MSSQL
    mode still surfaces it."""
    p = Path("/x/repos/multi-tenant-core-services/ingress-service/repository/database/sqlOperations.js")
    assert _system_for(p) == "both"


def test_mssql_named_file_not_tagged_postgres():
    """An mssql-named file in /designs/postgres/ would be a misclassification —
    but in practice no such file exists. Just verifying the heuristic doesn't
    get fooled by superficial substrings."""
    assert _system_for(Path("/x/plans/work/designs/platform/mssqlConnectionPool.md")) == "both"
