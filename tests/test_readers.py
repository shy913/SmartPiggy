"""读取层：编码、表头定位、压缩包、Excel。"""

from __future__ import annotations

import pytest

from bill.config import MAX_BYTES, MAX_ROWS
from bill.errors import UploadError
from bill.readers import build_grid, find_header, read_table

from .factories import ALIPAY_HEADER, alipay_csv, alipay_zip, bank_csv, wechat_xlsx

PROBES = ("交易时间", "收/支", "金额", "交易日期", "交易金额")


def test_gbk_is_not_silently_mangled():
    """回归：cp1252 / latin-1 对任何字节都不会报错，以前会「成功」把 GBK
    解成乱码还不吭声。现在按中文占比打分，必须选出 gb18030。"""
    table = read_table("支付宝账单.csv", alipay_csv(), probes=PROBES)
    assert table.encoding in ("gb18030", "gbk")
    text = "\n".join(",".join(row) for row in table.rows)
    assert "交易时间" in text
    assert "美团" in text
    assert "�" not in text


def test_utf8_with_non_ascii_is_not_mistaken_for_gbk():
    """回归：Monzo 账单是 UTF-8，里面有 emoji。

    那些 emoji 的字节被 gb18030 误解时会解出一堆汉字，于是在「中文占比」上
    反而把正确的 UTF-8 比了下去，整份文件里所有非 ASCII 文字都成了乱码。
    所以「合法 UTF-8 就是 UTF-8」这条判断必须排在按字符打分之前。
    """
    data = (
        "Transaction ID,Name,Amount\n"
        "tx_1,Apple 🍎,0.00\n"
        "tx_2,Café Sainsbury’s,-3.85\n"
    ).encode("utf-8")

    table = read_table("monzo.csv", data, probes=("Transaction ID", "Amount"))
    assert table.encoding == "utf-8"
    text = "\n".join(",".join(row) for row in table.rows)
    assert "🍎" in text
    assert "Café Sainsbury’s" in text, "非 ASCII 的商家名不能变成乱码"


def test_utf8_with_bom_loses_the_bom():
    line = "2026-01-05,,,x,支出,1.00,,,,,,\n"
    data = ("\ufeff" + ALIPAY_HEADER + "\n" + line).encode("utf-8")
    assert data.startswith(b"\xef\xbb\xbf")
    table = read_table("账单.csv", data, probes=("交易时间", "收/支", "金额"))
    assert table.encoding == "utf-8-sig"
    assert table.rows[0][0] == "交易时间"


def test_header_is_found_after_the_preamble():
    table = read_table("支付宝账单.csv", alipay_csv(footer=False), probes=PROBES)
    index = find_header(table.rows, ("交易时间", "收/支", "金额"), start_after="交易记录明细列表")
    assert index is not None
    assert index > 0, "表头不该在第 0 行——前面还有账户说明"
    grid = build_grid(table, index)
    assert grid.header[:3] == ["交易时间", "交易分类", "交易对方"]
    assert len(grid.rows) == 3


def test_start_after_skips_an_earlier_row_that_looks_like_the_header():
    """分隔行之后才是真表头时，start_after 要能跳过前面那个假的。"""
    probes = ("交易时间", "收/支", "金额")
    rows = [
        ["交易时间", "收/支", "金额"],
        ["------------------交易记录明细列表------------------"],
        ["交易时间", "收/支", "金额"],
        ["2026-01-01", "支出", "1.00"],
    ]
    assert find_header(rows, probes) == 0
    assert find_header(rows, probes, start_after="交易记录明细列表") == 2


def test_find_header_returns_none_instead_of_guessing():
    rows = [["随便", "什么"], ["都", "没有"]]
    assert find_header(rows, ("交易时间", "收/支")) is None


