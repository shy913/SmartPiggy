"""Flask 应用：路由在这里装配，业务逻辑都在别的模块里。

跑起来：

    uv run bill
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import (
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from . import (
    api,
    auth,
    categories,
    charts,
    config,
    dates,
    db,
    entry,
    ingest,
    params,
    repo,
    settings,
    themes,
)
from .api import list_counts
from .errors import BillError, ProfileError, UploadError
from .money import format_amount
from .params import (
    active_filter_labels,
    clean_date,
    default_range,
    list_hint,
    list_query,
)
from .params import as_int as _as_int
from .profiles import load_all, validate
from .readers import ACCEPT_ATTR

__all__ = ["create_app", "main"]

_started_at = datetime.now(timezone.utc)

# 不登录也能进的地方：登录页自己，加一个给监控用的 /healthz（只回 uptime，
# 不带任何账目数据），加静态文件——登录页的样式表就在那儿。
# 这个名单是**逐个点名**的：没点到的都要先登录，认不出来的 URL（那种请求的
# endpoint 是 None）也不例外——宁可让 404 也先走一趟登录页。
# assets / app_shell 在里面：外壳与它的 JS/CSS 都**不含账目数据**，要登录的是
# /api/*。把外壳也纳入闸门的话，没登录的人打开首页会被拽去登录页——而那个登录
# 视图本身就长在 SPA 里，先有鸡还是先有蛋。
OPEN_ENDPOINTS = frozenset(
    {"login_page", "login", "logout", "healthz", "static", "assets", "app_shell"}
)

# 前端构建产物。**不进版本库**（见 .gitignore），部署时现构建——仓库里就不会
# 有一份「改了源码忘了重建」的偏差。
APP_BUILD_DIR = Path(__file__).parent / "static" / "app"
# 外壳里那个 data-theme 占位符。服务端填好再吐出去，第一帧主题就是对的。
THEME_SLOT = "%BILL_THEME%"
# 外壳现在挂在 /app 下，跟旧界面并存；切换那一批会把它挪到根路径。
SHELL_PREFIX = "/app"

# 接口那边不登录也能打的几个。同样**逐个点名**，而且只点登录本身：
# 「问一句我登没登」「登录」「退出」，除此之外一律 401。
# （SPA 的页面外壳是另一回事，见下面 require_login 里的说明。）
OPEN_API_ENDPOINTS = frozenset({"api.session_state", "api.login", "api.logout"})

USAGE = "用法：bill [--set-password]"

# 顶部导航的四个区。当前在哪一区靠它给那一条加高亮。
#
# 一个区摊在好几个 endpoint 上（「记一笔」「改一笔」「合并组」都在交易记录
# 那条线上），所以这里是按 endpoint 归并成区，而不是让模板各自去猜——
# 新增页面时忘了在某处补一笔，页面就会「一条都不亮」，一眼看得见。
NAV_SECTIONS = {
    "summary": "summary",
    "summary_legacy": "summary",
    "transactions": "transactions",
    "transactions_bulk": "transactions",
    "transaction_new": "transactions",
    "transaction_create": "transactions",
    "transaction_edit": "transactions",
    "transaction_update": "transactions",
    "transaction_delete": "transactions",
    "transaction_detail": "transactions",
    "transaction_hide": "transactions",
    "transaction_unhide": "transactions",
    "groups_page": "transactions",
    "groups_scan": "transactions",
    "group_dissolve": "transactions",
    "group_update": "transactions",
    "import_upload": "import",
    "receive_import": "import",
    "import_preview": "import",
    "import_reprofile": "import",
    "import_confirm": "import",
    "import_cancel": "import",
    "imports": "import",
    "import_detail": "import",
    "import_delete": "import",
    "profiles_page": "import",
    "settings_page": "settings",
    "settings_save": "settings",
}


def nav_section(endpoint: str | None) -> str:
    """这个 endpoint 属于哪个导航区。不在任何一区（登录页、404…）就返回空串。"""
    return NAV_SECTIONS.get(endpoint or "", "")


def uptime_seconds() -> int:
    return int((datetime.now(timezone.utc) - _started_at).total_seconds())


def _next_target(value: str | None) -> str:
    """登录之后要回到哪一页。

    只收本应用里的路径：这个值是从 URL 和表单里来的，放行 `//evil.example`
    或者 `https://…` 就等于拿自己的登录页给别人做钓鱼跳板。

    控制字符也一并挡掉。它们会被原样交给 ``redirect``，而 werkzeug 拒绝把
    带换行的地址写进响应头——**输对密码那一刻**抛 500，等于一条链接就能让人
    登不进来。（挡的是控制字符，不是非 ASCII：搜索关键词、备注里都有中文，
    那些路径要能原样回得来。）
    """
    if not value or not value.startswith("/") or value.startswith("//"):
        return ""
    if "\\" in value:
        # 有的浏览器把反斜杠当斜杠看，`/\evil.example` 就溜出去了。
        return ""
    if any(character < " " or character == "\x7f" for character in value):
        return ""
    return value


def _autodetect_around(connection, conf, moment: str) -> int:
    """手动记一笔之后，顺手看一眼这附近有没有 AA。

    手工补的账跟导入进来的一样该合并：补了一笔替朋友垫的支出，又把朋友的还款
    补上，那就是一次 AA。范围只在那一笔前后各一个窗口，不扫全库——手动记一笔
    不该顺带把整个账本重算一遍。
    """
    if not conf.merge_enabled or not moment:
        return 0
    reach = conf.merge_window_hours
    return repo.autodetect(
        connection,
        since=dates.shift(moment, hours=-reach),
        until=dates.shift(moment, hours=reach),
    )


def create_app() -> Flask:
    app = Flask(__name__)
    # 在请求体被读进内存之前就挡掉超大文件。
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_BYTES
    # JSON 接口（前端 SPA 取数的地方）。它自己带 url_prefix="/api"，
    # 认证仍走下面那条 app 级的闸门——API 的路由名不在开放名单里，默认要登录。
    app.register_blueprint(api.blueprint)
    app.jinja_env.filters["amount"] = format_amount
    # 分类在列表页上只留一个 emoji（见 categories.py）。
    app.jinja_env.filters["category_emoji"] = categories.emoji
    # 列表上的日期掐到分钟：秒在窄屏上白占一整列的宽。
    app.jinja_env.filters["minute"] = dates.minute
    # 别处「跳到某个月的账」那些链接（汇总页的月份列、合并组那一行）用得上：
    # 列表页只认起止日期，模板里写成
    # ``url_for('transactions', **month_range('2026-03'))``。
    app.jinja_env.globals["month_range"] = dates.month_range

    # ---------------------------------------------------------------- 登录

    startup_auth = auth.load()
    # 会话 cookie 是**客户端签名**的：内容摆在浏览器那边，改一个字节就验不过。
    # 密钥跟着 auth.toml 走，重启服务不至于把登录状态清光。还没设密码的时候
    # 会话根本用不上，随便给一把就行——这也意味着设完密码**不用重启**：本次
    # 进程仍用这把临时的，下次起来才换成文件里那把，代价至多是重登一次。
    app.secret_key = startup_auth.session_key if startup_auth else os.urandom(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        # 这个应用的表单全是 POST 且没有 CSRF token。Lax 让别的站点发起的跨站
        # POST 带不上 cookie，正好把那条路堵掉（本站自己的跳转不受影响）。
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=auth.SESSION_DAYS),
        # 这个应用走的是明文 HTTP，SECURE 只能关着。密码和 cookie 在有嗅探的
        # 网络上都是明摆着的——那道防线在网络层（防火墙 / SSH 隧道），不在这一层。
    )

    def current_auth() -> auth.Auth | None:
        """这次请求读一次认证配置。

        跟 settings.load 一样**不缓存**：文件小，读一遍的开销可以忽略，而缓存
        会引出「密码改了没生效」这种最难查的毛病。
        """
        if "auth" not in g:
            g.auth = auth.load()
        return g.auth

    # 跟接口共用一个计数器：两处各持一个的话，同一台机器上的失败次数会被算成
    # 两份，锁得比该锁的松。（旧的那个登录页在下一批里就删掉了，现在先共用着。）
    app.extensions[api.ATTEMPTS_KEY] = auth.Attempts()
    attempts = app.extensions[api.ATTEMPTS_KEY]

    @app.before_request
    def api_needs_json():
        """改状态的接口只收 ``Content-Type: application/json``。

        这是一层**零成本的加料**，不是全部依靠：会话 cookie 是 SameSite=Lax，
        跨站的表单提交本来就带不上它。加这条是因为跨站 ``<form>`` 只能发出
        form-urlencoded / multipart / text-plain 三种，JSON 它发不出来——于是
        又堵掉一条路，代价是零。

        注册在 ``require_login`` **之前**：先按内容类型挡掉，再谈登录。反过来的话，
        一个跨站表单会先被当成「没登录」而放过去走另一条分支的语义。
        """
        if not request.path.startswith("/api/") or request.method not in (
            "POST",
            "PUT",
            "PATCH",
        ):
            return None
        # 没有请求体的 POST（隐藏、扫描、取消预览、确认）按路径就说明了意图，
        # 没有东西可以被跨站伪造。要求它们也带个 JSON 头，只会让一整类正常的
        # 请求平白收到 415。
        if not request.content_length:
            return None
        # 上传是唯一带文件的入口，只能走 multipart，单独放行。
        if request.endpoint == api.UPLOAD_ENDPOINT:
            return None
        if request.mimetype not in api.JSON_ONLY:
            return jsonify(
                error={"code": "unsupported_media_type", "message": "这个接口只收 JSON。"}
            ), 415
        return None

    @app.before_request
    def require_login():
        """没登录就先去登录页，并记下本来要去哪。"""
        # 接口走另一条路：303 到登录页对 SPA 来说是一坨 HTML，``response.json()``
        # 当场抛，屏幕上就是白屏。它要的是 401 加一个能认出来的 code。
        if request.path.startswith("/api/"):
            if request.endpoint in OPEN_API_ENDPOINTS:
                return None
            conf = current_auth()
            if conf is None or session.get("logged_in"):
                return None
            return api.unauthenticated()

        # 先看开放名单，再读配置：那几页不该因为 auth.toml 读不出来而跟着 500——
        # 恰恰是登录坏掉的时候，登录页的那张样式表更要拿得到。
        if request.endpoint in OPEN_ENDPOINTS:
            return None
        conf = current_auth()
        if conf is None or session.get("logged_in"):
            return None
        # 表单请求（POST）不记去向：登录完那份请求体也回不来了，把人送到那一页
        # 反而像「刚才那一下成功了」。
        where = request.full_path if request.query_string else request.path
        # 首页不用记：登录完本来就去那儿
        target = _next_target(where) if request.method == "GET" else ""
        if target == "/":
            target = ""
        if target:
            return redirect(url_for("login_page", next=target), code=303)
        return redirect(url_for("login_page"), code=303)

    # ---------------------------------------------------------------- 连接

    def get_conn():
        if "conn" not in g:
            g.conn = db.connect()
            db.init_db(g.conn)
        return g.conn

    @app.teardown_appcontext
    def close_conn(_exception):
        connection = g.pop("conn", None)
        if connection is not None:
            connection.close()

    def go(endpoint: str, **values):
        """Post/Redirect/Get 专用。

        用 303 而不是默认的 302：明确告诉浏览器「拿 GET 去打开那个地址」，
        刷新结果页时就不会把表单再提交一遍。
        """
        return redirect(url_for(endpoint, **values), code=303)

    # ---------------------------------------------------------------- 登录页

    def render_login(error: str | None, next_url: str, status: int = 200):
        return (
            render_template("login.html", error=error, next=next_url),
            status,
        )

    @app.get("/login")
    def login_page():
        """没设密码的时候没有这一页；已经进来的人也不必再看它一眼。"""
        if current_auth() is None or session.get("logged_in"):
            return go("summary")
        return render_login(None, _next_target(request.args.get("next")))

    @app.post("/login")
    def login():
        conf = current_auth()
        if conf is None:
            return go("summary")
        where = _next_target(request.form.get("next"))
        # 按来源计数
        source = request.remote_addr or "?"
        # **先验密码，再数失败。** 这道闸门是给猜密码的人设的，不该把输对了
        # 密码的那个人一起锁在门外：一个人的账本只有这一个密码，没有第二把
        # 钥匙，而锁是按来源算的（这里直连 8001，没有反向代理；日志里见过
        # Cloudflare 段的来访地址，那种情况下这条按代理地址聚合，会更粗）。
        # 所以挨个试依旧会在 429 上撞墙，但对的密码什么时候都能进。
        if conf.check(request.form.get("password") or ""):
            attempts.clear(source)
            session.clear()  # 换一个新的会话，不接着用登录之前那一个
            session["logged_in"] = True
            session.permanent = True
            return redirect(where or url_for("summary"), code=303)

        attempts.failed(source)
        wait = attempts.waiting(source)
        if wait > 0:
            return render_login(
                f"密码错的次数太多了，等 {int(wait) + 1} 秒再试。", where, status=429
            )
        return render_login("密码不对。", where, status=401)

    @app.post("/logout")
    def logout():
        session.clear()
        return go("login_page")

    # ---------------------------------------------------------------- 首页

    @app.context_processor
    def inject_palette():
        """每个页面都得知道自己在用哪套配色——``<html data-theme>`` 靠它。

        顺带告诉模板有没有登录这回事：没设密码时顶上要挂提示条，设了就多一个
        「退出」。
        """
        return {
            "palette": settings.load().palette(),
            "auth_enabled": current_auth() is not None,
            "nav": nav_section(request.endpoint),
        }

    @app.get("/")
    def summary():
        """首页就是汇总。"""
        connection = get_conn()
        conf = settings.load()
        palette = conf.palette()
        currencies = repo.currencies(connection)
        months = repo.by_month(connection)
        categories = repo.by_source_category(connection)

        # 按币种分别出图。把英镑和人民币画进同一组柱子，等于换个方式把它们相加。
        # 月份表是倒序的（最近的在上面），画时间轴要正过来。
        return render_template(
            "summary.html",
            months=months,
            categories=categories,
            overall=repo.overall_totals(connection),
            currencies=currencies,
            multiple_currencies=len(currencies) > 1,
            display_currency=conf.display_currency,
            large=repo.large_transactions(connection),
            charts=[
                {
                    "currency": currency,
                    "monthly": charts.monthly_columns(
                        sorted(
                            (
                                row
                                for row in months
                                if row["display_currency"] == currency
                            ),
                            key=lambda row: row["month"],
                        ),
                        unit=currency,
                        theme=palette,
                    ),
                    "categories": charts.category_bars(
                        [row for row in categories if row["display_currency"] == currency],
                        theme=palette,
                    ),
                }
                for currency in currencies
            ],
        )

    @app.get("/summary")
    def summary_legacy():
        """老地址，指回首页。"""
        return redirect(url_for("summary"), code=301)

    # ---------------------------------------------------------------- 导入

    @app.get("/import")
    def import_upload():
        connection = get_conn()
        return render_template(
            "import_upload.html",
            imports=repo.list_imports(connection, 8),
            accept=ACCEPT_ATTR,
            max_mb=config.MAX_BYTES // (1024 * 1024),
            error=None,
        )

    def upload_page(error: str, status: int = 400):
        connection = get_conn()
        return (
            render_template(
                "import_upload.html",
                imports=repo.list_imports(connection, 8),
                accept=ACCEPT_ATTR,
                max_mb=config.MAX_BYTES // (1024 * 1024),
                error=error,
            ),
            status,
        )

    @app.post("/import")
    def receive_import():
        file = request.files.get("file")
        if file is None or not file.filename:
            return upload_page("请先选一个账单文件。")
        try:
            preview = ingest.stage(get_conn(), file.filename, file.read())
        except UploadError as error:
            return upload_page(str(error))
        # Post/Redirect/Get：预览页有自己的地址，刷新不会重传文件。
        return go("import_preview", token=preview.token)

    @app.get("/import/<token>")
    def import_preview(token: str):
        try:
            preview = ingest.store.get(token)
        except ingest.PreviewExpired as error:
            return render_template("preview_expired.html", message=str(error)), 410
        return render_template(
            "import_preview.html",
            preview=preview,
            profiles=load_all(),
            max_rows=config.PREVIEW_ROWS,
        )

    @app.post("/import/<token>/reprofile")
    def import_reprofile(token: str):
        name = (request.form.get("profile") or "").strip()
        try:
            if name:
                ingest.reprofile(get_conn(), token, name)
        except ingest.PreviewExpired as error:
            return render_template("preview_expired.html", message=str(error)), 410
        except ProfileError as error:
            ingest.store.get(token).error = str(error)
        return go("import_preview", token=token)

    @app.post("/import/<token>/confirm")
    def import_confirm(token: str):
        try:
            import_id = ingest.confirm(get_conn(), token)
        except ingest.PreviewExpired as error:
            return render_template("preview_expired.html", message=str(error)), 410
        except BillError as error:
            return render_template("preview_expired.html", message=str(error)), 400
        return go("import_detail", import_id=import_id)

    @app.post("/import/<token>/cancel")
    def import_cancel(token: str):
        ingest.store.drop(token)
        return go("import_upload")

    # ---------------------------------------------------------------- 导入记录

    @app.get("/imports")
    def imports():
        return render_template(
            "imports.html", imports=repo.list_imports(get_conn(), 100)
        )

    @app.get("/imports/<int:import_id>")
    def import_detail(import_id: int):
        connection = get_conn()
        record = repo.get_import(connection, import_id)
        if record is None:
            abort(404)
        issues = connection.execute(
            """
            SELECT * FROM import_issues WHERE import_id = ?
            ORDER BY COALESCE(row_number, 0), id
            """,
            (import_id,),
        ).fetchall()
        rows = repo.transactions_for_import(connection, import_id, limit=50)
        return render_template(
            "import_detail.html", record=record, issues=issues, sample=rows
        )

    @app.post("/imports/<int:import_id>/delete")
    def import_delete(import_id: int):
        connection = get_conn()
        with connection:
            if not repo.delete_import(connection, import_id):
                abort(404)
        return go("imports")

    # ---------------------------------------------------------------- 账目

    # 列表页那几个筛选条件、页头两个数、那一行字：解析在 `params`，数数在
    # `api`（那两处都是接口与旧页面共用的）。留在这里的话，同一套语义就有两份，
    # 改漏一处不会报错，只会让列表页和汇总页对同一个区间各算各的。

    def back_to_list(**extra):
        """回到列表页，把原来的筛选条件带上。

        只看表单里的那几个筛选字段，不接受整串查询串——省得redirect的落点
        被 URL 里的东西牵着走。
        """
        still = {
            name: value
            for name in ("direction", "account", "q", "hidden", "limit")
            if (value := (request.form.get(name) or "").strip())
        }
        # 起止日期一律带上，空串也带。空串是「这一端不限」的意思，跟「压根没这个
        # 参数」（＝落回默认的当月）是两回事——丢掉的话，把区间清空之后隐掉一笔，
        # 回来的这一屏会自己缩回当月，像是在别的地方动过手。
        for name in ("start", "end"):
            still[name] = (request.form.get(name) or "").strip()
        # 行尾那几个按钮顺手把自己的行号交上来，回来自动落回那一行。不这样的话，
        # 在一份长列表靠后的地方隐掉一笔，303 回来会被甩到页首，还得再翻一遍。
        # 批量操作没有具体的行，不带这个字段，就还跟以前一样回页首。
        anchor = (request.form.get("anchor") or "").strip()
        if anchor.isdigit():
            return redirect(
                url_for("transactions", **{**still, **extra}, _anchor=f"row-{anchor}"),
                code=303,
            )
        return go("transactions", **still, **extra)

    @app.get("/transactions")
    def transactions():
        connection = get_conn()
        conf = settings.load()
        query = list_query(request.args, conf)
        total, hidden_total = list_counts(connection, query)
        # 两个日期填反了。不吭声的话这就是一个干干净净的「没有符合条件的记录」，
        # 看着跟「这个月真没记账」一模一样——把话说出来，别让人去别处找账。
        start, end = query["start"], query["end"]
        backwards = (
            "开始时间比结束时间晚，这个区间里一条都挑不出来。"
            if start and end and start > end
            else ""
        )
        rows = repo.list_transactions(
            connection,
            start=query["start"] or None,
            end=query["end"] or None,
            account_id=query["account_id"],
            direction=query["direction"] or None,
            search=query["search"] or None,
            hidden=query["hidden"] or None,
            limit=query["limit"],
            offset=0,
        )
        # 合并行只带锚点那一笔的文字，要看全组成员得另外取——而且只取这一段
        # 用得到的那些组。
        members: dict[int, list] = {}
        for member in repo.group_members(
            connection, [row["group_id"] for row in rows if row["group_id"]]
        ):
            members.setdefault(member["group_id"], []).append(member)

        accounts = repo.accounts(connection)
        return render_template(
            "transactions.html",
            display_currency=conf.display_currency,
            rows=rows,
            members=members,
            group_count=repo.count_groups(connection),
            accounts=accounts,
            categories=repo.transaction_categories(connection),
            total=total,
            hint=list_hint(total, hidden_total),
            # 筛选栏收起来之后，得让人一眼看出这一屏是筛过的——不然看着就像全部
            # 记录，而汇总和它又对不上。所以把这些条件明写在可以点开的标题旁边。
            active_filters=active_filter_labels(
                accounts,
                start=query["start"],
                end=query["end"],
                direction=query["direction"],
                account_id=query["account_id"],
                search=query["search"],
                hidden=query["hidden"],
            ),
            limit=query["limit"],
            remaining=max(total - len(rows), 0),
            # 直接读 params 上那一个：写成 import 进来的名字，就等于多了一份绑定，
            # 测试改了常量只有一半代码跟着变。
            next_limit=query["limit"] + params.LIST_STEP,
            error=(request.args.get("error") or "").strip() or backwards,
            filters={
                "start": query["start"],
                "end": query["end"],
                "direction": query["direction"],
                "account": query["account_id"],
                "q": query["search"],
                "hidden": query["hidden"],
            },
        )

    @app.get("/transactions/<int:txn_id>")
    def transaction_detail(txn_id: int):
        """一笔的详细信息：只读，把库里存着的都摊开。

        跟列表页是两个用途：列表是拿来扫的，这里是拿来对账的——账单原文、
        换算前后的金额、订单号、手改前后的分类，都在这上面。
        """
        connection = get_conn()
        row = repo.get_transaction(connection, txn_id)
        if row is None:
            abort(404)
        # 「← 交易记录」要落在这一笔现在待的那个月：被并进别个月的组之后（组落在
        # 最早那一笔上），照它自己的日期回，落在的是空屏。
        back_month = repo.effective_month(connection, txn_id) or row["occurred_date"][:7]
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except ValueError:
            raw = {}
        return render_template(
            "transaction_detail.html",
            tx=row,
            raw=raw if isinstance(raw, dict) else {},
            record=(
                repo.get_import(connection, row["import_id"])
                if row["import_id"]
                else None
            ),
            display_currency=settings.load().display_currency,
            back_month=back_month,
        )

    @app.post("/transactions/bulk")
    def transactions_bulk():
        """勾选之后的两件事：合并成一组，或者把分类一起改掉。"""
        connection = get_conn()
        action = (request.form.get("action") or "").strip()
        ids = [value for value in map(_as_int, request.form.getlist("txn")) if value]
        if not ids:
            return back_to_list(error="先在左边勾选几笔。")

        if action == "merge":
            try:
                with connection:
                    repo.create_group(connection, origin="manual", transaction_ids=ids)
            except BillError as error:
                return back_to_list(error=str(error))
            # 合完直接去合并组页：那一页能看见合成了什么、净额多少，也能一键取消。
            return go("groups_page")

        if action == "reclassify":
            category = (request.form.get("category") or "").strip()
            with connection:
                repo.reclassify(connection, ids, category)
            return back_to_list()

        return back_to_list(error="认不出这个操作。")

    # ---------------------------------------------------------------- 记一笔 / 改一笔

    def entry_page(*, values, errors, transaction=None, status: int = 200):
        connection = get_conn()
        # 「回到交易记录」落在这一笔**现在待的那个月**上：它可能已经被并进别个月的
        # 组（组落在最早那一笔上），照它自己的日期回，落在的是空屏——而保存之后的
        # 跳转走的是同一个口径（见 transaction_update）。
        back_month = (
            repo.effective_month(connection, transaction["id"]) if transaction else None
        )
        return (
            render_template(
                "transaction_form.html",
                values=values,
                errors=errors,
                transaction=transaction,
                back_month=back_month,
                accounts=repo.accounts(connection),
                currencies=repo.transaction_currencies(connection),
                categories=repo.transaction_categories(connection),
                display_currency=settings.load().display_currency,
            ),
            status,
        )

    @app.get("/transactions/new")
    def transaction_new():
        return entry_page(
            values=entry.blank(settings.load().display_currency), errors=[]
        )

    @app.post("/transactions/new")
    def transaction_create():
        connection = get_conn()
        conf = settings.load()
        draft = entry.read(request.form, display_currency=conf.display_currency)
        if not draft.ok:
            # 校验没过就原样把填过的内容交回去，不让人重填一遍。
            return entry_page(values=dict(request.form), errors=draft.errors, status=400)

        with connection:
            account_id = repo.ensure_account(connection, draft.account_name, "manual")
            txn_id = repo.insert_manual_transaction(
                connection,
                account_id=account_id,
                transaction=draft.transaction(),
                category=draft.category,
            )
            _autodetect_around(connection, conf, draft.occurred_at)
            # 刚记的这笔要是立刻被并进了别个月的组，就跳它现在待的那一页
            month = repo.effective_month(connection, txn_id) or draft.month
        return go("transactions", **dates.month_range(month))

    @app.get("/transactions/<int:txn_id>/edit")
    def transaction_edit(txn_id: int):
        connection = get_conn()
        row = repo.get_transaction(connection, txn_id)
        if row is None:
            abort(404)
        return entry_page(values=entry.from_row(row), errors=[], transaction=row)

    @app.post("/transactions/<int:txn_id>/edit")
    def transaction_update(txn_id: int):
        connection = get_conn()
        row = repo.get_transaction(connection, txn_id)
        if row is None:
            abort(404)
        draft = entry.read(
            request.form, display_currency=settings.load().display_currency
        )
        if not draft.ok:
            # 账户那一格是只读的，不会随表单提交上来——所以拿库里的补上，
            # 别让用户在改错一处的时候连这是哪个账户都看不见。
            return entry_page(
                values={**entry.from_row(row), **dict(request.form)},
                errors=draft.errors,
                transaction=row,
                status=400,
            )

        with connection:
            repo.update_transaction(connection, txn_id, draft.fields())
            month = repo.effective_month(connection, txn_id) or draft.month
        return go("transactions", **dates.month_range(month))

    @app.post("/transactions/<int:txn_id>/delete")
    def transaction_delete(txn_id: int):
        connection = get_conn()
        with connection:
            if not repo.delete_transaction(connection, txn_id):
                abort(404)
        return back_to_list()

    def toggle_hidden(txn_id: int, hidden: bool):
        connection = get_conn()
        with connection:
            if not repo.set_hidden(connection, txn_id, hidden):
                abort(404)
        # 详情页上按的，就回详情页——别把人甩回列表再自己找回来。
        if (request.form.get("back") or "") == "detail":
            return go("transaction_detail", txn_id=txn_id)
        if "application/json" not in (request.headers.get("Accept") or ""):
            return back_to_list()
        # 页面上那一行自己重画，不再整页重载（见 transactions.html 里那段 JS）。
        # 所以页头那两个数得服务端算准了交回去——「全表还隐藏着几笔」客户端算不出来。
        query = list_query(request.form, settings.load())
        total, hidden_total = list_counts(connection, query)
        row = repo.get_transaction(connection, txn_id)
        if row is None:
            abort(404)
        return jsonify(
            {
                "hidden": hidden,
                "total": total,
                # 当前这一档排不排得下它。排不下就把那一行从页面上撤掉，
                # 不然「只看隐藏」里还原一笔，它还会赖在页面上不走。
                "keep": not (
                    (query["hidden"] == "0" and hidden)
                    or (query["hidden"] == "1" and not hidden)
                ),
                "hint": list_hint(total, hidden_total),
                # 这一行整份重画：勾选框、标签、按钮文案都在 _transaction_rows.html
                # 那一份里，客户端只管换，不自己拼 markup。
                "row": render_template(
                    "_transaction_row.html",
                    tx=row,
                    index=_as_int(request.form.get("anchor") or ""),
                    members={},
                    display_currency=settings.load().display_currency,
                ),
            }
        )

    # 隐藏 / 取消隐藏拆成两条路径，而不是一条带个开关字段：这两个动作都是从
    # 那张大表单里用 formaction 提交的，走两条路径就不用再为它各塞一个隐藏字段。
    @app.post("/transactions/<int:txn_id>/hide")
    def transaction_hide(txn_id: int):
        return toggle_hidden(txn_id, True)

    @app.post("/transactions/<int:txn_id>/unhide")
    def transaction_unhide(txn_id: int):
        return toggle_hidden(txn_id, False)

    # ---------------------------------------------------------------- 合并组

    @app.get("/groups")
    def groups_page():
        connection = get_conn()
        conf = settings.load()
        found = repo.list_groups(connection)
        members: dict[int, list] = {}
        for member in repo.group_members(connection, [row["id"] for row in found]):
            members.setdefault(member["group_id"], []).append(member)
        return render_template(
            "groups.html",
            groups=found,
            members=members,
            created=request.args.get("created", type=int),
            display_currency=conf.display_currency,
        )

    @app.post("/groups/scan")
    def groups_scan():
        """把整个账本重新扫一遍。

        给「功能上线之前就记了几个月」的账本补检测用的；平时导入时会自动扫，
        不用来这儿手点。
        """
        connection = get_conn()
        with connection:
            created = repo.autodetect(connection)
        return go("groups_page", created=created)

    @app.post("/groups/<int:group_id>/dissolve")
    def group_dissolve(group_id: int):
        connection = get_conn()
        with connection:
            if not repo.dissolve_group(connection, group_id, veto=True):
                abort(404)
        return go("groups_page")

    @app.post("/groups/<int:group_id>/edit")
    def group_update(group_id: int):
        connection = get_conn()
        with connection:
            saved = repo.update_group(
                connection,
                group_id,
                title=(request.form.get("title") or "").strip(),
                category=(request.form.get("category") or "").strip(),
                note=(request.form.get("note") or "").strip(),
            )
            if not saved:
                # 组已经没了（另一个标签页取消过，或者这一页放了太久）。
                # 说「保存成功」比报错更糟：改的东西根本没人收。
                abort(404)
        return go("groups_page")

    # ---------------------------------------------------------------- 设置

    def render_settings(values, errors, *, status: int = 200, saved: bool = False):
        return (
            render_template(
                "settings.html",
                values=values,
                errors=errors,
                saved=saved,
                theme_choices=[themes.get(name) for name in themes.names()],
                path=str(config.user_settings_path()),
            ),
            status,
        )

    @app.get("/settings")
    def settings_page():
        return render_settings(
            settings.as_form(settings.load()),
            [],
            saved=request.args.get("saved") == "1",
        )

    @app.post("/settings")
    def settings_save():
        conf, values, errors = settings.read_form(request.form)
        if errors:
            # 校验没过就把填过的原样交回去，不让人重填一遍。
            return render_settings(values, errors, status=400)
        try:
            settings.save(conf)
        except BillError as error:
            return render_settings(values, [str(error)], status=400)
        # Post/Redirect/Get：刷新结果页时不会把这份表单再提交一遍。
        return go("settings_page", saved=1)

    # ---------------------------------------------------------------- 配置

    @app.get("/profiles")
    def profiles_page():
        """列映射一览。

        真实账单到手、格式对不上时，这一页是要看的地方：每份配置的列名候选、
        文件在哪、有没有毛病。传过文件之后还会把**实际读到的表头**并排列出来，
        好照着改。
        """
        loaded = load_all()
        staged = ingest.store.latest()
        headers = staged.grid.header if staged and staged.grid else []
        return render_template(
            "profiles.html",
            profiles=loaded,
            problems={profile.name: validate(profile) for profile in loaded},
            headers=headers,
            staged=staged,
            user_dir=str(config.user_profiles_dir()),
            builtin_dir=str(config.builtin_profiles_dir()),
        )

    # ---------------------------------------------------------------- 其它

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", uptime_seconds=uptime_seconds())

    # ---------------------------------------------------------------- 前端外壳

    @app.get("/assets/<path:filename>")
    def assets(filename: str):
        """构建出来的 JS / CSS。

        Vite 默认发绝对路径 `/assets/…`，所以这一条挂在根上而不是 /app 下面——
        将来外壳挪到根路径时，这些地址一个字都不用改。
        """
        return send_from_directory(APP_BUILD_DIR / "assets", filename)

    def render_shell():
        """把外壳 HTML 吐出去，顺手把主题填进 ``<html data-theme>``。

        填在服务端而不是等 /api/meta 回来再设：否则第一帧永远用默认那套配色，
        切成浅色主题的人每次打开都先闪一下深色。这里读一次设置就有，
        跟旧界面 ``<html data-theme>`` 的做法是同一条路。
        """
        index = APP_BUILD_DIR / "index.html"
        if not index.exists():
            # 产物不在 = 还没构建过，或者构建到一半失败了。**说清楚**，
            # 而不是回一个空白页——空白页看起来像应用坏了，而实际只是没构建。
            return (
                "<!doctype html><meta charset='utf-8'><h1>前端还没有构建</h1>"
                "<p>在 frontend/ 下跑 <code>npm ci &amp;&amp; npm run build</code>。</p>",
                503,
            )
        try:
            theme = settings.load().theme
        except BillError:
            # 设置坏了不该连带把界面也挡在外面：先用默认主题把页面给出去，
            # 设置页自己会说明哪里填错了。
            theme = themes.DEFAULT_THEME
        html = index.read_text(encoding="utf-8").replace(THEME_SLOT, theme)
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.get(SHELL_PREFIX)
    # `/app/` 要单独点一句：`<path:path>` 不匹配空串，少了这条，带尾斜杠的地址
    # 会落到 404 上去——而那条 404 是**旧界面**的模板，于是页面上会看到旧导航、
    # 旧提示条，看着像「新界面没生效」，实际只是这条地址没接住。
    @app.get(f"{SHELL_PREFIX}/")
    @app.get(f"{SHELL_PREFIX}/<path:path>")
    def app_shell(path: str = ""):
        """SPA 的入口。

        这一层**免登录**：外壳 HTML 里没有任何账目数据，数据全在 /api/*（那边
        每个接口都要登录）。这样打开首页不会先被拽去一个登录页再弹回来。
        真正的闸门在接口那一层。
        """
        return render_shell()

    @app.after_request
    def render_inline(response):
        """别让浏览器把页面当成文件。

        浏览器只会「下载」它渲染不了的东西，所以这里明确说这些是页面：
        inline 处置，加上 nosniff 防止内容被重新嗅探归类。真想发文件的
        路由仍然可以自己设 attachment——这里只是补默认值。
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
        return render_template("413.html", max_mb=config.MAX_BYTES // (1024 * 1024)), 413

    return app


def _set_password_command(arguments: list[str]) -> None:
    """``bill --set-password``：交互式问两遍，对上了才写。

    标准输入不是终端（管道、脚本）时只读一行、不确认——``echo … | bill
    --set-password`` 是给人拿来用的，多问一遍也没人答。
    """
    if arguments:
        raise SystemExit(f"认不出的参数：{' '.join(arguments)}\n{USAGE}")
    if sys.stdin.isatty():
        first = getpass.getpass("新密码：")
        if first != getpass.getpass("再输一遍："):
            raise SystemExit("两遍不一样，没改。")
    else:
        first = sys.stdin.readline().rstrip("\n")
    try:
        auth.write(first)
    except BillError as error:
        raise SystemExit(str(error)) from None
    print(f"密码已写入 {config.user_auth_path()}。下次打开页面就要登录。")


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--set-password"]:
        _set_password_command(arguments[1:])
        return
    if arguments:
        raise SystemExit(f"认不出的参数：{' '.join(arguments)}\n{USAGE}")

    app = create_app()
    if auth.load() is None:
        # 没设密码 = 跟以前一样不设防。这条只进日志：页面上另有一条提示条，
        # 这里说给「翻日志找问题」的人听。
        print(
            "注意：还没设密码，凡是能访问到这台机器这个端口的人都能看到全部账目。"
            "跑 `bill --set-password` 设一个（设完不用重启）。",
            file=sys.stderr,
        )
    app.run(
        # 默认绑所有网卡，方便从别的机器访问——本机开 127.0.0.1 也行。
        # 设了密码之后，绑 0.0.0.0 就不再等于把账本摆在网上：先要看得到密码。
        # 但 HTTP 是明文，密码和会话 cookie 在有嗅探的网络上都是明摆着的，
        # 真要隔着不受信任的网络用，还是靠防火墙兜住、或者走 SSH 隧道。
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8001")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )


if __name__ == "__main__":
    main()
