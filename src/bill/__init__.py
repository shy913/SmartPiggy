"""A small example website built with Flask.

Run it with:

    uv run bill

It serves on http://127.0.0.1:8001 by default; override with PORT / HOST.
"""

from __future__ import annotations

import itertools
import os
from datetime import datetime, timezone

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

__all__ = ["create_app", "main"]

# Notes live in memory, so they reset when the server restarts.
_started_at = datetime.now(timezone.utc)
_ids = itertools.count(1)
_notes: list[dict] = []

SEED_NOTES = (
    "Welcome to the demo — this note lives in memory on the server.",
    "Add one below: the form redirects, so refreshing won't resubmit it.",
    "GET /api/notes returns the same list as JSON.",
)


def add_note(body: str) -> dict:
    note = {
        "id": next(_ids),
        "body": body,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _notes.append(note)
    return note


def delete_note(note_id: int) -> bool:
    """Remove a note; return False if there was nothing to remove."""
    remaining = [n for n in _notes if n["id"] != note_id]
    if len(remaining) == len(_notes):
        return False
    _notes[:] = remaining
    return True


def notes_newest_first() -> list[dict]:
    """A plain list, so the template and the JSON API agree."""
    return list(reversed(_notes))


def uptime_seconds() -> int:
    return int((datetime.now(timezone.utc) - _started_at).total_seconds())


def create_app() -> Flask:
    app = Flask(__name__)

    if not _notes:
        for body in SEED_NOTES:
            add_note(body)

    @app.route("/")
    def index():
        return render_template(
            "index.html", note_count=len(_notes), uptime=uptime_seconds()
        )

    @app.route("/notes", methods=["GET", "POST"])
    def notes():
        if request.method == "POST":
            body = (request.form.get("body") or "").strip()
            if not body:
                return (
                    render_template(
                        "notes.html",
                        notes=notes_newest_first(),
                        error="A note needs some text.",
                    ),
                    400,
                )
            add_note(body)
            # Post/Redirect/Get, so reloading doesn't resubmit the form.
            return redirect(url_for("notes"))
        return render_template("notes.html", notes=notes_newest_first(), error=None)

    @app.post("/notes/<int:note_id>/delete")
    def remove_note(note_id: int):
        if not delete_note(note_id):
            abort(404)
        return redirect(url_for("notes"))

    @app.route("/about")
    def about():
        return render_template("about.html", started=_started_at)

    @app.route("/healthz")
    def healthz():
        return jsonify(status="ok", uptime_seconds=uptime_seconds())

    @app.get("/api/notes")
    def api_list_notes():
        return jsonify(count=len(_notes), notes=notes_newest_first())

    @app.post("/api/notes")
    def api_create_note():
        payload = request.get_json(silent=True) or {}
        body = (payload.get("body") or "").strip()
        if not body:
            return jsonify(error="'body' is required"), 400
        return jsonify(note=add_note(body)), 201

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("404.html"), 404

    return app


def main() -> None:
    app = create_app()
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8001")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )


if __name__ == "__main__":
    main()
