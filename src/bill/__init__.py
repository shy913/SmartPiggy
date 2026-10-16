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

from .tabular import ACCEPT_ATTR, MAX_BYTES, Table, UploadError, parse_upload

__all__ = ["create_app", "main"]

# Notes and uploads live in memory, so they reset when the server restarts.
_started_at = datetime.now(timezone.utc)
_ids = itertools.count(1)
_upload_ids = itertools.count(1)
_notes: list[dict] = []
_uploads: dict[int, Table] = {}
RECENT_UPLOADS = 8

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


def recent_uploads() -> list[tuple[int, Table]]:
    """Newest upload first, capped for the listing."""
    ids = sorted(_uploads, reverse=True)[:RECENT_UPLOADS]
    return [(upload_id, _uploads[upload_id]) for upload_id in ids]


def store_upload(table: Table) -> int:
    upload_id = next(_upload_ids)
    _uploads[upload_id] = table
    return upload_id


def create_app() -> Flask:
    app = Flask(__name__)
    # Reject oversized bodies before they're read into memory.
    app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES

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

    @app.route("/upload", strict_slashes=False)
    def upload():
        return render_template(
            "upload.html",
            uploads=recent_uploads(),
            accept=ACCEPT_ATTR,
            max_mb=MAX_BYTES // (1024 * 1024),
            error=None,
        )

    @app.post("/upload")
    def receive_upload():
        file = request.files.get("file") or request.files.get("csv")

        def failed(message: str, status: int = 400):
            return (
                render_template(
                    "upload.html",
                    uploads=recent_uploads(),
                    accept=ACCEPT_ATTR,
                    max_mb=MAX_BYTES // (1024 * 1024),
                    error=message,
                ),
                status,
            )

        if file is None or not file.filename:
            return failed("Choose a CSV or Excel file first.")
        try:
            table = parse_upload(file.filename, file.read())
        except UploadError as error:
            return failed(str(error))
        # Post/Redirect/Get: the preview gets its own URL.
        return redirect(url_for("upload_detail", upload_id=store_upload(table)))

    @app.get("/uploads/<int:upload_id>")
    def upload_detail(upload_id: int):
        table = _uploads.get(upload_id)
        if table is None:
            abort(404)
        return render_template("upload_detail.html", upload_id=upload_id, table=table)

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

    @app.after_request
    def render_inline(response):
        """Leave no room for a client to mistake a page for a file.

        A browser only offers to *download* a response it can't render, so say
        explicitly that these are pages: an inline disposition, plus nosniff so
        nothing sniffs the body and reclassifies it. Routes that genuinely want
        to send a file can still set `attachment` themselves — this only fills
        in the default.
        """
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if response.mimetype == "text/html":
            response.headers.setdefault("Content-Disposition", "inline")
        return response

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("404.html"), 404

    @app.errorhandler(413)
    def too_large(_error):
        return (
            render_template(
                "413.html", max_mb=MAX_BYTES // (1024 * 1024), uploads=recent_uploads()
            ),
            413,
        )

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
