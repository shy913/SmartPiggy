"""真实账单样本的验收用例。

默认跳过——`tests/fixtures/real/` 里没有文件时不跑。等真实样本到了，把它们
放进那个目录（已经在 .gitignore 里，含个人信息，不要提交）再跑：

    uv run pytest -m real

每个文件都必须能被某份配置认出来、并且解析时一条 error 级问题都没有。
"""

from __future__ import annotations

import pathlib

import pytest

from bill import db, ingest

REAL_DIR = pathlib.Path(__file__).parent / "fixtures" / "real"

_samples = sorted(
    path
    for path in REAL_DIR.glob("*")
    if path.suffix.lower()
    in {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".zip", ".pdf"}
) if REAL_DIR.is_dir() else []

pytestmark = [
    pytest.mark.real,
    pytest.mark.skipif(not _samples, reason="tests/fixtures/real/ 里还没有真实样本"),
]


@pytest.mark.parametrize("path", _samples, ids=lambda p: p.name)
def test_real_sample_imports_cleanly(conn_module, path):
    preview = ingest.stage(conn_module, path.name, path.read_bytes())

    assert preview.error == "", f"{path.name} 没被任何配置认出来：{preview.error}"
    assert preview.detected is not None, f"{path.name} 匹配度不够，需要调配置"

    errors = [issue for issue in preview.plan.issues if issue.level == "error"]
    assert errors == [], f"{path.name} 有 {len(errors)} 条解析错误"

    # 把摘要打出来，人工核对一眼。
    counts = preview.counts()
    totals = "、".join(
        f"{currency} 收入 {income / 100:.2f} / 支出 {expense / 100:.2f}"
        for currency, (income, expense) in preview.totals_by_currency().items()
    )
    print(
        f"\n{path.name}: {preview.profile_name} · "
        f"新增 {counts['new']} · 重复 {counts['duplicate']} · "
        f"中性 {counts['neutral']} · {totals}"
    )


@pytest.fixture
def conn_module():
    connection = db.connect()
    db.init_db(connection)
    yield connection
    connection.close()
