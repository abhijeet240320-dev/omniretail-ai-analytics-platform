"""Integrations with tools outside this repository.

Each module here answers one question about the *host environment* rather than
about the data: can this machine talk to X, and if not, exactly what is missing.

The point is that those answers are measured instead of asserted. A README that
says "Power BI Desktop is required" stops being true the day someone installs
it, and a reader who trusts it stops trying. `powerbi_availability` probes the
machine, reports what it found, and the documentation is required to agree with
it.
"""
