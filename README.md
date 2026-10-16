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
| GET | `/api/notes` | Notes as JSON |
| POST | `/api/notes` | Add a note from JSON |
| GET | `/healthz` | Liveness check |

```bash
curl -s localhost:8001/api/notes
curl -s -X POST localhost:8001/api/notes \
  -H 'content-type: application/json' -d '{"body":"hello from curl"}'
```

## Layout

```
src/bill/__init__.py      app factory, routes, main()
src/bill/templates/       Jinja templates sharing base.html
src/bill/static/style.css hand-written stylesheet
```

Notes are kept in a plain in-memory list, so they reset on restart — this is a
demo, not a database.
