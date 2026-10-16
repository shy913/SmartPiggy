"""把上传的字节变成一张表。

只有本模块关心编码、分隔符、ZIP 成员和 Excel 格式；别的模块拿到的是已经
规整成「表头 + 数据行」的表。

账单文件跟普通 CSV 有个关键区别：前面有若干行账户说明，中间一行分隔符，
真正的表头在中间，末尾还有账户汇总。所以读取分成两步——先原样把行读出来
（``RawTable``），再由调用方拿着 profile 的列名去定位表头（``find_header``）。
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import PurePosixPath

from .config import MAX_BYTES, MAX_ROWS
from .errors import UploadError

__all__ = [
    "ACCEPT_ATTR",
    "DELIMITED_SUFFIXES",
    "SPREADSHEET_SUFFIXES",
    "Grid",
    "RawTable",
    "build_grid",
    "find_header",
    "read_table",
]

SPREADSHEET_SUFFIXES = {".xlsx", ".xlsm"}
DELIMITED_SUFFIXES = {".csv", ".tsv", ".txt"}
ZIP_SUFFIXES = {".zip"}

# 文件选择框里给出的类型，加上 .zip —— 支付宝的导出就是一个压缩包。
ACCEPT_ATTR = ",".join(sorted(SPREADSHEET_SUFFIXES | DELIMITED_SUFFIXES | ZIP_SUFFIXES))

_ZIP_MAGIC = b"PK\x03\x04"
_DELIMITERS = (",", "\t", ";", "|")

# 按顺序试。注意这里「按顺序试」只是初始顺序，真正决定用哪个的是下面的打分：
# cp1252 / latin-1 对任何字节流都不会抛异常，要是照「第一个不报错的就用」，
# GBK 编码的支付宝账单会被它们悄悄解成一堆乱码，还不报任何错。
_ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "gb18030", "big5", "cp1252", "latin-1")


@dataclass
class RawTable:
    """一份文件解码后的原始行，还没决定哪一行是表头。"""

    filename: str
    kind: str  # "csv" | "xlsx"
    rows: list[list[str]]
    encoding: str = ""
    delimiter: str = ""
    truncated: bool = False
    member: str = ""  # ZIP 里的成员名，非压缩包为空
    notes: list[str] = field(default_factory=list)  # 给用户看的读取说明

    def describe(self) -> list[tuple[str, str]]:
        """预览页上那几块「文件信息」。"""
        facts = [("格式", self.kind.upper())]
        if self.member:
            facts.append(("压缩包成员", self.member))
        if self.encoding:
            facts.append(("编码", self.encoding))
        if self.delimiter:
            facts.append(("分隔符", {",": "逗号", "\t": "制表符", ";": "分号", "|": "竖线"}.get(self.delimiter, self.delimiter)))
        facts.append(("读到的行数", f"{len(self.rows):,}"))
        return facts


@dataclass
class Grid:
    """定位好表头的表：表头 + 数据行。"""

    header: list[str]
    rows: list[list[str]]
    header_index: int
    source: RawTable

    def column(self, name: str) -> int | None:
        """列名 -> 列号。找不到返回 None。"""
        try:
            return self.header.index(name)
        except ValueError:
            return None

    def cell(self, row: list[str], name: str) -> str:
        index = self.column(name)
        if index is None or index >= len(row):
            return ""
        return row[index].strip()


def read_table(
    filename: str,
    data: bytes,
    *,
    probes: tuple[str, ...] = (),
    encoding: str | None = None,
    delimiter: str | None = None,
) -> RawTable:
    """把上传的字节读成原始行。

    ``probes`` 是「这份文件里应该出现哪些列名」，同时用来给编码和 ZIP 成员打分。
    探测 profile 时传所有候选列名的并集。
    """
    name = (filename or "").strip()
    if not name:
        raise UploadError("请先选一个文件。")
    if not data:
        raise UploadError("这个文件是空的。")
    if len(data) > MAX_BYTES:
        raise UploadError(f"单个文件不能超过 {MAX_BYTES // (1024 * 1024)} MB。")

    suffix = PurePosixPath(name.replace("\\", "/")).suffix.lower()

    # 顺序很重要：.xlsx 本身就是一个 zip 包，所以必须先按后缀认 Excel，
    # 再考虑「靠魔数认出这是个压缩包」的情况（支付宝有时把 zip 导成 .csv）。
    if suffix in SPREADSHEET_SUFFIXES:
        return _read_excel(name, data)
    if suffix == ".xls":
        raise UploadError("不支持老的 .xls 格式——请另存为 .xlsx 或 .csv 再传。")
    if suffix in ZIP_SUFFIXES or data[:4] == _ZIP_MAGIC:
        return _read_zip(name, data, probes, encoding, delimiter)
    if suffix in DELIMITED_SUFFIXES or not suffix:
        return _read_delimited(name, data, probes, encoding, delimiter)
    raise UploadError(f"不认识的格式「{suffix}」。支持 CSV、TSV、XLSX 和 ZIP。")


# --------------------------------------------------------------------------
# 分隔符文本（CSV / TSV）
# --------------------------------------------------------------------------


def _read_delimited(
    filename: str,
    data: bytes,
    probes: tuple[str, ...],
    encoding: str | None,
    delimiter: str | None,
) -> RawTable:
    text, used_encoding = _decode(data, [encoding] if encoding else [], probes)
    used_delimiter = _choose_delimiter(text, delimiter)

    rows: list[list[str]] = []
    truncated = False
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=used_delimiter)
    for index, raw in enumerate(reader):
        if index >= MAX_ROWS:
            truncated = True
            break
        rows.append([cell.strip() for cell in raw])

    table = RawTable(
        filename=filename,
        kind="csv",
        rows=rows,
        encoding=used_encoding,
        delimiter=used_delimiter,
        truncated=truncated,
    )
    if truncated:
        table.notes.append(f"文件超过 {MAX_ROWS:,} 行，只读了前面这些。")
    return table


def _choose_delimiter(text: str, declared: str | None) -> str:
    """挑分隔符。

    不拿整段文本去跑 csv.Sniffer —— 账单前面的说明行里也带逗号和冒号，
    sniffer 很容易被带偏。这里改成看**结构一致性**：哪种分隔符切出来的列数
    在行间最稳定，就用哪种；一致程度相同再比列数。只切出单列的分隔符直接
    跳过，因为再怎么切也不像一张表。
    """
    if declared and declared != "auto":
        return "\t" if declared == "\\t" else declared

    sample = "\n".join(text.splitlines()[:50])
    if not sample.strip():
        return ","

    best, best_key = ",", (0.0, 0)
    for candidate in _DELIMITERS:
        widths = [
            len(row) for row in csv.reader(io.StringIO(sample), delimiter=candidate)
        ]
        if not widths:
            continue
        significant = [width for width in widths if width > 1]
        if not significant:
            continue
        width, occurrences = Counter(significant).most_common(1)[0]
        key = (occurrences / len(widths), width)
        if key > best_key:
            best, best_key = candidate, key
    return best


def _decode(data: bytes, declared: list[str], probes: tuple[str, ...]) -> tuple[str, str]:
    """解码，返回 (文本, 实际用的编码)。

    profile 声明的编码优先试，然后是通用水梯。剩下的候选**打分**而不是取第一个
    不报错的：得分看「表头列名命中几个」和「中文字符占比」，两项都高才像一份
    中文账单。这正是不让 cp1252 把 GBK 文件静默解成乱码的关键。
    """
    ordered: list[str] = []
    for candidate in (*declared, *_ENCODING_CANDIDATES):
        if candidate and candidate not in ordered:
            ordered.append(candidate)

    best: tuple[tuple[int, int], str, str] | None = None
    for candidate in ordered:
        try:
            text = data.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
        if "�" in text:
            continue
        hits = sum(1 for probe in probes if probe in text)
        key = (hits, _cjk_count(text))
        if best is None or key > best[0]:
            best = (key, text, candidate)
        if probes and hits == len(probes):
            break

    if best is None:
        raise UploadError("这个文件解不成文本，它真的是一份 CSV 吗？")
    return best[1], best[2]


def _cjk_count(text: str, limit: int = 4000) -> int:
    return sum(1 for char in text[:limit] if "一" <= char <= "鿿")


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------


def _read_excel(filename: str, data: bytes) -> RawTable:
    try:
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError:  # pragma: no cover - 依赖在 pyproject 里声明了
        raise UploadError("缺少 Excel 支持（openpyxl 没装）。")

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except InvalidFileException:
        raise UploadError("这看起来不是一个有效的 Excel 文件。")
    except Exception as error:  # zipfile/XML 的报错类型五花八门
        raise UploadError(f"读不了这个工作簿：{error}")

    # 账单只会在一张表里，取第一张有内容的——不像以前的预览器那样把所有
    # sheet 拼起来，那对记账没有意义。
    rows: list[list[str]] = []
    sheet_name = ""
    truncated = False
    try:
        for worksheet in workbook.worksheets:
            collected: list[list[str]] = []
            for index, values in enumerate(worksheet.iter_rows(values_only=True)):
                if index >= MAX_ROWS:
                    truncated = True
                    break
                collected.append([_cell(value) for value in values])
            if any(cell for row in collected for cell in row):
                rows, sheet_name = collected, worksheet.title
                break
    finally:
        workbook.close()

    if not rows:
        raise UploadError("这个工作簿里没有内容。")

    table = RawTable(filename=filename, kind="xlsx", rows=rows, truncated=truncated)
    table.notes.append(f"工作表：{sheet_name}")
    if truncated:
        table.notes.append(f"文件超过 {MAX_ROWS:,} 行，只读了前面这些。")
    return table


def _cell(value: object) -> str:
    """把 Excel 单元格摊平成文本。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        # openpyxl 把纯日期的单元格给成午夜 datetime，按表格的显示习惯只留日期。
        if value.time() == time.min:
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    return str(value).strip()


