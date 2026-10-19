"""The catalogue of analytics queries.

WHY THIS EXISTS RATHER THAN DUPLICATING THE SQL
----------------------------------------------
The 41 queries already exist as `-- [query: name]` blocks in `backend/sql/`.
The runner parses them, `docs/sql_analytics.md` documents them, and the
documentation tests validate them against `_sql_results.json`.

If the API re-declared any of that, there would be a second source of truth, and
the failure mode would be the worst kind: the API would happily serve a query
that no longer matches the one in the documentation, and nothing would complain.

So this module IMPORTS the runner's parser rather than reimplementing it. There
is one parser, one set of .sql files, and one name for each query.

WHAT THE API WILL AND WILL NOT DO WITH THEM
-------------------------------------------
It will execute them read-only, paginated, against the real database.

It will not let a caller supply SQL. That decision belongs to the NL-to-SQL
phase, and it needs a validator, an allowlist and a human-readable rejection
path - none of which exist yet. Exposing an "execute this SQL" endpoint now
would mean shipping the dangerous half of a feature whose safety half is still
a TODO, and the read-only role would be the only thing standing between a
prompt injection and the database.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = REPO_ROOT / "backend" / "sql"

if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

# The runner is a script rather than a package, but importing it is still
# better than copying its MARKER regex: a change to the query-file format then
# has exactly one place to be made.
_runner_path = REPO_ROOT / "backend" / "scripts" / "sql" / "run_analytics.py"
_spec = importlib.util.spec_from_file_location("_omniretail_analytics_runner", _runner_path)
assert _spec and _spec.loader, f"cannot load the analytics runner from {_runner_path}"
_runner = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _runner
_spec.loader.exec_module(_runner)

# 00_bootstrap is cluster DDL, not analytics.
SOURCES = tuple(_runner.SOURCES)


@dataclass(frozen=True)
class CataloguedQuery:
    """One query, as the API sees it."""

    name: str
    source: str
    question: str
    sql: str
    file: str

    @property
    def qualified_name(self) -> str:
        """`olist.review_coverage_and_score` - unique across all four sources.

        Bare names are unique in practice, but the API accepts the qualified form
        so a future second query file per source cannot create an ambiguity that
        silently resolves to the wrong dataset.
        """
        return f"{self.source}.{self.name}"


@lru_cache(maxsize=1)
def load_catalogue() -> dict[str, CataloguedQuery]:
    """Parse every analytics query, keyed by qualified name.

    Cached because the .sql files do not change within a process. `cache_clear`
    exists for tests that rewrite them.
    """
    catalogue: dict[str, CataloguedQuery] = {}
    for source in SOURCES:
        folder = SQL_DIR / source
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.sql")):
            for block in _runner.parse_file(path, source):
                entry = CataloguedQuery(
                    name=block.name,
                    source=block.source,
                    question=block.question,
                    sql=block.sql,
                    file=block.relative,
                )
                if entry.qualified_name in catalogue:
                    raise ValueError(
                        f"duplicate query name {entry.qualified_name!r} in "
                        f"{entry.file}; query names must be unique"
                    )
                catalogue[entry.qualified_name] = entry
    return catalogue


def resolve(name: str) -> CataloguedQuery | None:
    """Look a query up by bare or qualified name.

    Bare names resolve only when unambiguous. `orders` existing in two sources
    should be an error the caller sees, not a coin flip.
    """
    catalogue = load_catalogue()
    if name in catalogue:
        return catalogue[name]

    matches = [q for q in catalogue.values() if q.name == name]
    if len(matches) == 1:
        return matches[0]
    return None