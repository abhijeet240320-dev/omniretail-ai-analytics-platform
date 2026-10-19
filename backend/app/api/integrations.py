"""Host-environment integrations: what this machine can and cannot do.

Every route here answers a question about the machine rather than the data, and
every one of them is measured at request time instead of asserted in a README.

That distinction matters more than it sounds. "Power BI Desktop is not
installed" is a claim that becomes false the day somebody installs it, and a
reader who trusts a stale claim stops trying a step that now works. A probe
cannot go stale.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, Depends

from app.api.deps import require_admin

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

REPO_ROOT = Path(__file__).resolve().parents[3]
LINEAGE_YAML = REPO_ROOT / "backend" / "config" / "output_lineage.yaml"


@router.get(
    "/powerbi/status",
    summary="Whether a .pbix can be produced on this host, and why not if it cannot",
)
def powerbi_status(_principal=Depends(require_admin)) -> dict:
    """The measured answer to "can this machine write a Power BI report file?".

    `can_produce_pbix: false` is a normal, supported state - it is the state
    this project ships in - and `pbix_reason` says exactly what is missing and
    which three routes exist. Nothing here is a guess: the probe checks the
    install paths, the `App Paths` registry key and `PATH`.

    Admin-scoped because it describes the deployment environment, which is
    reconnaissance about the host rather than about the data.
    """
    from app.integrations.powerbi_availability import probe

    return probe().as_dict()


@router.get(
    "/lineage",
    summary="Every analytical output traced to its source, query, endpoint and page",
)
def lineage(
    source: str | None = None,
    frontend_route: str | None = None,
    _principal=Depends(require_admin),
) -> dict:
    """`source -> table -> grain -> SQL -> endpoint -> frontend route`.

    Served from `backend/config/output_lineage.yaml`, the same file
    `backend/tests/test_output_lineage.py` verifies against PostgreSQL, the
    query catalogue, the FastAPI routes and the frontend routes. It is exposed
    because lineage nobody can query is lineage nobody reads.

    Filters compose, so `?source=m5` gives every M5 output and
    `?frontend_route=forecast` gives everything behind one page.
    """
    payload = yaml.safe_load(LINEAGE_YAML.read_text(encoding="utf-8"))
    outputs = list(payload.get("outputs") or [])

    if source:
        outputs = [o for o in outputs if o.get("source") == source]
    if frontend_route:
        outputs = [o for o in outputs if o.get("frontend_route") == frontend_route]

    return {
        "outputs": outputs,
        "count": len(outputs),
        "artefacts": payload.get("artefacts") or [],
        "verified_by": "backend/tests/test_output_lineage.py",
        "chain": (
            "raw -> processing -> cleaning -> processed -> PostgreSQL "
            "-> SQL analytics -> API -> frontend / AI / Power BI"
        ),
        "documented_in": ["docs/data_lineage.md", "backend/config/output_lineage.yaml"],
    }
