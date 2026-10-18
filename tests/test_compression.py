"""响应压缩：只压该压的，别把客户端看不懂的字节发出去。"""

from __future__ import annotations

import gzip

from flask import jsonify

from bill import web as web_module
from bill.compression import accepts_gzip


def test_accept_encoding_parsing():
    assert accepts_gzip("gzip")
    assert accepts_gzip("deflate, gzip, br")
    assert accepts_gzip("*")
    assert accepts_gzip("gzip;q=0.5")
    # 显式的 q=0 比通配符优先，否则「除了 gzip 什么都行」的客户端也会被压。
    assert not accepts_gzip("gzip;q=0, *")
    assert not accepts_gzip("gzip;q=0")
    assert not accepts_gzip("identity")
    assert not accepts_gzip("")


def test_large_json_is_gzipped_and_round_trips(client):
    client.application.add_url_rule(
        "/api/test/big", "test_big", lambda: jsonify(payload="账" * 2000)
    )

    plain = client.get("/api/test/big")
    packed = client.get("/api/test/big", headers={"Accept-Encoding": "gzip"})

    assert "Accept-Encoding" in plain.headers["Vary"]
    assert "Content-Encoding" not in plain.headers
    assert packed.headers["Content-Encoding"] == "gzip"
    assert int(packed.headers["Content-Length"]) == len(packed.data)
    assert gzip.decompress(packed.data) == plain.data


def test_small_response_is_not_gzipped(client):
    small = client.get("/healthz", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in small.headers
    assert "Accept-Encoding" in small.headers["Vary"]


def test_q_zero_means_no_compression(client):
    client.application.add_url_rule(
        "/api/test/big", "test_big", lambda: jsonify(payload="账" * 2000)
    )
    response = client.get("/api/test/big", headers={"Accept-Encoding": "gzip;q=0, *"})
    assert "Content-Encoding" not in response.headers


def test_static_asset_is_gzipped(client, monkeypatch, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    body = b"console.log('" + b"x" * 8000 + b"');\n"
    (assets / "app.js").write_bytes(body)
    monkeypatch.setattr(web_module, "APP_BUILD_DIR", tmp_path)

    plain = client.get("/assets/app.js")
    packed = client.get("/assets/app.js", headers={"Accept-Encoding": "gzip"})

    assert plain.data == body
    assert packed.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(packed.data) == body


def test_conditional_requests_stay_headless(client, monkeypatch, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_bytes(b"x" * 8000)
    monkeypatch.setattr(web_module, "APP_BUILD_DIR", tmp_path)

    first = client.get("/assets/app.js")
    second = client.get(
        "/assets/app.js",
        headers={"Accept-Encoding": "gzip", "If-None-Match": first.headers["ETag"]},
    )
    assert second.status_code == 304
    assert "Content-Encoding" not in second.headers
