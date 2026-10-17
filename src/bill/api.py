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
from datetime import date, datetime

from flask import Blueprint, current_app, g, jsonify, request, session

from . import (
    auth,
    categories,
    config,
    dashboard as overview,
    dates,
    db,
    entry,
    ingest,
    params,
    repo,
    settings,
    themes,
)
from .errors import BillError, ProfileError, UploadError
from .profiles import load_all, validate

blueprint = Blueprint("api", __name__, url_prefix="/api")

__all__ = ["blueprint", "get_conn", "current_auth", "list_counts", "attempts"]

# 猜密码的失败计数挂在 ``app.extensions`` 上，而不是这个模块的全局变量：
# 全局的话它就成了**进程级**状态，一次进程里建两个 app（测试就是这么干的）
# 会共用同一个计数器，前面用例试错几次，后面的用例就撞上 429——一个跟被测行为
# 毫无关系的失败。挂在 app 上，生产里仍然是「一个进程一个计数器」，测试里
# 每个 app 一份，两边都对。
ATTEMPTS_KEY = "bill.attempts"


def attempts() -> auth.Attempts:
    """这次请求所属 app 的失败计数。"""
    return current_app.extensions[ATTEMPTS_KEY]

# 改状态的接口只收这几种 Content-Type。
#
# 这是一层**零成本的加料**，不是 CSRF 的全部依靠：会话 cookie 是 SameSite=Lax，
# 跨站的表单提交本来就带不上它。再加这条是因为跨站 ``<form>`` 只能发出
# form-urlencoded / multipart / text-plain 三种，而 JSON 它发不出来——于是
# 「必须带 Content-Type: application/json」等于又堵掉一条路，代价是零。
# 上传是唯一带文件的入口，只能走 multipart，单独放行。
JSON_ONLY = {"application/json"}
UPLOAD_ENDPOINT = "api.create_import"


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


# 合并后账本里的一行，界面真正用得上的列。
LEDGER_FIELDS = (
    "id",
    "group_id",
    "member_count",
    "occurred_at",
    "occurred_date",
    "counterparty",
    "description",
    "memo",
    "effective_category",
    "display_cents",
    "display_signed_cents",
    "display_currency",
)


# 交易列表上一行用得上的列。`raw_json` / `dedup_key` / `content_hash` 不在里面：
# 一次 200 行，那几列（尤其 raw_json）又长又没人看，发过去只是白白占带宽。
LIST_FIELDS = (
    "id",
    "occurred_at",
    "occurred_date",
    "direction",
    "amount_cents",
    "currency",
    "counterparty",
    "counterparty_account",
    "description",
    "memo",
    "effective_category",
    "account_id",
    "account_name",
    "group_id",
    "member_count",
    "hidden",
    "edited_at",
    "display_cents",
    "is_large",
)

MEMBER_FIELDS = (
    "id",
    "group_id",
    "occurred_at",
    "direction",
    "amount_cents",
    "currency",
    "counterparty",
    "description",
    "effective_category",
    "display_cents",
    # display_cents 是哪个币种的数。跟 amount_cents / currency 一起发过去，
    # 界面才能分清「这个数是换算来的」和「这个数本来就是原币」——后者再跟着
    # 标一份原币金额就是同一个数印两遍。
    "display_currency",
)


def transaction_rows(result) -> list[dict]:
    """交易列表的行。合并行的 id 归一成 null，跟账本行一个规矩。"""
    return [_listed(item, LIST_FIELDS, null_group_id=True) for item in result]


def member_rows(result) -> list[dict]:
    """合并组的组员。它们是**真实记录**，id 不动。"""
    return [_listed(item, MEMBER_FIELDS) for item in result]


