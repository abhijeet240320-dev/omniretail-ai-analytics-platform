# Power BI

## What is in this folder, and what is not

| File | What it is |
|---|---|
| `measures.dax` | 70 DAX measures as plain text, each documented with its purpose and the grain it assumes |
| `data_model.md` | The star schema Power BI consumes, and why it is shaped that way |

**There is no `.pbix` here, and no `screenshots/` folder.** Neither can be
produced by this repository, and it would be dishonest to imply otherwise:

## Can a .pbix be built on this machine? Ask it.

```
GET /api/integrations/powerbi/status        (requires admin:read)
```

or

```bash
PYTHONPATH=backend .venv/Scripts/python.exe -c \
  "from app.integrations.powerbi_availability import probe; import json; \
   print(json.dumps(probe().as_dict(), indent=2))"
```

The probe checks the three standard install paths, the `App Paths` registry key
and `PATH`. It is not a claim in a README that can go stale; it is a
measurement, and `test_the_readme_states_the_measured_availability` fails the
build if this file and the probe disagree.

**As of the last run on this machine:**

| | |
|---|---|
| Power BI Desktop | **not installed** (3 install locations + registry key checked) |
| pbi-tools / pbir | not on PATH |
| Can produce a `.pbix` | **no** |

The reason is structural, not a missing dependency that could be pip-installed.
A `.pbix` is an OPC zip package whose `DataModel` part is a proprietary,
undocumented, versioned Analysis Services backup written by the Tabular Object
Model. There is no open-source writer for it. The three real routes are:

1. **Power BI Desktop**, driven by a person.
2. **pbi-tools or Tabular Editor against a Power BI Service XMLA endpoint**,
   which needs a Fabric tenant and a credential this project does not have.
3. **pbir**, which needs Desktop to produce the binary model.

So: no `.pbix` is generated here, and nothing in this repository is a text file
renamed to look like one. `test_no_artefact_claims_to_be_a_pbix` walks the whole
tree and fails if a `.pbix` ever appears.

Screenshots are refused for the same reason as a fabricated result: a rendered
picture of a report that was never rendered is an invented artefact.

## What is delivered instead

Everything needed to build the report in about an hour, **plus automated proof
that the numbers in it are true against the live database.**

## Building the report

1. **Get the data.** Power BI Desktop, *Get Data* > *PostgreSQL*, connecting
   as `omniretail_readonly`. That role holds `SELECT` and nothing else, so the
   refresh path cannot write even if a model is wrong. Import the four fact
   tables and the `Source` dimension described in `data_model.md`.
2. **Build the model** from `data_model.md`. The one rule that matters: no
   relationship may connect two sources. There is no foreign key in this
   database that crosses a schema boundary, and the Power BI model must not
   invent one.
3. **Paste the measures** from `measures.dax`, in file order. They are grouped by
   source; paste all four groups.
4. **Verify before publishing.**

   ```bash
   .venv/Scripts/python.exe -m pytest backend/tests/test_powerbi_artifacts.py -v
   ```

   Then, in Power BI, compare the model against the same queries:
   `backend/scripts/sql/run_analytics.py` writes
   `data/processed/_sql_results.json`, and every measure comment quotes a figure
   from it. If a measure and that file disagree, the file is right.

5. **Refresh schedule.** The PostgreSQL cluster on this machine is local and
   stops when the machine stops. Either leave it running or move the database to
   a host Power BI Service can reach, and set the gateway accordingly. The
   read-only role means a refresh can never damage the source.

## How these artefacts are verified without Power BI

There is no DAX engine here, so `measures.dax` cannot be executed. What can be
done - and is, on every test run - is to check every *claim* the files make
against the live database:

* every row count in `data_model.md` matches the live table
* every figure quoted in a DAX comment is re-derived from a real query
* every table and column a measure references exists in the live schema
  (this catches the commonest DAX failure: a renamed column)
* no measure sums across sources - asserted by parsing the measure bodies
* the 801 / 2,500 dunnhumby population split is described correctly, because
  getting that backwards is the single easiest way to make a dashboard lie
* the artefact files stay ASCII, because a `.dax` with a smart quote or a
  non-breaking space fails to paste into Power BI with an unhelpful error

The limitation these tests cannot cover - **DAX syntax** - is stated here and in
the test module's docstring rather than left implied. A measure that references
only columns that exist can still fail to compile. Compile it in Desktop.

## Why four fact tables and not one

Because that is what the data is. Olist is a Brazilian marketplace, DataCo is a
supply chain, dunnhumby is a US grocery panel and M5 is Walmart daily sales:
four unrelated companies with no shared key space and no common definition of a
sale. A single fact table with a `source` column would let a slicer produce a
"total revenue" that sums a marketplace's payments, a supply chain's order
lines and Walmart's unit counts - a number with no referent that renders
exactly like a real one.

The consequence reaches past layout. Every measure is per source with the
source always in context, and `measures.dax` contains no all-source total.