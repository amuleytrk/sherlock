"""Tests for the system tagging in the indexer (PG-only post-cutover)."""
from __future__ import annotations

from pathlib import Path

from indexer.run import _system_for


def test_all_paths_tagged_postgres():
    """Post-cutover: _system_for always returns 'postgres' regardless of path.
    The corpus is PG-only; the dual-system toggle is gone."""
    paths = [
        Path("/Users/x/plans/work/designs/postgres/pgSystemFlow.md"),
        Path("/x/plans/work/user-docs/postgresqlTransition.md"),
        Path("/x/plans/work/api-specs/postgresDeviceMgmtApis.md"),
        Path("/x/plans/work/designs/platform/pgSystemFlow.md"),
        Path("/x/plans/work/designs/postgres/dataMigrationPg.md"),
        Path("/x/plans/work/designs/platform/systemFlow.md"),
        Path("/x/repos/multi-tenant-core-services/ingress-service/repository/database/sqlOperations.js"),
        Path("/x/plans/work/designs/platform/mssqlConnectionPool.md"),
    ]
    for p in paths:
        assert _system_for(p) == "postgres", f"expected 'postgres' for {p}"


def test_no_path_returns_mssql():
    """No path should ever return 'mssql' post-cutover."""
    paths = [
        Path("/x/plans/work/designs/platform/mssqlConnectionPool.md"),
        Path("/x/repos/some-service/mssql/queryHelper.ts"),
        Path("/x/docs/mssql_migration.md"),
    ]
    for p in paths:
        assert _system_for(p) != "mssql", f"got 'mssql' for {p} — should never happen post-cutover"
