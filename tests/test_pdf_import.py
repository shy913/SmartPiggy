"""中国银行 PDF：读取、配置识别、币种映射、中性判定与幂等。"""

from __future__ import annotations

import pytest

from bill import ingest, profiles
from bill.errors import UploadError
from bill.models import Direction
from bill.readers import read_table

from .factories import BOC_HEADER, BOC_ROWS, boc_pdf, boc_text_pdf


def _all_probes() -> tuple[str, ...]:
    names: list[str] = []
    for profile in profiles.load_all():
        for name in profile.probe_columns():
            if name not in names:
                names.append(name)
    return tuple(names)


def _write_settings(tmp_path, timezone: str) -> None:
    tmp_path.joinpath("settings.toml").write_text(
        f'timezone = "{timezone}"\n\n[currency]\ndisplay = "CNY"\nrates = {{}}\n',
        encoding="utf-8",
    )


def test_pdf_is_read_with_password_from_filename():
    table = read_table("332150.pdf", boc_pdf(), probes=_all_probes())

    assert table.kind == "pdf"
    assert table.rows[0] == BOC_HEADER
    assert len(table.rows) == 1 + len(BOC_ROWS)

    # 单元格里的排版换行被拼回去，而不是留下一个空格或截断。
    first = table.rows[1]
    assert first[10] == "GB84MONZ040058"


def test_pdf_also_accepts_the_full_filename_as_password():
    table = read_table("332150.pdf", boc_pdf(password="332150.pdf"))
    assert table.rows[0] == BOC_HEADER


def test_pdf_wrong_password_is_a_readable_error():
    with pytest.raises(UploadError, match="密码"):
        read_table("wrong.pdf", boc_pdf(password="332150"))


def test_pdf_without_tables_is_a_readable_error():
    with pytest.raises(UploadError, match="没有找到表格"):
        read_table("扫描件.pdf", boc_text_pdf())


def test_corrupt_pdf_is_a_readable_error():
    with pytest.raises(UploadError, match="打不开"):
        read_table("坏文件.pdf", b"%PDF-1.4 not really a pdf")


def test_repeated_page_headers_are_collapsed():
    table = read_table("332150.pdf", boc_pdf(repeat=20), probes=_all_probes())

    assert sum(1 for row in table.rows if row == BOC_HEADER) == 1
    # 只丢表头，不动数据行——同一份文件里两笔真实相同的消费必须都留着。
    assert len(table.rows) == 1 + len(BOC_ROWS) * 20


def test_boc_profile_maps_currencies():
    profile = next(item for item in profiles.load_all() if item.name == "boc")

    assert profile.currency_code("英镑") == "GBP"
    assert profile.currency_code("人民币") == "CNY"
    assert profile.currency_code("美元") == "USD"
    assert profile.currency_code("") == "CNY"


def test_boc_pdf_is_detected_and_parsed(conn):
    preview = ingest.stage(conn, "332150.pdf", boc_pdf())

    assert preview.error == ""
    assert preview.profile_name == "boc"
    assert preview.detected is not None
    assert preview.detected.profile.name == "boc"
    assert [issue for issue in preview.plan.issues if issue.level == "error"] == []

    rows = preview.plan.rows
    assert len(rows) == len(BOC_ROWS)
    assert {row.transaction.currency for row in rows} == {"GBP", "CNY", "USD"}

    # 本人对方（英文名）→ 中性，金额保留。
    transfer = next(row for row in rows if row.transaction.description == "国际汇款")
    assert transfer.transaction.direction is Direction.NEUTRAL
    assert transfer.transaction.amount_cents == 301200
    assert transfer.transaction.counterparty == "HONGYANG SHEN"

    # 「支付宝-沈宏阳」是子串，也该判中性。
    alipay = next(row for row in rows if "支付宝-沈宏阳" == row.transaction.counterparty)
    assert alipay.transaction.direction is Direction.NEUTRAL

    # 退款不是收入。
    refund = next(row for row in rows if row.transaction.description == "网上快捷退款")
    assert refund.transaction.direction is Direction.NEUTRAL

    # 外部消费、工资照常收支。
    expense = next(row for row in rows if row.transaction.description == "网上快捷支付")
    assert expense.transaction.direction is Direction.OUT
    assert expense.transaction.amount_cents == 253996
    salary = next(row for row in rows if row.transaction.description == "小额定期")
    assert salary.transaction.direction is Direction.IN

    # 预览合计按币种分组，绝不跨币种相加。
    assert preview.totals_by_currency() == {
        "CNY": (180200, 253996),
        "USD": (1, 0),
    }


def test_boc_time_is_converted_from_shanghai(conn, isolated_data_dir):
    _write_settings(isolated_data_dir, "Europe/London")

    preview = ingest.stage(conn, "332150.pdf", boc_pdf())
    row = next(
        row
        for row in preview.plan.rows
        if row.transaction.description == "国际汇款"
    )

    # 2026-10-14 19:00:46 北京 = 12:00:46 伦敦（BST）。
    assert row.transaction.occurred_at == "2026-10-14 12:00:46"


def test_boc_confirm_is_idempotent_and_creates_the_account(conn):
    data = boc_pdf()
    first = ingest.stage(conn, "332150.pdf", data)

    assert first.counts()["new"] == len(BOC_ROWS)
    assert first.counts()["neutral"] == 4

    ingest.confirm(conn, first.token)

    account = conn.execute(
        "SELECT name FROM accounts WHERE name = '中国银行'"
    ).fetchone()
    assert account is not None
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE profile_name = 'boc'"
    ).fetchone()["n"] == len(BOC_ROWS)

    again = ingest.stage(conn, "332150.pdf", data)
    assert again.counts()["new"] == 0
    assert again.counts()["duplicate"] == len(BOC_ROWS)
