"""按 ``Accept-Encoding`` 压缩响应体。

只做 gzip。这台机器上的客户端（浏览器、curl、手机）都认它，再加 brotli 就得
多装一个依赖，换来的那点体积差抵不上。压缩放在 ``after_request`` 里统一做，
接口 JSON 和前端产物走同一条路——静态资源那 700 多 KB 比任何一条查询都大，
绕开它等于没做。

几条边界，违反了不会报错，只会让某个客户端拿到看不懂的字节：

- 小于 1 KB 的体不压：gzip 头本身就有十几个字节，小响应压完反而更大。
- 已经带 ``Content-Encoding`` 的不碰，206（Range 响应）也不碰。
- 可压缩类型即使这次没压，也要带 ``Vary: Accept-Encoding``；否则中间缓存
  会把压过的版本发给不认 gzip 的客户端。
- 静态文件是 ``direct_passthrough`` 的文件包装，得先读成字节再换掉；最大的
  那份 JS 也就 700 多 KB，一次压缩的代价远小于多传一份未压缩的。
"""

from __future__ import annotations

import gzip

# 这些类型压缩有意义。图片、字体、zip 本来就是压缩过的，列进来只会白费 CPU。
COMPRESSIBLE_TYPES = frozenset(
    {
        "application/json",
        "application/javascript",
        "text/javascript",
        "text/html",
        "text/css",
        "text/plain",
        "image/svg+xml",
    }
)

# 小于这个字节数不压。常见的最小响应（/healthz、/api/session）本来就一两百字节。
MIN_BYTES = 1024

# 6 是压缩率与 CPU 的折中：对 JSON 和 JS 已经能拿到九成以上的收益。
COMPRESS_LEVEL = 6


def accepts_gzip(header: str) -> bool:
    """``Accept-Encoding`` 里 gzip 的 q 值是否大于 0。

    显式的 ``gzip;q=0`` 优先于通配符：``gzip;q=0, *`` 的意思是「除了 gzip
    什么都可以」。只看请求里有没有 "gzip" 会把这个客户端也压上。
    """
    gzip_q: float | None = None
    star_q: float | None = None
    for item in header.split(","):
        coding, _, params = item.partition(";")
        coding = coding.strip().lower()
        if not coding:
            continue
        quality = 1.0
        for param in params.split(";"):
            key, _, raw = param.strip().partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(raw)
                except ValueError:
                    quality = 0.0
        quality = max(quality, 0.0)
        if coding in ("gzip", "x-gzip"):
            gzip_q = quality if gzip_q is None else max(gzip_q, quality)
        elif coding == "*":
            star_q = quality if star_q is None else max(star_q, quality)
    if gzip_q is not None:
        return gzip_q > 0
    return bool(star_q and star_q > 0)


def compress(response, accept_encoding: str):
    """能压就压，不能压就原样返回。``response`` 是 Flask 的响应对象。"""
    if response.mimetype not in COMPRESSIBLE_TYPES:
        return response

    # 可压缩类型一律声明按编码区分，哪怕这一次没压。共享缓存（以及将来可能
    # 加上的代理）据此不会把 gzip 版本原样发给 identity 的客户端。
    response.vary.add("Accept-Encoding")

    if response.status_code != 200:
        return response
    if response.headers.get("Content-Encoding"):
        return response
    if not accepts_gzip(accept_encoding):
        return response

    # 静态文件是 direct_passthrough 的文件包装，不解开就读不出字节。换成
    # 内存里的普通响应之后，set_data 会顺带把 Content-Length 重新算对。
    response.direct_passthrough = False
    body = response.get_data()
    if len(body) < MIN_BYTES:
        return response
    # mtime=0：同样的内容每次压出来都一模一样，方便比对，也省得缓存把
    # 「内容没变但字节不同」当成新版本。
    packed = gzip.compress(body, compresslevel=COMPRESS_LEVEL, mtime=0)
    if len(packed) >= len(body):
        return response

    response.set_data(packed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(packed))
    # 字节已经换了，原来的 Range 支持不再成立。
    response.headers.pop("Accept-Ranges", None)
    return response
