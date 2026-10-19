"""Phase 1 checkpoint — verify that this machine can run the project.

WHY THIS FILE EXISTS
--------------------
Analytics projects fail for boring reasons: the wrong Python interpreter, a
package that never installed, a typo in .env. This script checks all of that in
one place *before* we write any business logic, so when something breaks later
we know the environment is not the cause.

Run it with:   python scripts/check_setup.py

Exit code 0 means the Python side is ready. The database check is reported as a
warning only, because the database itself is not created until Phase 3.

NOTE ON OUTPUT: everything printed here is plain ASCII on purpose. The default
Windows console uses the cp1252 code page, which cannot encode characters like
the tick (U+2714) and so crashes with UnicodeEncodeError.
"""

from __future__ import annotations

import importlib
import sys
from importlib import metadata
from pathlib import Path

# Make sure we can import our own package when the script is run directly
# (python scripts/check_setup.py adds scripts/ to sys.path, not the project root).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MINIMUM_PYTHON = (3, 11)

# import name -> what we use it for (shown in the report)
REQUIRED_PACKAGES = {
    "pandas": "tabular data analysis",
    "numpy": "numeric arrays",
    "matplotlib": "charts",
    "sqlalchemy": "Python <-> database layer",
    "alembic": "database migrations",
    "psycopg": "PostgreSQL driver",
    "fastapi": "backend API framework",
    "uvicorn": "ASGI server",
    "pydantic": "data validation",
    "pydantic_settings": "settings from .env",
    "dotenv": "local .env loading",
    "sklearn": "machine learning",
    "statsmodels": "statistics / forecasting",
    "scipy": "statistical tests",
    "pytest": "test runner",
}

# Some import names differ from the PyPI package name, and a few packages do not
# expose __version__. This map lets us ask the installed-metadata index instead.
DISTRIBUTION_NAMES = {
    "dotenv": "python-dotenv",
    "sklearn": "scikit-learn",
    "pydantic_settings": "pydantic-settings",
}


def report(status: str, message: str) -> None:
    """Print one aligned result line."""
    print(f"  [{status:<5}] {message}")


def package_version(module_name: str, module: object) -> str:
    """Best-effort version string for an already-imported module."""
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    distribution = DISTRIBUTION_NAMES.get(module_name, module_name)
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "?"


def check_python() -> bool:
    version = sys.version_info
    pretty = f"{version.major}.{version.minor}.{version.micro}"
    if version[:2] >= MINIMUM_PYTHON:
        report("OK", f"Python {pretty} ({sys.executable})")
        return True
    report("FAIL", f"Python {pretty} is too old; need {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}+")
    return False


def check_packages() -> bool:
    missing: list[str] = []
    for module_name, purpose in REQUIRED_PACKAGES.items():
        try:
            module = importlib.import_module(module_name)
            report("OK", f"{module_name:<18} {package_version(module_name, module):<10} {purpose}")
        except ImportError:
            missing.append(module_name)
            report("FAIL", f"{module_name:<18} {'MISSING':<10} {purpose}")

    if missing:
        print()
        print("  Fix: are you inside the virtual environment?")
        print("       Git Bash  :  source .venv/Scripts/activate")
        print("       PowerShell:  .venv\\Scripts\\Activate.ps1")
        print("       Then      :  python -m pip install -r requirements.txt")
        return False
    return True


def check_settings() -> bool:
    try:
        from app.core.config import get_settings
    except Exception as exc:  # noqa: BLE001 - we want to show the real reason
        report("FAIL", f"could not import app.core.config: {exc}")
        return False

    settings = get_settings()
    report("OK", f"settings loaded (app_env={settings.app_env})")

    if (PROJECT_ROOT / ".env").is_file():
        report("OK", ".env file found (your real values are being used)")
    else:
        report("WARN", ".env not found - defaults from .env.example are used")
        print("         Create one with:  cp .env.example .env")

    # Show the DSN with the password masked, so nothing secret hits the screen.
    masked = settings.database_url.split("://")[0] + "://" + settings.database_url.split("://")[1].split("@")[1]
    report("OK", f"read/write DSN -> {masked}")
    return True


def check_database() -> bool | None:
    """Return True/False, or None when we cannot even try."""
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import SQLAlchemyError

        from app.core.config import get_settings
    except Exception:  # noqa: BLE001
        return None

    settings = get_settings()
    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version()")).scalar_one()
        report("OK", f"database reachable -> {str(version).split(',')[0]}")
        return True
    except SQLAlchemyError as exc:
        first_line = str(exc.orig or exc).splitlines()[0]
        report("WARN", f"database not usable yet: {first_line}")
        print("         Expected until Phase 3. The server answering at all is a good sign.")
        return False


def main() -> int:
    print()
    print("=" * 64)
    print("  OmniRetail - Phase 1 environment check")
    print("=" * 64)

    print("\nPython interpreter")
    python_ok = check_python()

    print("\nRequired packages")
    packages_ok = check_packages()

    print("\nApplication settings")
    settings_ok = check_settings()

    print("\nDatabase (optional in Phase 1)")
    check_database()

    all_ok = python_ok and packages_ok and settings_ok
    print()
    print("-" * 64)
    print("  RESULT:", "environment ready" if all_ok else "problems found")
    print("-" * 64)
    print()
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