def _listed(item, fields, *, null_group_id: bool = False) -> dict:
    row = _project(item, fields, null_group_id=null_group_id)
    # 分类在列表上退化成一个 emoji，分类名挂在 tooltip 上：这一列窄，分类名
    # 单独占一列会让别的列跟着挤。映射表（含关键词兜底）在 `categories.py`，
    # 在这儿算好发出去——搬到前端就等于把那张表再抄一份。
    row["emoji"] = categories.emoji(row.get("effective_category"))
    return row


def _project(item, fields, *, null_group_id: bool = False) -> dict:
    data = dict(item)
    row = {key: data.get(key) for key in fields}
    if null_group_id and row.get("group_id") is not None:
        # `_effective_cte` 给合成行的 id 是 `-组号` 那个哨兵。前端据此判断
        # 「这一行点不进详情页」——「字段在不在」比「id 是不是小于零」可靠。
        row["id"] = None
    return row


def ledger_rows(result) -> list[dict]:
    """投影成界面上那个「一笔」的形状。

    两件事：

    1. **别把库里的身份发出去。** ``large_transactions`` 取的是 ``t.*``，里面带着
       ``raw_json``、``dedup_key``、``content_hash``——那是记录的身份，没有理由
       跟着响应跑到浏览器上。顺便让大额和开销排行两个列表长得一样，前端一套
       类型就够。
    2. **合并行的 id 归一成 null。** ``_effective_cte`` 给合成行的 id 是 ``-组号``，
       那是个哨兵、不是一条记录的 id。前端据此判断「这一行点不进详情页」——
       「字段在不在」比「id 是不是小于零」可靠得多。
    """
    out = []
    for item in result:
        data = dict(item)
        row = {key: data.get(key) for key in LEDGER_FIELDS}
        if row["group_id"] is not None:
            row["id"] = None
        # 跟交易列表同一个口径：分类退化成 emoji，名字留在 effective_category 里。
        row["emoji"] = categories.emoji(row.get("effective_category"))
        out.append(row)
    return out


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

def _account_overview(connection, conf, *, start, end) -> list[dict]:
    """首页账户区：账户 + 上次导入时间 + 区间内的收支。

    「距今几天」在**服务端**按显示时区算。``imports.uploaded_at`` 本来就是按
    那个时区写的墙上时钟，交给前端再算一遍等于多一套时区逻辑，两边会在跨天
    时差出一天——而屏幕上什么都不说。

    从没导入过的账户 ``last_import_at`` 为空，``never_imported`` 为真；它和
    「导入过但太久没导」都算提醒对象，但界面上的话不一样。
    """
    totals: dict[int, list[dict]] = {}
    for item in repo.account_totals(connection, start=start, end=end):
        row = dict(item)
        account_id = row.pop("account_id")
        totals.setdefault(account_id, []).append(row)

    today = datetime.now(conf.tz()).date()
    overview = []
    for record in repo.account_imports(connection):
        last = record["last_import_at"]
        days = (today - date.fromisoformat(last[:10])).days if last else None
        never = last is None
        # stale_days = 0 是「不提醒」的明确选择，不是「0 天没导入就提醒」。
        stale = conf.stale_days > 0 and (never or days >= conf.stale_days)
        overview.append(
            {
                "id": record["id"],
                "name": record["name"],
                "kind": record["kind"],
                "last_import_at": last,
                "days_since_import": days,
                "never_imported": never,
                "stale": stale,
                "totals": totals.get(record["id"], []),
            }
        )
    return overview


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
        large=ledger_rows(repo.large_transactions(connection, **bounds)),
        # 每币种前 20 条；counts 给的是**总数**，前端拿它说「另有多少笔」。
        ranking=ledger_rows(repo.expense_ranking(connection, **bounds)),
        ranking_counts=counted,
        # 账户区：金额跟随当前区间，导入时间不跟——它是账户自己的属性。
        accounts=_account_overview(
            connection, conf, start=bounds["start"], end=bounds["end"]
        ),
    )


