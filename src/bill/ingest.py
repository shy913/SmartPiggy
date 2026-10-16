"""把「上传 → 预览 → 确认」串起来。

预览结果放在内存里（一份文件一个 token）。它是单人单次的东西：一个看二十秒
的页面，不值得为它再建一张跟 transactions 一样宽的表。代价是进程重启后确认
会失败——所以专门给了「预览已过期」的页面，而不是让用户撞上 404。
真要跨重启保活，改这一个模块就够了。
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from . import config, dedup, repo
from .errors import BillError, ProfileError
from .importer import parse
from .models import ImportPlan
from .profiles import Candidate, Profile, detect, get_profile, load_all, validate
from .readers import Grid, RawTable, build_grid, read_table

__all__ = ["PreviewExpired", "StagedPreview", "confirm", "reprofile", "stage", "store"]


class PreviewExpired(BillError):
    """这个预览已经不在了（超时、被顶掉，或者服务重启过）。"""


@dataclass
class StagedPreview:
    """一份已经解析好、等着用户点确认的文件。"""

    token: str
    filename: str
    size_bytes: int
    sha256: str
    table: RawTable
    candidates: list[Candidate]
    profile_name: str
    grid: Grid | None = None
    plan: ImportPlan | None = None
    error: str = ""
    notes: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)

    @property
    def profile(self) -> Profile:
        return get_profile(self.profile_name)

    @property
    def detected(self) -> Candidate | None:
        """得分最高、且够有信心的那个候选。没有就是认不出来。"""
        for candidate in self.candidates:
            if candidate.confident:
                return candidate
        return None

    def counts(self) -> dict[str, int]:
        if self.plan is None:
            return {"new": 0, "duplicate": 0, "possible": 0, "neutral": 0, "problem": 0}
        return self.plan.counts()

    def totals(self) -> tuple[int, int]:
        return self.plan.totals() if self.plan else (0, 0)

    def rows(self, limit: int | None = None):
        if self.plan is None:
            return []
        return self.plan.rows if limit is None else self.plan.rows[:limit]

    def raw_preview(self, limit: int = 15) -> list[tuple[int, list[str]]]:
        """没找到表头时，把文件开头几行原样摊给用户看——这就是他们要拿去
        改配置的信息。"""
        return [
            (index + 1, row) for index, row in enumerate(self.table.rows[:limit])
        ]


class _Store:
    """有上限、会过期的内存暂存区。"""

    def __init__(self) -> None:
        self._items: OrderedDict[str, StagedPreview] = OrderedDict()

    def put(self, preview: StagedPreview) -> None:
        self._evict()
        self._items[preview.token] = preview
        while len(self._items) > config.PREVIEW_KEEP:
            self._items.popitem(last=False)

    def get(self, token: str) -> StagedPreview:
        preview = self._items.get(token)
        if preview is None:
            raise PreviewExpired("这个预览已经不在了，可能超时或者服务重启过。")
        if time.monotonic() - preview.created_at > config.PREVIEW_TTL_SECONDS:
            self._items.pop(token, None)
            raise PreviewExpired("这个预览已经过期了，请把文件重新传一次。")
        return preview

    def latest(self) -> StagedPreview | None:
        """最近一次暂存的预览。配置页靠它显示刚读到的真实表头。"""
        if not self._items:
            return None
        return next(reversed(self._items.values()))

    def drop(self, token: str) -> None:
        self._items.pop(token, None)

    def clear(self) -> None:
        self._items.clear()

    def _evict(self) -> None:
        now = time.monotonic()
        stale = [
            token
            for token, preview in self._items.items()
            if now - preview.created_at > config.PREVIEW_TTL_SECONDS
        ]
        for token in stale:
            self._items.pop(token, None)


store = _Store()


# --------------------------------------------------------------------------
# 上传
# --------------------------------------------------------------------------


def stage(connection: sqlite3.Connection, filename: str, data: bytes) -> StagedPreview:
    """读一份上传的文件，猜格式，先解析一遍给用户看。"""
    profiles = load_all()
    if not profiles:
        raise ProfileError("一个列映射配置都没有，无法导入。")

    # 让编码探测和 ZIP 成员挑选看得懂任何一种已知格式的列名。
    probes: list[str] = []
    for profile in profiles:
        for name in profile.probe_columns():
            if name not in probes:
                probes.append(name)

    table = read_table(filename, data, probes=tuple(probes))
    candidates = detect(table, filename, profiles)

    preview = StagedPreview(
        token=secrets.token_urlsafe(12),
        filename=filename,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        table=table,
        candidates=candidates,
        # 认不准就用得分最高的那份先解析着，下拉框里用户可以改。
        profile_name=(candidates[0].profile.name if candidates else profiles[0].name),
    )

    _apply(connection, preview, preview.profile_name)
    store.put(preview)
    return preview


def reprofile(
    connection: sqlite3.Connection, token: str, profile_name: str
) -> StagedPreview:
    """换一个配置重新解析。文件还在暂存区里，不用重传。"""
    preview = store.get(token)
    _apply(connection, preview, profile_name)
    return preview


def _apply(connection: sqlite3.Connection, preview: StagedPreview, profile_name: str) -> None:
    """换配置重新解析一次，然后把页面上的提示一并刷新。"""
    _apply_inner(connection, preview, profile_name)
    _rebuild_notes(connection, preview)


def _apply_inner(
    connection: sqlite3.Connection, preview: StagedPreview, profile_name: str
) -> None:
    """用指定配置重新算一遍格子、计划和重复标记，就地写回 preview。"""
    preview.profile_name = profile_name
    preview.grid = None
    preview.plan = None
    preview.error = ""

    try:
        profile = get_profile(profile_name)
    except ProfileError as error:
        preview.error = str(error)
        return

    problems = validate(profile)
    if problems:
        preview.error = "这份配置本身有问题：" + "；".join(problems)
        return

    index = next(
        (
            candidate.header_index
            for candidate in preview.candidates
            if candidate.profile.name == profile_name and candidate.header_index is not None
        ),
        None,
    )
    if index is None:
        from .readers import find_header

        index = find_header(
            preview.table.rows,
            profile.header_probes(),
            scan=profile.scan_first_rows,
            start_after=profile.start_after,
        )
    if index is None:
        probes = "、".join(profile.header_probes()) or "（配置里没写表头特征）"
        preview.error = f"在这份文件里没找到表头行——需要同时出现：{probes}"
        return

    grid = build_grid(preview.table, index)
    # 预览阶段只看不改：这里只**查**账户，不建。账户等 confirm 真的导入时再建，
    # 否则「确认之前不写库」就是句空话——光看一眼预览页也会在库里留下一条账户。
    # 账户还不存在时下面按「库里没有历史记录」处理，那正是事实。
    account_id = repo.find_account(connection, profile.title)

    try:
        plan = parse(grid, profile, account_id=account_id)
    except ProfileError as error:
        preview.error = str(error)
        return

    # 结合库里已有的数据，给每一行定最终去重键并标出重复。
    # 账户还没建过的话就是「库里一条都没有」，空字典正是这个意思。
    pairs = [(row.dedup_key, row.content_hash) for row in plan.rows]
    existing = repo.existing_dedup(connection, account_id) if account_id else {}
    for row, (key, duplicate) in zip(plan.rows, dedup.assign(pairs, existing=existing)):
        row.dedup_key = key
        row.duplicate = duplicate

    if account_id is not None:
        _flag_possible_duplicates(connection, account_id, plan)

    preview.grid = grid
    preview.plan = plan


def _flag_possible_duplicates(
    connection: sqlite3.Connection, account_id: int, plan: ImportPlan
) -> None:
    """标出「很可能是同一笔、但去重键没认出来」的行。

    判据：同一个账户里已经有同一天、同方向、同金额的记录。银行重新导出时把
    摘要或对方名换了个写法就会落到这里——去重键对不上，但钱其实是同一笔。
    照常入库（宁可多存也不丢真账），只是在预览页上标出来让人过目。
    """
    dates = [row.transaction.occurred_date for row in plan.rows]
    if not dates:
        return

    existing = repo.fingerprints(connection, account_id, min(dates), max(dates))
    for row in plan.rows:
        if row.duplicate:
            continue
        transaction = row.transaction
        row.possible_duplicate = (
            transaction.occurred_date,
            str(transaction.direction),
            transaction.amount_cents,
        ) in existing


def _rebuild_notes(connection: sqlite3.Connection, preview: StagedPreview) -> None:
    """重算预览页顶部的提示。

    每次重新解析都整个重建，免得换配置之后还挂着上一次的旧提示。
    """
    preview.notes = []

    earlier = repo.previous_import_of_file(connection, preview.sha256)
    if earlier is not None:
        preview.notes.append(
            f"这个文件在 {earlier['uploaded_at'][:10]} 导过一次，重复的部分会自动跳过。"
        )

    plan = preview.plan
    if plan is None or not plan.rows or plan.account_id is None:
        return

    dates = [row.transaction.occurred_date for row in plan.rows]
    overlapping = repo.imports_overlapping(
        connection, plan.account_id, min(dates), max(dates)
    )
    if not overlapping:
        return

    first = overlapping[0]
    preview.notes.append(
        f"这份文件的时间范围（{min(dates)} ~ {max(dates)}）跟 "
        f"{first['uploaded_at'][:10]} 导入的「{first['filename']}」有重叠——"
        "长期记账时这很正常，重叠部分的重复记录已经跳过了。"
    )


# --------------------------------------------------------------------------
# 确认入库
# --------------------------------------------------------------------------


def confirm(connection: sqlite3.Connection, token: str) -> int:
    """把预览里的东西真正写进库，返回 import_id。"""
    preview = store.get(token)
    if preview.plan is None:
        raise PreviewExpired("这份预览没有可导入的内容，请重新上传文件。")

    plan = preview.plan
    profile = preview.profile

    rows = plan.new_rows
    coverage_start = min((row.transaction.occurred_at for row in rows), default=None)
    coverage_end = max((row.transaction.occurred_at for row in rows), default=None)

    with connection:
        # 账户按配置现算一次，不直接信任暂存时存下的 id：那是上一个请求的
        # 连接建的，而且用户中途可能换过配置。
        account_id = repo.ensure_account(connection, profile.title, profile.kind)

        import_id = repo.create_import(
            connection,
            account_id=account_id,
            profile_name=plan.profile_name,
            filename=preview.filename,
            file_sha256=preview.sha256,
            size_bytes=preview.size_bytes,
            row_count=len(plan.rows),
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )

        inserted, duplicates = repo.insert_rows(
            connection, account_id=account_id, import_id=import_id, rows=plan.rows
        )

        repo.save_issues(connection, import_id, plan.issues)
        repo.finalize_import(
            connection,
            import_id,
            inserted=inserted,
            duplicates=duplicates,
            errors=sum(1 for issue in plan.issues if issue.level == "error"),
            warnings=sum(1 for issue in plan.issues if issue.level == "warning"),
        )

    store.drop(token)
    return import_id
