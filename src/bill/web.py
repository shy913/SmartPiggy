"""Flask 应用装配：一个进程同时供 JSON 接口和前端外壳。

跑起来：

    uv run bill

这一层现在只剩三件事：**装接口**、**发前端产物**、**把没登录的人挡在接口外面**。
页面长什么样全在 `frontend/` 里，服务端不画 HTML——一句模板都不渲染。

（`templates/` 与 `static/style.css` 已经不在了。旧的 Jinja 界面和它那 33 条路由
整体换成了 `api.py` 的接口加一个 SPA；改变的是**谁来画界面**，账怎么算一个字
没动——`repo.py` 那套合并后的净额口径原样还在。）
"""

from __future__ import annotations

import getpass
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session

from . import api, auth, config, settings, themes
from .errors import BillError

__all__ = ["create_app", "main"]

_started_at = datetime.now(timezone.utc)

USAGE = "用法：bill [--set-password]"

# 前端构建产物。**不进版本库**（见 .gitignore），部署时现构建——仓库里就不会
# 有一份「改了源码忘了重建」的偏差。
APP_BUILD_DIR = Path(__file__).parent / "static" / "app"
# 外壳里那个 data-theme 占位符。服务端填好再吐出去，第一帧主题就是对的。
THEME_SLOT = "%BILL_THEME%"

# 不登录也能打的接口。**逐个点名**，而且只点登录本身：「问一句我登没登」
# 「登录」「退出」。除此之外的 /api/* 一律 401。
#
# 外壳那几条（`/`、`/assets/*`、`/healthz`）根本不进这道闸门——它们不含任何
# 账目数据。把外壳也纳入的话，没登录的人打开首页会被拽去登录页，而那个登录
# 视图本身就长在 SPA 里：先有鸡还是先有蛋。
OPEN_API_ENDPOINTS = frozenset({"api.session_state", "api.login", "api.logout"})


def create_app() -> Flask:
    # static_folder=None：默认那套 `/static/<file>` 是给旧界面那张样式表用的，
    # 现在没必要留着——产物走 `/assets/`，由下面那条路由自己管。
    app = Flask(__name__, static_folder=None)
    # 在请求体被读进内存之前就挡掉超大文件。
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_BYTES
    app.register_blueprint(api.blueprint)

    # ---------------------------------------------------------------- 会话

    startup_auth = auth.load()
    # 会话 cookie 是**客户端签名**的：内容摆在浏览器那边，改一个字节就验不过。
    # 密钥跟着 auth.toml 走，重启服务不至于把登录状态清光。还没设密码的时候
    # 会话根本用不上，随便给一把就行——这也意味着设完密码**不用重启**。
    app.secret_key = startup_auth.session_key if startup_auth else os.urandom(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        # 这个应用的表单全是 JSON POST，改状态的接口只收 application/json，
        # 跨站的 <form> 发不出这个类型；再加上 Lax，跨站请求带不上 cookie，
        # 两条一起把 CSRF 那条路堵住。没有引入 token 机制——单人本地应用，
        # 收益不抵复杂度。
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=auth.SESSION_DAYS),
        # 这个应用走的是明文 HTTP，SECURE 只能关着。密码和 cookie 在有嗅探的
        # 网络上都是明摆着的——那道防线在网络层（防火墙 / SSH 隧道），不在这一层。
    )
    # 猜密码的失败计数挂在 app 上而不是模块全局：全局的话一次进程里建两个 app
    # （测试就是这么干的）会共用一个计数器，前面用例试错几次、后面的用例就撞 429。
    app.extensions[api.ATTEMPTS_KEY] = auth.Attempts()

    def current_auth() -> auth.Auth | None:
        """这次请求读一次认证配置。

        跟 settings.load 一样**不缓存**：文件小，读一遍的开销可以忽略，而缓存
        会引出「密码改了没生效」这种最难查的毛病。
        """
        from flask import g

        if "auth" not in g:
            g.auth = auth.load()
        return g.auth

    # ---------------------------------------------------------------- 闸门

    @app.before_request
    def api_needs_json():
        """改状态的接口只收 ``Content-Type: application/json``。

        这是一层**零成本的加料**，不是全部依靠：会话 cookie 是 SameSite=Lax，
        跨站的表单提交本来就带不上它。加这条是因为跨站 ``<form>`` 只能发出
        form-urlencoded / multipart / text-plain 三种，JSON 它发不出来。

        注册在 ``require_login`` **之前**：先按内容类型挡掉，再谈登录。
        """
        if not request.path.startswith("/api/") or request.method not in ("POST", "PUT", "PATCH"):
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
        """接口没登录就 401。

        **不跳登录页**：SPA 收到 303 跟过去，拿回来的是一整页 HTML，
        ``response.json()`` 当场抛，屏幕上就是白屏而不是登录框。
        """
        if not request.path.startswith("/api/"):
            return None
        if request.endpoint in OPEN_API_ENDPOINTS:
            return None
        conf = current_auth()
        if conf is None or session.get("logged_in"):
            return None
        return api.unauthenticated()

    # ---------------------------------------------------------------- 前端

    @app.get("/healthz")
    def healthz():
        """部署之后唯一的存活探针。免登录，只回 uptime，不带任何账目数据。"""
        return jsonify(status="ok", uptime_seconds=int((datetime.now(timezone.utc) - _started_at).total_seconds()))

    @app.get("/assets/<path:filename>")
    def assets(filename: str):
        """构建出来的 JS / CSS。"""
        return send_from_directory(APP_BUILD_DIR / "assets", filename)

    def render_shell():
        """外壳 HTML，顺手把主题填进 ``<html data-theme>``。

        填在服务端而不是等 /api/meta 回来再设：否则第一帧永远用默认那套配色，
        切成浅色主题的人每次打开都先闪一下深色。
        """
        index = APP_BUILD_DIR / "index.html"
        if not index.exists():
            # 产物不在 = 还没构建过，或者构建失败被回滚掉了。**说清楚**，
            # 而不是回一个空白页——空白页看起来像应用坏了，实际只是没构建。
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

    @app.get("/")
    @app.get("/<path:path>")
    def app_shell(path: str = ""):
        """SPA 的入口。除接口和产物之外，所有地址都回到这里，由前端路由接管。

        这样 `/transactions/12`、`/import/preview/<token>` 这些地址刷新、后退、
        存书签都还在——跟旧界面那些「每个页面有自己的地址」是同一个承诺。
        """
        if path.startswith("api/"):
            # 认不出来的接口地址要回 JSON，不能把外壳 HTML 交给一个 fetch：
            # 那会变成一个解析错误，而不是一句「没有这个接口」。
            return jsonify(error={"code": "not_found", "message": "没有这个接口。"}), 404
        return render_shell()

    # ---------------------------------------------------------------- 其它

    @app.after_request
    def harden(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify(error={"code": "not_found", "message": "没有这个接口。"}), 404
        # 其余交给前端路由：SPA 自己会画那个 404 页面。
        return render_shell()

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(
            error={
                "code": "too_large",
                "message": f"文件太大了，上限 {config.MAX_BYTES // (1024 * 1024)} MB。",
            }
        ), 413

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