@blueprint.get("/dashboard")
def dashboard():
    """首页概览：区间数字、预测、平均、趋势、两个排行。

    跟 ``/summary`` 共用同一套区间参数（``params.summary_range``），所以两个
    接口对「本月」的边界不可能各说各话。这里只多一个 ``currency``：多币种时
    一次看一个币种，绝不把两个币种加进同一张卡。
    """
    conf = settings.load()
    connection = get_conn()
    body = overview.build(connection, conf, request.args)
    # 排行里的每一行还是库里的行，这里投影成界面的形状——顺便把合并行的
    # 哨兵 id 归一成 null，前端据此决定点不点得进详情页。
    body["singles"] = ledger_rows(body["singles"])
    return jsonify(body)


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
        "category": query["category"] or None,
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
    query = params.list_query(request.args)
    total, hidden_total = list_counts(connection, query)

    offset = max(request.args.get("offset", type=int) or 0, 0)
    found = repo.list_transactions(
        connection,
        start=query["start"] or None,
        end=query["end"] or None,
        account_id=query["account_id"],
        direction=query["direction"] or None,
        search=query["search"] or None,
        category=query["category"] or None,
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
        rows=transaction_rows(found),
        members={str(key): member_rows(value) for key, value in members.items()},
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
            category=query["category"],
            hidden=query["hidden"],
        ),
        filters={
            "start": start,
            "end": end,
            "direction": query["direction"],
            "account": query["account_id"],
            "q": query["search"],
            "category": query["category"],
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
        # 改一笔那张表单的初值。放在这里而不是让前端自己拼：`from_row` 里带着
        # 金额的格式化（分 → '32.50'）和时间那个 'T' 分隔符，那是**服务端的
        # 一套写法**，在前端再抄一遍就会有两处各写各的。
        #
        # 也带上 errors / accounts / currencies / categories / back_month：
        # 这样「改一笔」和「记一笔」回的是同一个形状，前端两份表单共用一套类型。
        values=entry.from_row(found),
        errors=[],
        raw=raw if isinstance(raw, dict) else {},
        record=row(
            repo.get_import(connection, found["import_id"]) if found["import_id"] else None
        ),
        accounts=_account_rows(connection),
        currencies=repo.transaction_currencies(connection),
        categories=repo.transaction_categories(connection),
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
        categories=repo.transaction_categories(connection),
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
# 登录
# --------------------------------------------------------------------------

def _login_error(message: str, status: int):
    return jsonify(error={"code": "unauthenticated", "message": message}), status


@blueprint.post("/session")
def login():
    """没设密码时没有这回事，直接说成功——跟旧界面「没设密码就没有登录页」一致。"""
    conf = current_auth()
    if conf is None:
        return jsonify(ok=True, auth_enabled=False)

    body = request.get_json(silent=True) or {}
    username = body.get("username") if isinstance(body, dict) else ""
    password = body.get("password") if isinstance(body, dict) else ""
    source = request.remote_addr or "?"

    # **先验账号密码，再数失败。** 这道闸门是给猜的人设的，不该把输对了的那个人
    # 一起锁在门外：账本就这一组凭据，没有第二把钥匙。
    if conf.check(username, password):
        attempts().clear(source)
        session.clear()  # 换一个新的会话，不接着用登录之前那一个
        session["logged_in"] = True
        session.permanent = True
        return jsonify(ok=True, auth_enabled=True)

    attempts().failed(source)
    wait = attempts().waiting(source)
    if wait > 0:
        return _login_error(f"错的次数太多了，等 {int(wait) + 1} 秒再试。", 429)
    # 账号错和密码错**用同一句**：分开说等于告诉猜的人「账号对了、接着猜密码」。
    return _login_error("账号或密码不对。", 401)


@blueprint.delete("/session")
def logout():
    session.clear()
    return "", 204


# --------------------------------------------------------------------------
# 表单适配
# --------------------------------------------------------------------------

class JsonForm:
    """把一份 JSON 对象包成表单适配器。

    ``settings.read_form`` 和 ``entry.read`` 收的都是 werkzeug 的 multidict
    （``get`` / ``getlist``）。包一层比把那两处改成两套接口便宜得多——而且
    **校验和文案全都留在了原处**，那才是表单行为真正的所在（哪一句提示对应
    哪一种填错，测试逐条盯着）。

    复选框从 JSON 来的是布尔：表单里「勾上」才有这个字段，所以 ``False``
    要还原成「没有」，不然 ``bool(data.get(...))`` 永远为真。
    """

    def __init__(self, data: dict | None):
        self._data = data if isinstance(data, dict) else {}

    def get(self, key, default=None):
        value = self._data.get(key, default)
        if isinstance(value, bool):
            return "on" if value else None
        return value

    def getlist(self, key):
        value = self._data.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]

    def __contains__(self, key) -> bool:
        return key in self._data


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# 记一笔 / 改一笔
# --------------------------------------------------------------------------

