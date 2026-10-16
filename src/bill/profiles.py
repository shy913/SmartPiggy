"""列映射配置。

账单格式的差异收在这里，而不是散在代码里：支付宝和微信不是两套**代码**，
是两份**配置**。新增一家银行 = 新增一个 TOML 文件，不用改 Python。

用 TOML（stdlib ``tomllib``）而不是 YAML/JSON：同样零依赖，但**能写注释**——
这种文件几乎总是照着别人的改出来的，能标注「招行 2024 版把日期放在第 3 列」
比什么都重要。
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .dates import DEFAULT_FORMATS, DEFAULT_TIME
from .errors import ProfileError
from .readers import RawTable, build_grid, find_header

__all__ = [
    "FIELDS",
    "SIGN_CONVENTIONS",
    "Candidate",
    "Profile",
    "detect",
    "load_all",
    "load_profile",
    "validate",
]

# [columns] 里认得的字段。
#
# time 是给「日期和时间分成两列」的账单用的（Monzo 就是），映射了它就会跟
# date 拼起来再解析。
# amount_in/amount_out 是给「借贷分两列」的银行用的，配了它就走分列模式，
# 不看 amount 和 sign_convention。
FIELDS = (
    "date",
    "time",
    "amount",
    "amount_in",
    "amount_out",
    "direction",
    "counterparty",
    "counterparty_account",
    "description",
    "memo",
    "payment_method",
    "status",
    "source_txn_id",
    "merchant_txn_id",
    "category",
    "currency",
)

SIGN_CONVENTIONS = ("direction_column", "negative_is_out", "negative_is_in")

# 找表头时最多往前扫多少行，兜住再啰嗦的前言。
DEFAULT_SCAN = 60

# 一行至少要有几格内容才可能是笔交易。定得很低（日期 + 金额就够）是刻意的：
# 末尾那些汇总行已经由 footer_patterns 负责，这个值只是兜住零星的窄行，
# 定高了会把「末尾字段为空」的合法行一起吃掉——丢真数据的代价大得多。
DEFAULT_MIN_COLUMNS = 2


@dataclass(frozen=True)
class Profile:
    name: str
    title: str
    kind: str = "other"
    # 账单里没有币种列时用这个。有了币种字段，汇总才能不把英镑和人民币加在一起。
    default_currency: str = "CNY"
    format: str = "auto"
    encoding: tuple[str, ...] = ()
    delimiter: str | None = None
    filename_globs: tuple[str, ...] = ()
    required_headers: tuple[str, ...] = ()
    preferred_headers: tuple[str, ...] = ()
    header_any: tuple[str, ...] = ()
    scan_first_rows: int = DEFAULT_SCAN
    start_after: str | None = None
    columns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    date_formats: tuple[str, ...] = DEFAULT_FORMATS
    default_time: str = DEFAULT_TIME
    sign_convention: str = "direction_column"
    direction_from_sign: bool = False
    in_values: tuple[str, ...] = ("收入",)
    out_values: tuple[str, ...] = ("支出",)
    neutral_values: tuple[str, ...] = ("不计收支", "/", "")
    id_null_values: frozenset[str] = field(default_factory=frozenset)
    footer_patterns: tuple[str, ...] = ()
    min_columns: int = DEFAULT_MIN_COLUMNS
    origin: str = "builtin"  # builtin | user
    path: str = ""

    # -- 列名解析 ---------------------------------------------------------

    def candidates(self, name: str) -> tuple[str, ...]:
        return self.columns.get(name, ())

    def probe_columns(self) -> tuple[str, ...]:
        """这份配置认得的所有列名，给编码和 ZIP 成员打分用。"""
        seen: list[str] = []
        for names in self.columns.values():
            for name in names:
                if name not in seen:
                    seen.append(name)
        for name in (*self.required_headers, *self.header_any):
            if name not in seen:
                seen.append(name)
        return tuple(seen)

    def header_probes(self) -> tuple[str, ...]:
        """定位表头行时要全部命中的列名。"""
        return self.header_any or self.required_headers

    def id_column(self) -> tuple[str, ...]:
        return self.columns.get("source_txn_id", ())

    @property
    def split_amount(self) -> bool:
        """收入、支出分成两列（不少银行的流水就是这样）。"""
        return bool(self.columns.get("amount_in")) and bool(
            self.columns.get("amount_out")
        )

    @property
    def uses_sign(self) -> bool:
        return self.sign_convention in ("negative_is_out", "negative_is_in")


@dataclass
class Candidate:
    """一个 profile 对一份文件的匹配情况。"""

    profile: Profile
    score: int = 0
    header_index: int | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.score >= 60 and self.header_index is not None


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return (str(value),)


def load_profile(path: Path, origin: str = "builtin") -> Profile:
    """读一份 TOML 配置。"""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        raise ProfileError(f"找不到配置：{path}")
    except tomllib.TOMLDecodeError as error:
        raise ProfileError(f"{path.name} 不是合法的 TOML：{error}")

    meta = data.get("profile", {})
    name = str(meta.get("name") or path.stem)
    match = data.get("match", {})
    headers = data.get("headers", {})
    parse = data.get("parse", {})
    values = data.get("values", {})
    dedup = data.get("dedup", {})
    cleanup = data.get("cleanup", {})

    columns: dict[str, tuple[str, ...]] = {}
    for key, value in (data.get("columns") or {}).items():
        if key not in FIELDS:
            raise ProfileError(
                f"{path.name} 里的列名「{key}」不认识。可用的是：{'、'.join(FIELDS)}"
            )
        names = _as_tuple(value)
        if names:
            columns[key] = names

    sign = str(parse.get("sign_convention") or "direction_column")
    if sign not in SIGN_CONVENTIONS:
        raise ProfileError(
            f"{path.name} 的 sign_convention「{sign}」不认识，"
            f"只能是：{'、'.join(SIGN_CONVENTIONS)}"
        )

    return Profile(
        name=name,
        title=str(meta.get("title") or name),
        kind=str(meta.get("kind") or "other"),
        default_currency=str(meta.get("currency") or "CNY").upper(),
        format=str(meta.get("format") or "auto"),
        encoding=_as_tuple(meta.get("encoding")),
        delimiter=meta.get("delimiter") or None,
        filename_globs=_as_tuple(match.get("filename_globs")),
        required_headers=_as_tuple(match.get("required_headers")),
        preferred_headers=_as_tuple(match.get("preferred_headers")),
        header_any=_as_tuple(headers.get("match_any")),
        scan_first_rows=int(headers.get("scan_first_rows") or DEFAULT_SCAN),
        start_after=headers.get("start_after") or None,
        columns=columns,
        date_formats=_as_tuple(parse.get("date_formats")) or DEFAULT_FORMATS,
        default_time=str(parse.get("default_time") or DEFAULT_TIME),
        sign_convention=sign,
        direction_from_sign=bool(parse.get("direction_from_sign", False)),
        in_values=_as_tuple(values.get("in")) or ("收入",),
        out_values=_as_tuple(values.get("out")) or ("支出",),
        neutral_values=_as_tuple(values.get("neutral"))
        or ("不计收支", "/", ""),
        id_null_values=frozenset(_as_tuple(dedup.get("id_null_values")) or ("", "/", "--", "0")),
        footer_patterns=_as_tuple(cleanup.get("footer_patterns")),
        min_columns=int(cleanup.get("min_columns") or DEFAULT_MIN_COLUMNS),
        origin=origin,
        path=str(path),
    )


def load_all() -> list[Profile]:
    """内置配置 + 用户配置。

    用户的排在前面，同名的直接覆盖内置——用户想改内置支付宝配置时，
    写一份同名文件到自己的目录就行，不用动代码。
    """
    loaded: dict[str, Profile] = {}

    for directory, origin in (
        (config.builtin_profiles_dir(), "builtin"),
        (config.user_profiles_dir(), "user"),
    ):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.toml")):
            try:
                profile = load_profile(path, origin)
            except ProfileError:
                # 用户目录里写坏的文件不该让整个应用起不来。
                if origin == "builtin":
                    raise
                continue
            loaded[profile.name] = profile

    return list(loaded.values())


def get_profile(name: str) -> Profile:
    for profile in load_all():
        if profile.name == name:
            return profile
    raise ProfileError(f"没有名为「{name}」的配置。")


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------


def validate(profile: Profile) -> list[str]:
    """配置自身的毛病，返回给人看的问题列表。

    在解析任何数据行**之前**跑，这样用户看到的是一句「这份配置说不清收支方向」，
    而不是三千行报错。
    """
    problems: list[str] = []

    if not profile.candidates("date"):
        problems.append("没有配置日期列（[columns] 里的 date）")
    if not profile.split_amount and not profile.candidates("amount"):
        problems.append("没有配置金额列（[columns] 里的 amount）")
    if profile.split_amount and not profile.candidates("amount"):
        pass  # 分列模式不需要 amount

    if not profile.split_amount:
        if profile.sign_convention == "direction_column" and not profile.candidates(
            "direction"
        ):
            problems.append(
                "既没有 direction 列，也没有把 sign_convention 设成 "
                "negative_is_out / negative_is_in，无法判断收还是支"
            )

    if profile.format not in ("auto", "csv", "xlsx", "zip"):
        problems.append(f"format「{profile.format}」不认识，只能是 auto/csv/xlsx/zip")

    for pattern in profile.footer_patterns:
        try:
            re.compile(pattern)
        except re.error as error:
            problems.append(f"cleanup.footer_patterns 里的「{pattern}」不是合法正则：{error}")

    return problems


# --------------------------------------------------------------------------
# 探测：这份文件像哪个 profile
# --------------------------------------------------------------------------


def detect(
    table: RawTable, filename: str, profiles: list[Profile]
) -> list[Candidate]:
    """给每个 profile 打分，从高到低返回。

    分数只是**建议**，不是决定。差得不多或者都不到线，就交给用户在下拉框里选——
    认错格式然后静默入库一堆垃圾，比多问一句糟得多。
    """
    candidates = [
        _score(table, filename, profile)
        for profile in profiles
        if not validate(profile)  # 自身有毛病的配置不参与竞争
    ]
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates


def _score(table: RawTable, filename: str, profile: Profile) -> Candidate:
    result = Candidate(profile=profile)

    probes = profile.header_probes()
    index = find_header(
        table.rows,
        probes,
        scan=profile.scan_first_rows,
        start_after=profile.start_after,
    )
    if index is None:
        missing = "、".join(probes) if probes else "（配置里没写表头特征）"
        result.reasons.append(f"没找到表头行（需要同时出现：{missing}）")
        return result

    result.header_index = index
    grid = build_grid(table, index)
    header = set(grid.header)

    if profile.required_headers:
        missing = [name for name in profile.required_headers if name not in header]
        if missing:
            result.reasons.append(f"表头缺列：{'、'.join(missing)}")
        else:
            result.score += 40
            result.reasons.append("必需列都在")

    preferred_hits = sum(1 for name in profile.preferred_headers if name in header)
    if preferred_hits:
        result.score += min(preferred_hits * 5, 20)
        result.reasons.append(f"命中 {preferred_hits} 个特征列")

    if profile.filename_globs and any(
        fnmatch.fnmatch(_lower(filename), _lower(pattern))
        for pattern in profile.filename_globs
    ):
        result.score += 15
        result.reasons.append("文件名符合")

    mapping = resolve(grid, profile)
    # 只关心必需字段有没有对上；对方、备注之类的缺了也能用。
    # 而且只算这份配置**真的声明过**的字段，否则没配 amount_in 的支付宝
    # 会被报一句莫名其妙的「没对上」。
    essential = [
        name
        for name in ("date", "amount", "amount_in", "amount_out", "direction")
        if profile.candidates(name)
    ]
    resolved = [name for name in essential if mapping[name]]
    unresolved = [name for name in essential if not mapping[name]]
    if resolved:
        result.score += 25
        result.reasons.append(f"映射上 {len(resolved)} 个字段")
    if unresolved:
        result.reasons.append(f"没对上：{'、'.join(unresolved)}")

    if profile.format != "auto" and profile.format == table.kind:
        result.score += 10

    return result


def resolve(grid, profile: Profile) -> dict[str, str]:
    """所有字段实际对上哪个列名。"""
    return {
        name: next(
            (option for option in profile.candidates(name) if option in grid.header), ""
        )
        for name in FIELDS
    }


def _lower(text: str) -> str:
    return (text or "").lower()
