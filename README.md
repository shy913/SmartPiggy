# bill

A small example website built with Flask, served on **port 8001**.

## Run it

```bash
uv run bill
```

Then open <http://127.0.0.1:8001>.

`HOST` and `PORT` override the defaults (`0.0.0.0:8001`); set `FLASK_DEBUG=1` for
the reloader and debugger.

## Routes

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/` | Home page with a live note count |
| GET | `/notes` | List every note |
| POST | `/notes` | Add a note, then redirect (PRG) |
| POST | `/notes/<id>/delete` | Delete one note |
| GET | `/upload` | Upload form and recent files |
| POST | `/upload` | Parse a CSV/TSV/XLSX (multipart), then redirect |
| GET | `/uploads/<id>` | Preview of one uploaded file |
| GET | `/api/notes` | Notes as JSON |
| POST | `/api/notes` | Add a note from JSON |
| GET | `/healthz` | Liveness check |

```bash
curl -s localhost:8001/api/notes
curl -s -X POST localhost:8001/api/notes \
  -H 'content-type: application/json' -d '{"body":"hello from curl"}'
```

## Uploading a file

Open <http://127.0.0.1:8001/upload> and pick a `.csv`, `.tsv`, `.txt`, `.xlsx`
or `.xlsm` file (up to 5 MB), or post one straight to the endpoint:

```bash
curl -F file=@sales.xlsx localhost:8001/upload
```

What happens to it:

- Parsed entirely in memory — nothing is written to disk, nothing is uploaded
  anywhere else. The last 8 files stay in memory so their previews keep working.
- CSVs get encoding (`utf-8-sig`/`utf-8`/`cp1252`/`latin-1`) and delimiter
  (`,`/`;`/tab/`|`) detection; blank headers become `Column N` and blanks are
  padded so every row lines up.
- Excel workbooks are read with `openpyxl` — every sheet, values only, so
  formulas show their cached result. Boolean and date cells are formatted the
  way a spreadsheet shows them.
- Preview is capped at 500 rows per sheet (the header notes when a file was
  truncated) and 10 sheets.
- Legacy `.xls`, oversized files, and anything unparseable are rejected with a
  readable message instead of a stack trace.

## Response headers

Every HTML response carries `Content-Disposition: inline` and
`X-Content-Type-Options: nosniff`. Without them a browser or an intermediary
proxy may decide that an extensionless path like `/upload` is a file and offer
to *download* it instead of rendering the page. A future route that genuinely
wants to send a file sets `attachment` itself; the hook only supplies the
default.

## Layout

```
src/bill/__init__.py      app factory, routes, main()
src/bill/tabular.py       CSV/Excel parsing into a small table model
src/bill/templates/       Jinja templates sharing base.html
src/bill/static/style.css hand-written stylesheet
```

Notes and uploads are kept in plain in-memory structures, so they reset on
restart — this is a demo, not a database.