def _form_payload(connection, values, errors, *, transaction=None) -> dict:
    """表单回显：填过的东西原样交回去，不让人重填一遍。"""
    return {
        "values": values,
        "errors": errors,
        "transaction": row(transaction),
        # 「回到交易记录」落在这一笔**现在待的那个月**上：它可能已经被并进别个月
        # 的组（组落在最早那一笔上），照它自己的日期回，落在的是空屏。
        "back_month": (
            repo.effective_month(connection, transaction["id"]) if transaction else None
        ),
        "accounts": _account_rows(connection),
        "currencies": repo.transaction_currencies(connection),
        "categories": repo.transaction_categories(connection),
        "display_currency": settings.load().display_currency,
    }


@blueprint.post("/transactions")
def transaction_create():
    connection = get_conn()
    conf = settings.load()
    draft = entry.read(JsonForm(_body()), display_currency=conf.display_currency)
    if not draft.ok:
        return (
            jsonify(
                error={"code": "validation", "message": "；".join(draft.errors)},
                **_form_payload(connection, _body(), draft.errors),
            ),
            400,
        )

    with connection:
        account_id = repo.ensure_account(connection, draft.account_name, "manual")
        txn_id = repo.insert_manual_transaction(
            connection,
            account_id=account_id,
            transaction=draft.transaction(),
            category=draft.category,
        )
        _autodetect_around(connection, conf, draft.occurred_at)
        # 刚记的这笔要是立刻被并进了别个月的组，就报它现在待的那个月
        month = repo.effective_month(connection, txn_id) or draft.month
    return jsonify(id=txn_id, month=month), 201


@blueprint.patch("/transactions/<int:txn_id>")
def transaction_update(txn_id: int):
    connection = get_conn()
    found = repo.get_transaction(connection, txn_id)
    if found is None:
        return _not_found("没有这一笔。")

    fields = _body()
    draft = entry.read(JsonForm(fields), display_currency=settings.load().display_currency)
    if not draft.ok:
        # 账户那一格是只读的，不会随表单提交上来——拿库里的补上，别让用户在改错
        # 一处的时候连这是哪个账户都看不见。
        return (
            jsonify(
                error={"code": "validation", "message": "；".join(draft.errors)},
                **_form_payload(
                    connection, {**entry.from_row(found), **fields}, draft.errors,
                    transaction=found,
                ),
            ),
            400,
        )

    with connection:
        repo.update_transaction(connection, txn_id, draft.fields())
        month = repo.effective_month(connection, txn_id) or draft.month
    after = repo.get_transaction(connection, txn_id)
    # 改完这一笔在列表上落在哪一行：还在组里的话，列表画的是一行合并记录，
    # 前端拿 `group_id` 才知道回去该亮哪一行；组散了才是这一笔自己。
    return jsonify(id=txn_id, month=month, group_id=after["group_id"] if after else None)