def test_zip_picks_the_detail_member_and_skips_junk():
    table = read_table("支付宝账单.zip", alipay_zip(footer=False), probes=PROBES)
    assert table.member.endswith("明细.csv"), "汇总表一个列名都对不上，应该被挑掉"
    grid = build_grid(table, find_header(table.rows, ("交易时间", "收/支", "金额")))
    assert len(grid.rows) == 3
    assert grid.cell(grid.rows[0], "交易对方") == "美团平台商户"
    assert any("跳过" in note for note in table.notes)


def test_zip_named_csv_is_still_read_as_a_zip():
    """支付宝有时把压缩包导成 .csv 后缀，靠魔数也能认出来。"""
    table = read_table("支付宝账单.csv", alipay_zip(), probes=PROBES)
    assert table.member.endswith("明细.csv")


def test_xlsx_is_not_mistaken_for_a_zip():
    """回归：xlsx 本身就是个 zip 包，按后缀必须先判 Excel。"""
    table = read_table("微信支付账单.xlsx", wechat_xlsx())
    assert table.kind == "xlsx"
    grid = build_grid(table, find_header(table.rows, ("交易时间", "收/支", "金额(元)")))
    assert grid.cell(grid.rows[0], "金额(元)") == "38.5"
    assert grid.cell(grid.rows[0], "交易对方") == "星巴克"
    # 浮点单元格不该带出二进制尾巴
    assert grid.cell(grid.rows[0], "金额(元)") != "38.500000000000004"


def test_tsv_delimiter_is_detected():
    data = "交易日期\t交易金额\n20260105\t-12.00\n".encode()
    table = read_table("明细.tsv", data, probes=("交易日期", "交易金额"))
    assert table.delimiter == "\t"
    assert table.rows[1][0] == "20260105"


@pytest.mark.parametrize(
    "filename, payload, fragment",
    [
        ("账单.xls", b"whatever", ".xls"),
        ("账单.docx", b"whatever", "不认识"),
    ],
)
def test_unsupported_types_are_rejected_with_a_readable_message(filename, payload, fragment):
    with pytest.raises(UploadError) as info:
        read_table(filename, payload)
    assert fragment in str(info.value)


def test_empty_and_oversized_are_rejected():
    with pytest.raises(UploadError, match="空的"):
        read_table("a.csv", b"")
    with pytest.raises(UploadError, match="MB"):
        read_table("a.csv", b"x" * (MAX_BYTES + 1))
    with pytest.raises(UploadError, match="选一个文件"):
        read_table("  ", b"data")


def test_more_than_the_old_500_row_cap_is_read_in_full():
    """回归：老实现的 MAX_ROWS=500 是给预览用的截断。导入若复用它，
    500 行以后的记录会静默消失。现在读取上限要高得多。"""
    rows = [
        (f"2026-01-{(i % 28) + 1:02d} 10:00:00", "餐饮美食", f"商户{i}", "", "消费",
         "支出", "¥1.00", "余额宝", "交易成功", f"202601{i:06d}", "", "")
        for i in range(1200)
    ]
    table = read_table("支付宝账单.csv", alipay_csv(rows, footer=False), probes=PROBES)
    grid = build_grid(table, find_header(table.rows, ("交易时间", "收/支", "金额")))
    assert len(grid.rows) == 1200
    assert MAX_ROWS > 1200
    assert not table.truncated


def test_footer_rows_are_kept_in_the_grid_and_left_for_the_importer():
    """读取层不替导入层做丢行的决定——它只管把行读出来，丢什么由配置说了算。"""
    table = read_table("支付宝账单.csv", alipay_csv(), probes=PROBES)
    grid = build_grid(table, find_header(table.rows, ("交易时间", "收/支", "金额")))
    assert any("共3笔记录" in "".join(row) for row in grid.rows)


def test_bank_csv_with_crlf_and_bom():
    data = bank_csv().replace(b"\n", b"\r\n")
    table = read_table("明细.csv", data, probes=PROBES)
    grid = build_grid(table, find_header(table.rows, ("交易日期", "交易金额")))
    assert grid.cell(grid.rows[0], "交易金额") == "-88.00"
