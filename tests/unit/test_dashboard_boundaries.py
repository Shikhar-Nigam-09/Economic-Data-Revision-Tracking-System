"""Verify strict boundary isolation between dashboard/ and backend packages.

The Streamlit dashboard must communicate exclusively over HTTP via ApiClient.
It must NEVER import sqlalchemy, psycopg, alembic, or econ_monitor directly.
"""

import ast
from pathlib import Path

FORBIDDEN_MODULES = {
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "alembic",
    "econ_monitor",
}


def test_dashboard_does_not_import_backend_modules():
    """Scan all Python files in dashboard/ to ensure zero forbidden imports."""
    dashboard_dir = Path(__file__).resolve().parent.parent.parent / "dashboard"
    assert dashboard_dir.exists(), f"Dashboard directory {dashboard_dir} does not exist."

    py_files = list(dashboard_dir.rglob("*.py"))
    assert len(py_files) >= 10, f"Expected at least 10 dashboard files, found {len(py_files)}"

    violations = []

    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    if root_pkg in FORBIDDEN_MODULES:
                        violations.append(
                            f"{py_file.name}:{node.lineno} imports '{alias.name}' directly"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    if root_pkg in FORBIDDEN_MODULES:
                        violations.append(
                            f"{py_file.name}:{node.lineno} imports from '{node.module}'"
                        )

    assert not violations, (
        "Architectural boundary violated! Dashboard files must not import backend modules:\n"
        + "\n".join(violations)
    )