@blueprint.delete("/transactions/<int:txn_id>")
def transaction_delete(txn_id: int):
    connection = get_conn()
    with connection:
        if not repo.delete_transaction(connection, txn_id):
            return _not_found("没有这一笔。")
    return "", 204


def _toggle_hidden(txn_id: int, hidden: bool):
    connection = get_conn()
    with connection:
        if not repo.set_hidden(connection, txn_id, hidden):
            return _not_found("没有这一笔。")
    # 页头那两个数得服务端算准了交回去——「全表还隐藏着几笔」客户端算不出来。
    conf = settings.load()
    query = params.list_query(request.args)
    total, hidden_total = list_counts(connection, query)
    return jsonify(
        hidden=hidden,
        total=total,
        hidden_total=hidden_total,
        # 当前这一档排不排得下它。排不下就把那一行从列表里撤掉，不然「只看隐藏」
        # 里还原一笔，它还会赖在列表上不走。
        keep=not (
            (query["hidden"] == "0" and hidden)
            or (query["hidden"] == "1" and not hidden)
        ),
        hint=params.list_hint(total, hidden_total),
    )


@blueprint.post("/transactions/<int:txn_id>/hide")
def transaction_hide(txn_id: int):
    return _toggle_hidden(txn_id, True)


@blueprint.delete("/transactions/<int:txn_id>/hidden")
def transaction_unhide(txn_id: int):
    return _toggle_hidden(txn_id, False)


def _autodetect_around(connection, conf, moment: str) -> int:
    """手动记一笔之后，顺手看一眼这附近有没有 AA。

    范围只在那一笔前后各一个窗口，不扫全库——手动记一笔不该顺带把整个账本重算一遍。
    """
    if not (conf.merge_enabled or conf.merge_rollup) or not moment:
        return 0
    reach = conf.merge_window_hours
    return repo.autodetect(
        connection,
        since=dates.shift(moment, hours=-reach),
        until=dates.shift(moment, hours=reach),
        aa=conf.merge_enabled,
    )


@blueprint.patch("/transactions")
def transactions_bulk_reclassify():
    """勾选之后把分类一起改掉。"""
    ids = [value for value in map(params.as_int, _body().get("transaction_ids") or []) if value]
    if not ids:
        return _error("validation", "先在左边勾选几笔。", 400)
    with get_conn() as connection:
        changed = repo.reclassify(connection, ids, (_body().get("category") or "").strip())
    return jsonify(changed=changed)


# --------------------------------------------------------------------------
# 合并组
# --------------------------------------------------------------------------

@blueprint.post("/groups")
def group_create():
    """把勾选的几笔合成一组。"""
    ids = [value for value in map(params.as_int, _body().get("transaction_ids") or []) if value]
    if not ids:
        return _error("validation", "先在左边勾选几笔。", 400)
    connection = get_conn()
    try:
        with connection:
            group_id = repo.create_group(connection, origin="manual", transaction_ids=ids)
    except BillError as error:
        # 「已经并进别组了」「有币种没配汇率」这些是**冲突**，不是填错了：请求本身
        # 没问题，是当前状态不允许。前端据此提示，而不是当表单错误去标红字段。
        return _error("conflict", str(error), 409)
    return jsonify(group_id=group_id), 201


@blueprint.post("/groups/scan")
def groups_scan():
    """把整个账本重新扫一遍，补检测用。"""
    connection = get_conn()
    with connection:
        created = repo.autodetect(connection)
    return jsonify(created=created)


@blueprint.delete("/groups/<int:group_id>")
def group_dissolve(group_id: int):
    connection = get_conn()
    with connection:
        if not repo.dissolve_group(connection, group_id, veto=True):
            return _not_found("没有这一组，可能已经被取消了。")
    return "", 204


