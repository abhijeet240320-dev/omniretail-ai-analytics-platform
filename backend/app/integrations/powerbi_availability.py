"""Can this machine produce a real `.pbix`? Ask, don't assume.

WHY THIS IS CODE AND NOT A PARAGRAPH
------------------------------------
"Power BI Desktop is not installed" is the kind of claim that goes stale the
moment someone installs it, and a stale claim in a README is worse than no
claim: it tells a reader to give up on a step that now works. So the
environment is probed, and `powerbi/README.md` and `docs/powerbi.md` are both
required to agree with what this module reports at runtime.

HOW A `.pbix` IS ACTUALLY BUILT
-------------------------------
A `.pbix` is an OPC zip package whose `DataModel` part is an **Analysis Services
backup** - a proprietary, undocumented, versioned binary format written by the
Tabular Object Model. There is no open-source writer for it. The options are:

  1. Power BI Desktop, driven by a human (or by its UI automation).
  2. `pbir`/`Tabular Editor` against a **Power BI Service** XMLA endpoint,
     which needs a Fabric/Power BI tenant and a credential this project does
     not have.
  3. `pbi-tools`, which extracts and compiles `.pbix` files but still needs a
     Desktop install to produce the binary model.

None of the three is reachable here. So the honest deliverable is everything
that can be verified without the binary: the DAX, the model documentation, and
machine checks that the DAX and the model agree with the live database. Those
checks exist in `backend/tests/test_powerbi_artifacts.py` and run in CI.

WHAT IS NOT DONE HERE, AND WILL NOT BE FAKED
---------------------------------------------
No `.pbix` is generated, and nothing in this repository is a renamed text file
pretending to be one. `test_no_artefact_claims_to_be_a_pbix` enforces that.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]

# Windows install paths for Power BI Desktop. Checked by explicit path as well
# as PATH, because Desktop registers `msedge`/its own folder but historically
# has not always been on PATH for a non-interactive session.
_WINDOWS_INSTALL_PATHS = (
    r"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe",
    r"C:\Program Files (x86)\Microsoft Power BI Desktop\bin\PBIDesktop.exe",
    r"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktopStore.exe",
)

# Names on PATH, across platforms. pbi-tools is the open-source alternative and
# counts as "available" for the purpose of *building* a model, so it is probed
# separately and reported separately.
_DESKTOP_EXECUTABLES = ("PBIDesktop", "PBIDesktopStore", "powerbi")
_TOOLING_EXECUTABLES = ("pbi-tools", "pbir", "pbi-tools.exe")


@dataclass(frozen=True)
class PowerBiAvailability:
    """The measured answer to "can this machine write a `.pbix`?"."""

    desktop_available: bool
    desktop_paths: list[str] = field(default_factory=list)
    desktop_detail: str = ""
    tooling_available: bool = False
    tooling_found: list[str] = field(default_factory=list)
    tooling_detail: str = ""
    can_produce_pbix: bool = False
    pbix_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "desktop_available": self.desktop_available,
            "desktop_paths": self.desktop_paths,
            "desktop_detail": self.desktop_detail,
            "tooling_available": self.tooling_available,
            "tooling_found": self.tooling_found,
            "tooling_detail": self.tooling_detail,
            "can_produce_pbix": self.can_produce_pbix,
            "pbix_reason": self.pbix_reason,
        }


def _which(*names: str) -> list[str]:
    found: list[str] = []
    for name in names:
        path = shutil.which(name)
        if path:
            found.append(path)
    return found


def _windows_desktop() -> tuple[list[str], str]:
    """Explicit install paths, plus the registry key Desktop actually writes."""
    hits: list[str] = []
    checked: list[str] = []

    for candidate in _WINDOWS_INSTALL_PATHS:
        checked.append(candidate)
        if Path(candidate).is_file():
            hits.append(candidate)

    if os.name == "nt":
        try:
            import winreg

            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\PBIDesktop.exe"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "")
                if value:
                    checked.append(key_path)
                    if Path(value).is_file() and value not in hits:
                        hits.append(value)
        except Exception:
            # No registry, no key, or permission denied - all ordinary on a
            # machine without Power BI. Absence of the key is the answer.
            pass

    return hits, f"checked {len(checked)} install locations and the App Paths registry key"


def _probe_version(executable: str) -> str:
    """Ask the binary what version it is. Best effort; never raises."""
    for flag in ("-version", "--version", "/? "):
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [executable, flag],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except Exception:
            return "found, but would not report a version"
        blob = (result.stdout or "") + (result.stderr or "")
        for line in blob.splitlines():
            if "version" in line.lower() and line.strip():
                return line.strip()[:120]
    return "found, but would not report a version"


def probe() -> PowerBiAvailability:
    """Measure the environment. Cheap, read-only, safe to call at startup."""
    paths, desktop_detail = _windows_desktop()
    desktop_hits = list(paths) + _which(*_DESKTOP_EXECUTABLES)

    detail = desktop_detail
    if desktop_hits:
        detail = f"{desktop_detail}; found {_probe_version(desktop_hits[0])}"

    tooling_hits = _which(*_TOOLING_EXECUTABLES)

    desktop_available = bool(desktop_hits)
    tooling_available = bool(tooling_hits)

    if desktop_available:
        reason = ""
        can = True
    elif tooling_available:
        can = True
        reason = (
            "pbi-tools is present but compiling a .pbix still needs a "
            "Power BI Desktop install to produce the binary data model"
        )
    else:
        can = False
        reason = (
            "Power BI Desktop is not installed on this machine and pbi-tools "
            "is not available. A .pbix is an OPC package whose DataModel part "
            "is a proprietary Analysis Services backup; there is no open-source "
            "writer for it, so it cannot be produced here by any script."
        )

    return PowerBiAvailability(
        desktop_available=desktop_available,
        desktop_paths=sorted(set(desktop_hits)),
        desktop_detail=detail,
        tooling_available=tooling_available,
        tooling_found=sorted(set(tooling_hits)),
        tooling_detail=(
            f"pbi-tools found at {tooling_hits[0]}"
            if tooling_hits else "no pbi-tools or pbir on PATH"
        ),
        can_produce_pbix=can,
        pbix_reason=reason,
    )


@lru_cache(maxsize=1)
def availability() -> PowerBiAvailability:
    return probe()


def refresh() -> PowerBiAvailability:
    """Re-probe, bypassing the cache. Used by the CLI and by tests."""
    availability.cache_clear()
    return availability()


@lru_cache(maxsize=1)
def availability() -> PowerBiAvailability:
    return probe()


def refresh() -> PowerBiAvailability:
    """Re-probe, bypassing the cache. Used by the CLI and by tests."""
    availability.cache_clear()
    return availability()


__all__ = [
    "PowerBiAvailability",
    "availability",
    "probe",
    "refresh",
]
