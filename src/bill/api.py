"""JSON 接口：前端 SPA 取数的地方。

**这一层只做「取数 + 序列化」。** 所有口径都在 `repo.py` 里（尤其
``_effective_cte`` 那份合并后的净额账本），这里一行 SQL 都不写——接口和旧页面
算出来的数必须是同一个，唯一的办法就是它们取的是同一份数。

三条纪律，违反了不会报错，只会出错数：

1. **金额一律整数最小单位**（人民币的分、英镑的便士），字段名以 ``_cents`` 结尾。
   JSON 里**绝不出现浮点金额**——浮点一过序列化就可能变成 ``325.0000000000001``
   这种数，前端再加一次就偏了。汇率也走字符串（``"8.9"``）。
2. **时间发的是库里那串显示时区的墙上时钟**（``"YYYY-MM-DD HH:MM:SS"``），
   不是 UTC、不是 epoch。``occurred_date`` 的区间筛选靠的是字符串字典序，
   换一种表示就把那个前提推翻了。
3. **筛选参数缺席与为空是两件事**：``?start=&end=`` 是不限，压根没给这个参数
   才是「落回当月」。判定在 `params.list_query` 里，一处说了算。
"""

from __future__ import annotations

import json

from flask import Blueprint, g, jsonify, request, session

from . import auth, config, db, entry, ingest, params, repo, settings, themes
from .profiles import load_all, validate

blueprint = Blueprint("api", __name__, url_prefix="/api")

__all__ = ["blueprint", "get_conn", "current_auth", "list_counts"]


# --------------------------------------------------------------------------
# 请求上下文：连接与认证
# --------------------------------------------------------------------------

def get_conn():
    """这次请求用的数据库连接。

    挂在 ``g`` 上，连接由 ``web.py`` 那个 app 级的 teardown 负责关——同一个 key，
    两条取数路径（旧页面与接口）共用一份连接，一次请求不会开出两个来。
    """
    if "conn" not in g:
        g.conn = db.connect()
        db.init_db(g.conn)
    return g.conn


def current_auth() -> auth.Auth | None:
    """这次请求读一次认证配置。

    跟 ``settings.load`` 一样**不缓存**：文件小，读一遍的开销可以忽略，而缓存
    会引出「密码改了没生效」这种最难查的毛病。
    """
    if "auth" not in g:
        g.auth = auth.load()
    return g.auth


def logged_in() -> bool:
    return bool(session.get("logged_in"))


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------

def rows(result) -> list[dict]:
    """``sqlite3.Row`` 的列表 → 普通 dict 的列表。

    ``dict(row)`` 原样带出列的类型：``amount_cents`` 是 INTEGER，出来就是 int，
    不会被这里转成浮点。刻意不做任何「格式化」——前端要的是数，不是
    ``"12,000.00"`` 这种串。
    """
    return [dict(row) for row in result]


def row(result) -> dict | None:
    return None if result is None else dict(result)


def _account_rows(connection) -> list[dict]:
    return rows(repo.accounts(connection))


# --------------------------------------------------------------------------
# 会话与元信息
# --------------------------------------------------------------------------

@blueprint.get("/session")
def session_state():
    """前端开屏问的第一件事：要不要登录、现在登没登。

    ``auth_enabled`` 为假＝这台机器还没设密码（不设防），前端顶上挂提示条、
    不展示登录页——跟旧界面上「没设密码就没有登录页」是一个意思。
    """
    return jsonify(auth_enabled=current_auth() is not None, logged_in=logged_in())


@blueprint.get("/meta")
def meta():
    """表单要用的下拉候选、当前主题、默认区间——一次拿全。

    每页各拉一遍的话，各页之间迟早会有一处用着旧数据。
    """
    conf = settings.load()
    connection = get_conn()
    return jsonify(
        auth_enabled=current_auth() is not None,
        logged_in=logged_in(),
        display_currency=conf.display_currency,
        timezone=conf.timezone,
        theme=conf.theme,
        themes=[{"name": name, "label": themes.get(name).label} for name in themes.names()],
        currencies=repo.currencies(connection),
        categories=repo.transaction_categories(connection),
        accounts=_account_rows(connection),
        large={"out_cents": conf.large_out_cents, "in_cents": conf.large_in_cents},
        # 默认区间的「今天」按**显示时区**算，且只在服务端算这一份——前端再算一遍
        # 就多了一套时区逻辑，两边会在月初月末差出一天。
        default_range=params.default_range(conf),
    )


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------

