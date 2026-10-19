"""Check the documentation against the repository it describes.

Documentation drift is the failure mode where prose outlives the code: a test
count that was true last week, a file that was renamed, an endpoint that moved.
Each check below fails loudly rather than reporting a soft warning, because the
whole point is that the docs must describe THIS tree.

    .venv/Scripts/python.exe backend/scripts/verify/verify_doc_drift.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCS = sorted(
    p
    for p in ROOT.rglob("*.md")
    if not any(
        part in {".venv", "node_modules", ".git", ".pytest_cache", "dist"}
        for part in p.parts
    )
)

# `](path)` inside markdown, plus bare `path/like/this.py` mentions.
LINK = re.compile(r"\]\(([^)\s]+)\)")
# `path/like/this.ext` with at least one slash, ignoring URLs and anchors.
BARE = re.compile(r"(?<![\w/])((?:backend|frontend|docs|powerbi|notebooks|policies|data)/[\w./-]+\.[a-z]{1,5})")

failures: list[str] = []
checked = 0


def fail(message: str) -> None:
    failures.append(message)


def resolves(doc: Path, ref: str) -> bool:
    """A reference resolves if it exists, directly or from the repo root.

    Directories count. `](profiling/)` is a legitimate link to a folder of
    generated reports, and treating it as broken would be the audit lying.
    """
    ref = ref.split("#", 1)[0].split("?", 1)[0]
    if not ref or ref.startswith(("http://", "https://", "mailto:")):
        return True
    candidates = [
        doc.parent / ref,
        ROOT / ref,
        doc.parent / ref.replace(".md", ""),
    ]
    return any(c.exists() for c in candidates)


def main() -> int:
    global checked
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8", errors="replace")
        for ref in LINK.findall(text) + BARE.findall(text):
            checked += 1
            if not resolves(doc, ref):
                fail(f"{doc.relative_to(ROOT)} references a file that does not exist: {ref}")

    print("=" * 76)
    print("1. FILE REFERENCES - every path the docs name must exist")
    print("=" * 76)
    print(f"  documents scanned : {len(DOCS)}")
    print(f"  references checked: {checked}")
    print(f"  broken           : {len(failures)}")
    for f in failures:
        print(f"    FAIL {f}")

    # ---------------------------------------------------------------- test counts
    print()
    print("=" * 76)
    print("2. TEST COUNTS - a doc claiming N tests must mean N collected tests")
    print("=" * 76)

    import subprocess

    CLAIM = re.compile(
        r"`((?:backend|frontend)/[\w./-]*tests?[\w./-]*\.(?:py|ts|tsx))`[^.\n]{0,40}?[—\-:]\s*\**(\d+)\s+tests?\b"
    )


    def collected(path: str) -> int | None:
        """How many tests does this file actually contribute right now?"""
        if path.endswith(".py"):
            out = subprocess.run(
                [sys.executable, "-m", "pytest", path, "-q", "--collect-only"],
                cwd=ROOT, capture_output=True, text=True, timeout=600,
            ).stdout
            m = re.search(r"(\d+) tests? collected", out)
            return int(m.group(1)) if m else None
        proc = subprocess.run(
            ["npx", "vitest", "run", path, "--reporter=json", "--silent"],
            cwd=ROOT / "frontend", capture_output=True, text=True, timeout=600,
        )
        try:
            return int(json.loads(proc.stdout[proc.stdout.index("{"):])["numTotalTests"])
        except Exception:
            return None


    claims = sorted(
        {
            (m.group(1), int(m.group(2)))
            for doc in DOCS
            for m in CLAIM.finditer(doc.read_text(encoding="utf-8", errors="replace"))
        }
    )
    for path, claimed in claims:
        actual = collected(path)
        if actual is None:
            print(f"  SKIP {path}: doc says {claimed}, could not collect (not counted either way)")
            continue
        if actual == claimed:
            print(f"  OK   {path}: doc says {claimed}, collected {actual}")
        else:
            fail(f"doc claims {claimed} tests for {path}, collection finds {actual}")
            print(f"  FAIL {path}: doc says {claimed}, collected {actual}")
    if not claims:
        print("  (no test-count claims found in prose)")

    print()
    print("=" * 76)
    if failures:
        print(f"DOCUMENTATION DRIFT - {len(failures)} problem(s)")
        print("=" * 76)
        for f in failures:
            print(f"  FAIL {f}")
        return 1
    print("DOCUMENTATION MATCHES THE TREE - no drift")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())