# --------------------------------------------------------------------------
# ZIP（支付宝的导出是个压缩包，里面装着明细 CSV 和汇总 CSV）
# --------------------------------------------------------------------------


def _read_zip(
    filename: str,
    data: bytes,
    probes: tuple[str, ...],
    encoding: str | None,
    delimiter: str | None,
) -> RawTable:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UploadError("这个压缩包打不开，可能已经损坏。")

    members: list[RawTable] = []
    skipped: list[str] = []
    with archive:
        for info in archive.infolist():
            name = info.filename
            if info.is_dir():
                continue
            # macOS 打包时会塞进来的元数据副本。
            if name.startswith("__MACOSX/") or PurePosixPath(name).name.startswith("._"):
                skipped.append(name)
                continue
            member_name = PurePosixPath(name).name
            suffix = PurePosixPath(member_name).suffix.lower()
            if suffix not in SPREADSHEET_SUFFIXES | DELIMITED_SUFFIXES:
                skipped.append(name)
                continue
            try:
                payload = archive.read(info)
                if suffix in SPREADSHEET_SUFFIXES:
                    table = _read_excel(member_name, payload)
                else:
                    table = _read_delimited(member_name, payload, probes, encoding, delimiter)
            except UploadError:
                skipped.append(name)
                continue
            table.member = name
            members.append(table)

    if not members:
        raise UploadError("这个压缩包里没有能读的 CSV 或 Excel 文件。")

    # 谁能对上最多列名就用谁——支付宝包里明细表命中 11 个列名，汇总表一个都
    # 对不上，所以直接选明细表。并列时选行数多的那个。
    def score(table: RawTable) -> tuple[int, int]:
        text = "\n".join(",".join(row) for row in table.rows[:60])
        return (sum(1 for probe in probes if probe in text), len(table.rows))

    best = max(members, key=score)
    if skipped:
        best.notes.append(f"压缩包里跳过了 {len(skipped)} 个不相关的文件。")
    if len(members) > 1:
        best.notes.append(f"压缩包里有 {len(members)} 个表格，用了「{best.member}」。")
    best.filename = filename
    return best


