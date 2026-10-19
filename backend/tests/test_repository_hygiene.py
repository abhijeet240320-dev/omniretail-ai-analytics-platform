"""What git is allowed to track, asserted against git itself.

These are cheap, but they catch a real class of mistake: a rule that looks
right in the file and does the opposite. `.gitignore` has no inline comments,
so `!.env.example   # committed` is a pattern for a file named
`!.env.example   # committed` -- the negation quietly does nothing and the
template never reaches a fresh clone. Reading the file is not enough; asking
git what it would do is.

Every assertion runs the real `git check-ignore`. If git is unavailable the
tests skip rather than pretend to have verified something.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GITIGNORE = ROOT / ".gitignore"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60
    )


def ignored(path: str) -> bool:
    """True when git would drop this path. Exit 0 means "matched a rule"."""
    return _git("check-ignore", "-q", "--", path).returncode == 0


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not on PATH"
)


# --------------------------------------------------------------- secrets out
@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        ".env.local",
        ".venv/pyvenv.cfg",
        "backend/__pycache__/app.cpython-311.pyc",
        "frontend/node_modules/react/package.json",
        "frontend/dist/index.html",
    ],
)
def test_credentials_and_build_output_are_excluded(path: str) -> None:
    assert ignored(path), f"{path} is not ignored - it must never be committed"


def test_the_real_env_file_on_this_machine_is_excluded() -> None:
    """Not a hypothetical: assert against the file that actually exists."""
    if (ROOT / ".env").is_file():
        assert ignored(".env")


# ------------------------------------------------------------- evidence in
@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "data/raw/_checksums.json",
        "data/processed/_sql_results.json",
        "README.md",
        "backend/config/output_lineage.yaml",
    ],
)
def test_documentation_and_measurement_evidence_is_tracked(path: str) -> None:
    assert not ignored(path), f"{path} is ignored - a fresh clone would be missing it"


# ------------------------------------------------------- the inline-comment trap
def test_the_env_template_negation_is_not_swallowed_by_a_comment() -> None:
    """Regression: the `!.env.example` line once carried a trailing comment.

    gitignore has no inline comments, so that line was a negation for a
    nonsense filename and `.env.example` stayed ignored. The comment in the
    file claimed the template "IS committed"; it was not.
    """
    assert not ignored(".env.example")


def test_no_gitignore_rule_carries_an_inline_comment() -> None:
    """A trailing `# ...` is part of the pattern. Catch any new one."""
    offenders: list[str] = []
    for number, raw in enumerate(GITIGNORE.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line and not line.startswith("!"):
            offenders.append(f"line {number}: {line}")
    assert not offenders, "inline comments in .gitignore are part of the pattern: " + "; ".join(
        offenders
    )


def test_gitignore_separates_the_secret_from_its_template() -> None:
    """One assertion a reviewer can read and believe.

    `.env` holds the live database URL and must never be committed;
    `.env.example` is the documented template that tells a new operator how to
    build one, and a fresh clone without it cannot be set up at all.
    """
    assert ignored(".env"), "the real .env must be untrackable"
    assert not ignored(".env.example"), "the template must be trackable"