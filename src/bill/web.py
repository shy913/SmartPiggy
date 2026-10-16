"""Flask 应用：路由在这里装配，业务逻辑都在别的模块里。

跑起来：

    uv run bill
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import (
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from . import charts, config, db, ingest, repo, settings
from .errors import BillError, ProfileError, UploadError
from .money import format_amount
from .profiles import load_all, validate
from .readers import ACCEPT_ATTR

__all__ = ["create_app", "main"]

_started_at = datetime.now(timezone.utc)
PAGE_SIZE = 100


def current_month() -> str:
    """「本月」按账本统一的显示时区算，不是按服务器的时钟。

    这台机器在 UTC+8 而账本显示英国时间，跨月那几小时会整整差一个月。
    """
    return datetime.now(settings.load().tz()).strftime("%Y-%m")


def uptime_seconds() -> int:
    return int((datetime.now(timezone.utc) - _started_at).total_seconds())


def create_app() -> Flask:
    app = Flask(__name__)
    # 在请求体被读进内存之前就挡掉超大文件。
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_BYTES
    app.jinja_env.filters["amount"] = format_amount

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

    # ---------------------------------------------------------------- 首页

    @app.get("/")
    def index():
        connection = get_conn()
        # 默认看最近有数据的月份。导一份历史账单进来时当前月往往是空的，
        # 默认落在当前月会让首页一片 0，看着像没导进去。
        month = (
            request.args.get("month")
            or repo.latest_month(connection)
            or current_month()
        )
        conf = settings.load()
        currencies = repo.currencies(connection)
        return render_template(
            "index.html",
            month=month,
            display_currency=conf.display_currency,
            currency_note=conf.describe(),
            is_current_month=month == current_month(),
            month_totals=repo.month_totals(connection, month),
            overall=repo.overall_totals(connection),
            currencies=currencies,
            multiple_currencies=len(currencies) > 1,
            coverage=repo.coverage(connection),
            imports=repo.list_imports(connection, 5),
            uptime=uptime_seconds(),
        )

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

    @app.get("/transactions")
    def transactions():
        connection = get_conn()
        month = request.args.get("month") or ""
        direction = request.args.get("direction") or ""
        account_id = request.args.get("account", type=int)
        search = (request.args.get("q") or "").strip()
        page = max(request.args.get("page", 1, type=int), 1)

        total = repo.count_transactions(
            connection,
            month=month or None,
            account_id=account_id,
            direction=direction or None,
            search=search or None,
        )
        rows = repo.list_transactions(
            connection,
            month=month or None,
            account_id=account_id,
            direction=direction or None,
            search=search or None,
            limit=PAGE_SIZE,
            offset=(page - 1) * PAGE_SIZE,
        )
        return render_template(
            "transactions.html",
            display_currency=settings.load().display_currency,
            rows=rows,
            accounts=repo.accounts(connection),
            total=total,
            page=page,
            pages=max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1),
            filters={
                "month": month,
                "direction": direction,
                "account": account_id,
                "q": search,
            },
        )

    @app.get("/summary")
    def summary():
        connection = get_conn()
        conf = settings.load()
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
            currency_note=conf.describe(),
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
                    ),
                    "categories": charts.category_bars(
                        [row for row in categories if row["display_currency"] == currency]
                    ),
                }
                for currency in currencies
            ],
        )

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


def main() -> None:
    app = create_app()
    app.run(
        # 默认绑所有网卡，方便从别的机器访问——本机开 127.0.0.1 也行。
        # 注意这个应用没有登录认证：绑 0.0.0.0 等于凡能访问到本机这个端口的
        # 人都能看到全部账目。放到共享网络或公网上时，要么靠防火墙兜住，
        # 要么用 HOST=127.0.0.1 收回本机。
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8001")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )


if __name__ == "__main__":
    main()
