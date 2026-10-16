"""Turn an uploaded CSV/TSV or Excel file into a small table model.

Everything is parsed in memory — nothing is written to disk — and rows are
capped so a huge file can't blow up the page.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import PurePosixPath

__all__ = ["UploadError", "Table", "Sheet", "parse_upload", "ACCEPT_ATTR", "MAX_BYTES"]

MAX_BYTES = 5 * 1024 * 1024  # upload cap, mirrored by Flask's MAX_CONTENT_LENGTH
MAX_ROWS = 500  # data rows read per sheet
MAX_SHEETS = 10
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
CSV_DELIMITERS = ",;\t|"
SPREADSHEET_SUFFIXES = {".xlsx", ".xlsm"}
DELIMITED_SUFFIXES = {".csv", ".tsv", ".txt"}

# What the <input type="file"> should offer in the picker.
ACCEPT_ATTR = ",".join(sorted(SPREADSHEET_SUFFIXES | DELIMITED_SUFFIXES))


class UploadError(ValueError):
    """Raised when a file can't be read as a table. The message is user-facing."""


@dataclass
class Sheet:
    name: str
    header: list[str]
    rows: list[list[str]]
    total_rows: int
    truncated: bool = False


@dataclass
class Table:
    filename: str
    kind: str  # "csv" | "excel"
    size: int
    sheets: list[Sheet] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return sum(sheet.total_rows for sheet in self.sheets)

    @property
    def column_count(self) -> int:
        return max((len(sheet.header) for sheet in self.sheets), default=0)

    def stats(self) -> list[tuple[str, str]]:
        """Label/value pairs for the preview header."""
        return [
            ("Sheet" + ("s" if len(self.sheets) != 1 else ""), str(len(self.sheets))),
            ("Rows", f"{self.row_count:,}"),
            ("Columns", str(self.column_count)),
        ]


def parse_upload(filename: str, data: bytes) -> Table:
    """Parse ``data`` as a table, or raise UploadError with a readable reason."""
    name = (filename or "").strip()
    if not name:
        raise UploadError("Pick a file to upload.")
    if not data:
        raise UploadError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise UploadError(f"Files must be under {MAX_BYTES // (1024 * 1024)} MB.")

    suffix = PurePosixPath(name.replace("\\", "/")).suffix.lower()
    if suffix in SPREADSHEET_SUFFIXES:
        return _parse_excel(name, data)
    if suffix in DELIMITED_SUFFIXES:
        return _parse_delimited(name, data)
    if suffix == ".xls":
        raise UploadError(
            "Legacy .xls files aren't supported — save as .xlsx or .csv and try again."
        )
    raise UploadError(f"Unsupported file type '{suffix or name}'. Use CSV, TSV or XLSX.")


def _parse_delimited(filename: str, data: bytes) -> Table:
    text = _decode(data)
    try:
        delimiter = csv.Sniffer().sniff(text[:8192], delimiters=CSV_DELIMITERS).delimiter
    except csv.Error:
        delimiter = "\t" if filename.lower().endswith(".tsv") else ","

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    raw = []
    truncated = False
    for index, row in enumerate(reader):
        if index > MAX_ROWS:
            truncated = True
            break
        raw.append([cell.strip() for cell in row])

    sheet = _sheet_from_rows("(csv)" if delimiter != "\t" else "(tsv)", raw, truncated)
    return Table(filename=filename, kind="csv", size=len(data), sheets=[sheet])


def _parse_excel(filename: str, data: bytes) -> Table:
    try:
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError:  # pragma: no cover - dependency is declared in pyproject
        raise UploadError("Excel support is unavailable (openpyxl isn't installed).")

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except InvalidFileException:
        raise UploadError("That doesn't look like a valid Excel workbook.")
    except Exception as error:  # zipfile/XML errors surface as various types
        raise UploadError(f"Couldn't read that workbook: {error}")

    sheets: list[Sheet] = []
    try:
        for worksheet in workbook.worksheets[:MAX_SHEETS]:
            rows, truncated = [], False
            for index, values in enumerate(worksheet.iter_rows(values_only=True)):
                if index > MAX_ROWS:
                    truncated = True
                    break
                rows.append([_cell(value) for value in values])
            # A worksheet can come back as a single empty trailing row.
            rows = [row for row in rows if any(row)]
            if not rows:
                continue
            sheets.append(_sheet_from_rows(worksheet.title, rows, truncated))
    finally:
        workbook.close()

    if not sheets:
        raise UploadError("That workbook has no rows to show.")
    return Table(filename=filename, kind="excel", size=len(data), sheets=sheets)


def _decode(data: bytes) -> str:
    for encoding in CSV_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UploadError("Couldn't decode that file as text — is it really a CSV?")


def _cell(value: object) -> str:
    """Flatten an Excel cell to display text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        # openpyxl hands back date-only cells as midnight datetimes; show the
        # date alone, the way a spreadsheet would.
        if value.time() == time.min:
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    return str(value)


def _sheet_from_rows(name: str, rows: list[list[str]], truncated: bool) -> Sheet:
    if not rows:
        return Sheet(name=name, header=[], rows=[], total_rows=0, truncated=truncated)

    header, *body = rows
    width = max([len(header), *(len(row) for row in body)] or [0])
    header = _clean_header(header, width)
    body = [(row + [""] * width)[:width] for row in body]
    return Sheet(
        name=name, header=header, rows=body, total_rows=len(body), truncated=truncated
    )


def _clean_header(header: list[str], width: int) -> list[str]:
    """Pad blank header cells and de-duplicate repeats, keeping order."""
    labels, seen = [], {}
    for index in range(width):
        label = header[index].strip() if index < len(header) else ""
        label = label or f"Column {index + 1}"
        if label in seen:
            seen[label] += 1
            label = f"{label} ({seen[label]})"
        else:
            seen[label] = 0
        labels.append(label)
    return labels