@blueprint.patch("/groups/<int:group_id>")
def group_update(group_id: int):
    body = _body()
    connection = get_conn()
    with connection:
        saved = repo.update_group(
            connection,
            group_id,
            title=(body.get("title") or "").strip(),
            category=(body.get("category") or "").strip(),
            note=(body.get("note") or "").strip(),
        )
    if not saved:
        # 组已经没了（另一个标签页取消过，或者这一页放了太久）。说「保存成功」
        # 比报错更糟：改的东西根本没人收。
        return _not_found("没有这一组，可能已经被取消了。")
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# 设置
# --------------------------------------------------------------------------

def _rates_to_form(values: dict) -> dict:
    """把 ``rates: [{code, rate}]`` 摊成 ``rate_code`` / ``rate_value`` 两列。

    GET 回来的就是 ``rates`` 这个形状，PUT 也收同一个形状——线上格式对称，
    前端不用记两套。往下交给 ``settings.read_form`` 时再还原成它认的那两列，
    校验逻辑一个字都不用动。
    """
    data = dict(values)
    entries = data.pop("rates", None)
    if isinstance(entries, list):
        data["rate_code"] = [
            str(item.get("code", "")) for item in entries if isinstance(item, dict)
        ]
        data["rate_value"] = [
            str(item.get("rate", "")) for item in entries if isinstance(item, dict)
        ]
    return data


@blueprint.put("/settings")
def settings_save():
    body = _body()
    conf, values, errors = settings.read_form(JsonForm(_rates_to_form(body)))
    if errors:
        # 校验没过就把填过的原样交回去，不让人重填一遍。
        return jsonify(error={"code": "validation", "message": "；".join(errors)},
                       errors=errors, values=values), 400
    try:
        settings.save(conf)
    except BillError as error:
        return _error("validation", str(error), 400)
    return jsonify(saved=True, values=values)


# --------------------------------------------------------------------------
# 导入：上传 → 预览 → 确认
# --------------------------------------------------------------------------

def _preview_payload(preview) -> dict:
    """预览体。

    ``rows`` 按 ``config.PREVIEW_ROWS`` 截断，并明说截了——十万行的账单整个塞进
    JSON 里，先撑不住的是浏览器。``counts`` / ``totals`` 仍然是**全量**的，
    所以「导进来几条」这个数不受截断影响。
    """
    detected = preview.detected
    rows = preview.rows(config.PREVIEW_ROWS)
    totals = preview.totals_by_currency()
    issues = preview.plan.issues if preview.plan else []
    seen: dict[str, int] = {}
    grouped: list[dict] = []
    for issue in issues:
        seen[issue.code] = seen.get(issue.code, 0) + 1
        if seen[issue.code] <= 10:
            grouped.append(
                {
                    "code": issue.code,
                    "level": issue.level,
                    "message": issue.message,
                    "row_number": issue.row_number,
                }
            )
    return {
        "token": preview.token,
        "filename": preview.filename,
        "size_bytes": preview.size_bytes,
        "profile_name": preview.profile_name,
        "error": preview.error,
        "notes": list(preview.notes),
        "detected": (
            {"name": detected.profile.name, "score": detected.score, "confident": True}
            if detected
            else None
        ),
        "candidates": [
            {
                "name": candidate.profile.name,
                "score": candidate.score,
                "confident": candidate.confident,
            }
            for candidate in preview.candidates
        ],
        "profiles": [{"name": profile.name, "title": profile.title} for profile in load_all()],
        # 认不出表头时把文件开头摊出来——这就是用户拿去改配置的信息。
        "raw_preview": [
            {"row_number": number, "cells": cells}
            for number, cells in preview.raw_preview(15)
        ],
        "counts": preview.counts(),
        "totals": [
            {"currency": currency, "income": income, "expense": expense}
            for currency, (income, expense) in totals.items()
        ],
        "issues": grouped,
        "issues_total": len(issues),
        "rows": [
            {
                "row_number": parsed.row_number,
                "status": parsed.status,
                "duplicate": parsed.duplicate,
                "possible_duplicate": parsed.possible_duplicate,
                "occurred_at": parsed.transaction.occurred_at,
                "direction": str(parsed.transaction.direction),
                "amount_cents": parsed.transaction.amount_cents,
                "currency": parsed.transaction.currency,
                "counterparty": parsed.transaction.counterparty,
                "description": parsed.transaction.description,
                "source_category": parsed.transaction.source_category,
            }
            for parsed in rows
        ],
        "max_rows": config.PREVIEW_ROWS,
        "truncated": (preview.plan is not None and len(preview.plan.rows) > config.PREVIEW_ROWS),
    }