# --------------------------------------------------------------------------
# 表头定位
# --------------------------------------------------------------------------


def find_header(
    rows: list[list[str]],
    candidates: tuple[str, ...],
    *,
    scan: int = 60,
    start_after: str | None = None,
) -> int | None:
    """在开头若干行里找表头行，返回行号；找不到返回 None。

    这里刻意不做「挑最宽的那一行」之类的猜测——猜错表头比明确报错更糟。
    找不到就老实返回 None，让上层提示用户换一个配置。
    """
    if not candidates:
        return None

    start = 0
    if start_after:
        for index, row in enumerate(rows[:scan]):
            if any(start_after in cell for cell in row):
                start = index + 1
                break

    stop = min(len(rows), start + scan)
    for index in range(start, stop):
        cells = {cell.strip() for cell in rows[index]}
        if all(candidate in cells for candidate in candidates):
            return index
    return None


def build_grid(source: RawTable, header_index: int) -> Grid:
    """把原始行切成表头和数据行。"""
    header = _clean_header(source.rows[header_index])
    width = len(header)
    body = [(row + [""] * width)[:width] for row in source.rows[header_index + 1 :]]
    return Grid(header=header, rows=body, header_index=header_index, source=source)


def _clean_header(header: list[str]) -> list[str]:
    """补齐空列名、给重名的列名加序号，保持原有顺序。"""
    labels: list[str] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(header):
        label = raw.strip().replace("　", " ") if raw else ""
        label = label or f"第 {index + 1} 列"
        if label in seen:
            seen[label] += 1
            label = f"{label} ({seen[label]})"
        else:
            seen[label] = 0
        labels.append(label)
    return labels