@blueprint.get("/summary")
def summary():
    """整页一个口径：选定的区间作用到上面每一个数、每一张表。

    所以这里的 ``start`` / ``end`` 是**同一个** bounds 展开给所有取数函数用的，
    不是每个函数各读一次参数——各读一次的话，将来加一处筛选就会有某个块忘了跟。
    """
    conf = settings.load()
    connection = get_conn()
    span = params.summary_range(conf, request.args)
    bounds = {"start": span["start"] or None, "end": span["end"] or None}

    codes = repo.currencies(connection, **bounds)
    counted = repo.expense_ranking_counts(connection, **bounds)
    return jsonify(
        range=span,
        # 账本本身是不是空的，跟「这个区间里没有记录」是两回事：共用一句话会让人
        # 跑去看导入页，而问题在区间上。
        has_data=repo.coverage(connection)["total"] > 0,
        currencies=codes,
        multi_currency=len(codes) > 1,
        display_currency=conf.display_currency,
        overall=rows(repo.overall_totals(connection, **bounds)),
        # limit=None：这张表在页面上是「图上没画全的月份，下面查得到」的兑现处，
        # 悄悄截断等于跟读者撒谎。图的 12 个月上限在图表组件那一侧。
        months=rows(repo.by_month(connection, limit=None, **bounds)),
        categories=rows(repo.by_source_category(connection, **bounds)),
        large=rows(repo.large_transactions(connection, **bounds)),
        # 每币种前 20 条；counts 给的是**总数**，前端拿它说「另有多少笔」。
        ranking=rows(repo.expense_ranking(connection, **bounds)),
        ranking_counts=counted,
    )


# --------------------------------------------------------------------------
# 账目
# --------------------------------------------------------------------------

def list_counts(connection, query) -> tuple[int, int]:
    """（符合条件的条数，其中隐藏的条数）。两个数同一个口径，都是全表。

    隐藏那一档不能只数这一页渲染出来的行：一段 200 条以上的列表上会写成个偏小
    的数，被隐的那笔要是正好不在这段里，页面上干脆什么都不说。
    """
    common = {
        "start": query["start"] or None,
        "end": query["end"] or None,
        "account_id": query["account_id"],
        "direction": query["direction"] or None,
        "search": query["search"] or None,
    }
    total = repo.count_transactions(connection, **common, hidden=query["hidden"] or None)
    if query["hidden"] == "1":
        return total, total  # 这一档整页都是隐藏的
    if query["hidden"] == "0":
        return total, 0  # 这一档一条隐藏的都不列
    return total, repo.count_transactions(connection, **common, hidden="1")


@blueprint.get("/transactions")
def transaction_list():
    conf = settings.load()
    connection = get_conn()
    query = params.list_query(request.args, conf)
    total, hidden_total = list_counts(connection, query)

    offset = max(request.args.get("offset", type=int) or 0, 0)
    found = repo.list_transactions(
        connection,
        start=query["start"] or None,
        end=query["end"] or None,
        account_id=query["account_id"],
        direction=query["direction"] or None,
        search=query["search"] or None,
        hidden=query["hidden"] or None,
        limit=query["limit"],
        offset=offset,
    )
    # 合并行只带锚点那一笔的文字，要看全组成员得另外取——而且只取这一段用得到的组。
    members: dict[int, list] = {}
    for member in repo.group_members(
        connection, [item["group_id"] for item in found if item["group_id"]]
    ):
        members.setdefault(member["group_id"], []).append(member)

    accounts = repo.accounts(connection)
    start, end = query["start"], query["end"]
    return jsonify(
        rows=rows(found),
        members={str(key): rows(value) for key, value in members.items()},
        total=total,
        hidden_total=hidden_total,
        hint=params.list_hint(total, hidden_total),
        limit=query["limit"],
        offset=offset,
        remaining=max(total - offset - len(found), 0),
        # 两个日期填反了。不吭声的话这就是一个干干净净的「没有符合条件的记录」，
        # 看着跟「这个月真没记账」一模一样。
        backwards=bool(start and end and start > end),
        # 筛选栏收起来之后，得让人一眼看出这一屏是筛过的——不然看着就像全部记录，
        # 而汇总和它又对不上。
        active_filters=params.active_filter_labels(
            accounts,
            start=start,
            end=end,
            direction=query["direction"],
            account_id=query["account_id"],
            search=query["search"],
            hidden=query["hidden"],
        ),
        filters={
            "start": start,
            "end": end,
            "direction": query["direction"],
            "account": query["account_id"],
            "q": query["search"],
            "hidden": query["hidden"],
        },
        accounts=_account_rows(connection),
        categories=repo.transaction_categories(connection),
        group_count=repo.count_groups(connection),
        display_currency=conf.display_currency,
    )