@blueprint.post("/imports")
def create_import():
    """收下文件，先解析一遍给用户看。**这一步还不写库。**"""
    file = request.files.get("file")
    if file is None or not file.filename:
        return _error("validation", "请先选一个账单文件。", 400)
    try:
        preview = ingest.stage(get_conn(), file.filename, file.read())
    except UploadError as error:
        return _error("validation", str(error), 400)
    return jsonify(preview=_preview_payload(preview)), 201


def _expired(message: str):
    """预览没了：410 而不是 404。

    它是**曾经存在、现在过期**的东西，不是「你找错了地址」。前端据此显示
    「重新上传一次就行」＋一个回上传页的按钮，而不是一个干巴巴的找不到。
    """
    return _error("preview_expired", message, 410)


@blueprint.get("/imports/previews/<token>")
def import_preview(token: str):
    try:
        preview = ingest.store.get(token)
    except ingest.PreviewExpired as error:
        return _expired(str(error))
    return jsonify(preview=_preview_payload(preview))


@blueprint.post("/imports/previews/<token>/profile")
def import_reprofile(token: str):
    """换一个列映射重新解析。文件还在暂存区里，不用重传。"""
    name = (_body().get("profile") or "").strip()
    try:
        if name:
            ingest.reprofile(get_conn(), token, name)
        preview = ingest.store.get(token)
    except ingest.PreviewExpired as error:
        return _expired(str(error))
    except ProfileError as error:
        # 这份配置本身有问题：把它记在预览上，让页面照常显示、并说明为什么。
        ingest.store.get(token).error = str(error)
        preview = ingest.store.get(token)
    return jsonify(preview=_preview_payload(preview))


@blueprint.post("/imports/previews/<token>/confirm")
def import_confirm(token: str):
    try:
        import_id = ingest.confirm(get_conn(), token)
    except ingest.PreviewExpired as error:
        return _expired(str(error))
    except BillError as error:
        return _error("validation", str(error), 400)
    return jsonify(import_id=import_id), 201


@blueprint.delete("/imports/previews/<token>")
def import_cancel(token: str):
    ingest.store.drop(token)
    return "", 204


@blueprint.delete("/imports/<int:import_id>")
def import_delete(import_id: int):
    connection = get_conn()
    with connection:
        if not repo.delete_import(connection, import_id):
            return _not_found("没有这一次导入。")
    return "", 204


# --------------------------------------------------------------------------
# 错误
# --------------------------------------------------------------------------

def _error(code: str, message: str, status: int):
    """统一的错误体。

    ``message`` 用的是各自领域里原来那串中文，一个字都没改——那些提示语是
    校验结果的一部分，测试逐条盯着。
    """
    return jsonify(error={"code": code, "message": message}), status


def _not_found(message: str):
    return _error("not_found", message, 404)


def unauthenticated():
    """没登录时接口回的那个响应。

    接口**不能**像旧页面那样 303 跳到登录页：SPA 收到的是一个 303，跟着去
    拿回来的是一整页 HTML，``response.json()`` 直接抛，屏幕上就是白屏。它要的
    是一个能认出来的 ``code``，好据此弹登录视图。
    """
    return _error("unauthenticated", "先登录。", 401)