@blueprint.get("/transactions/<int:txn_id>")
def transaction_detail(txn_id: int):
    """一笔的详细信息：列表是拿来扫的，这里是拿来对账的。"""
    connection = get_conn()
    found = repo.get_transaction(connection, txn_id)
    if found is None:
        return _not_found("没有这一笔。")
    try:
        raw = json.loads(found["raw_json"] or "{}")
    except ValueError:
        raw = {}
    return jsonify(
        transaction=row(found),
        raw=raw if isinstance(raw, dict) else {},
        record=row(
            repo.get_import(connection, found["import_id"]) if found["import_id"] else None
        ),
        display_currency=settings.load().display_currency,
        # 「← 交易记录」要落在这一笔现在待的那个月：被并进别个月的组之后
        # （组落在最早那一笔上），照它自己的日期回，落在的是空屏。
        back_month=repo.effective_month(connection, txn_id) or found["occurred_date"][:7],
    )


@blueprint.get("/transactions/new")
def transaction_new():
    """记一笔那张表单的空初值，外加下拉候选。"""
    conf = settings.load()
    connection = get_conn()
    return jsonify(
        values=entry.blank(conf.display_currency),
        errors=[],
        accounts=_account_rows(connection),
        currencies=repo.transaction_currencies(connection),
        categories=repo.transaction_categories(connection),
        display_currency=conf.display_currency,
        back_month=None,
    )


# --------------------------------------------------------------------------
# 合并组
# --------------------------------------------------------------------------

@blueprint.get("/groups")
def groups():
    connection = get_conn()
    found = repo.list_groups(connection)
    members: dict[int, list] = {}
    for member in repo.group_members(connection, [item["id"] for item in found]):
        members.setdefault(member["group_id"], []).append(member)
    return jsonify(
        groups=rows(found),
        members={str(key): rows(value) for key, value in members.items()},
        display_currency=settings.load().display_currency,
    )


# --------------------------------------------------------------------------
# 导入记录
# --------------------------------------------------------------------------

@blueprint.get("/imports")
def imports():
    limit = request.args.get("limit", type=int) or 8
    return jsonify(imports=rows(repo.list_imports(get_conn(), limit)))


@blueprint.get("/imports/<int:import_id>")
def import_detail(import_id: int):
    connection = get_conn()
    record = repo.get_import(connection, import_id)
    if record is None:
        return _not_found("没有这一次导入。")
    issues = connection.execute(
        """
        SELECT * FROM import_issues WHERE import_id = ?
        ORDER BY COALESCE(row_number, 0), id
        """,
        (import_id,),
    ).fetchall()
    return jsonify(
        import_=row(record),
        issues=rows(issues),
        sample=rows(repo.transactions_for_import(connection, import_id, limit=50)),
    )


# --------------------------------------------------------------------------
# 设置与列映射
# --------------------------------------------------------------------------

@blueprint.get("/settings")
def settings_page():
    """设置的初值。

    **保持字符串形态**（``large_out: "500"``），不改成一堆 ``_cents`` 整数：
    这张表单的校验（认不认得出币种、是不是正数、有没有重复行）和那些文案都在
    ``settings.read_form`` 里，原样复用就等于原样保住了全部行为。顺带也满足
    「JSON 里没有浮点」——数字是字符串。
    """
    conf = settings.load()
    return jsonify(
        values=settings.as_form(conf),
        errors=[],
        saved=False,
        path=str(config.user_settings_path()),
    )


@blueprint.get("/profiles")
def profiles_page():
    """列映射一览。真实账单格式对不上时，这一页是要看的地方。"""
    loaded = load_all()
    staged = ingest.store.latest()
    headers = staged.grid.header if staged and staged.grid else []
    return jsonify(
        profiles=[
            {
                "name": profile.name,
                "title": getattr(profile, "title", profile.name),
                "origin": getattr(profile, "origin", ""),
                "problems": validate(profile),
            }
            for profile in loaded
        ],
        headers=headers,
        staged=(
            {"token": staged.token, "filename": staged.filename} if staged else None
        ),
        user_dir=str(config.user_profiles_dir()),
        builtin_dir=str(config.builtin_profiles_dir()),
    )


# --------------------------------------------------------------------------
# 错误
# --------------------------------------------------------------------------

def _not_found(message: str):
    return jsonify(error={"code": "not_found", "message": message}), 404
