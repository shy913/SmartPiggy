"""测试样本用代码造，不往仓库里塞二进制。

GBK 字节放在 git 里既不好评审也不好手改；这边一个函数就能生成。
等真实样本到了，改一行参数就能对上——这正是「拿到样本后调整成本要低」
那条要求的落点。
"""

from __future__ import annotations

import io
import zipfile

__all__ = [
    "ALIPAY_HEADER",
    "MONZO_HEADER",
    "WECHAT_HEADER",
    "alipay_csv",
    "alipay_zip",
    "bank_csv",
    "monzo_csv",
    "wechat_xlsx",
]

ALIPAY_HEADER = (
    "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,"
    "交易状态,交易订单号,商家订单号,备注"
)

WECHAT_HEADER = [
    "交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
    "支付方式", "当前状态", "交易单号", "商户单号", "备注",
]

ALIPAY_ROWS = [
    ("2026-01-05 12:30:45", "餐饮美食", "美团平台商户", "", "美团外卖",
     "支出", "¥32.50", "余额宝", "交易成功", "2026010522001", "MT001", ""),
    ("2026-01-06 09:15:00", "转账红包", "张三", "", "转账",
     "不计收支", "¥100.00", "余额", "交易成功", "2026010622002", "", ""),
    ("2026-01-07 18:02:11", "收入", "某公司", "", "工资",
     "收入", "¥12000.00", "银行卡", "交易成功", "2026010722003", "", ""),
]

WECHAT_ROWS = [
    ["2026-02-01 08:00:00", "商户消费", "星巴克", "拿铁", "支出", 38.5,
     "零钱", "支付成功", "WX001", "M001", ""],
    ["2026-02-02 12:00:00", "转账", "李四", "转账", "/", 200,
     "零钱", "已转账", "WX002", "M002", ""],
    ["2026-02-03 09:00:00", "退款", "淘宝", "退款", "收入", 38.5,
     "零钱", "已退款", "WX003", "M003", ""],
]

_ALIPAY_PREAMBLE = [
    "支付宝交易记录明细查询",
    "账号:[test@example.com]",
    "起始日期:[2026-01-01 00:00:00]    终止日期:[2026-01-31 23:59:59]",
    "---------------------------------交易记录明细列表------------------------------------",
]

_ALIPAY_FOOTER = [
    "--------------------------------------------------------------------------------------",
    "共3笔记录",
    "已收入:12000.00元",
    "已支出:32.50元",
]


MONZO_HEADER = [
    "Transaction ID", "Date", "Time", "Type", "Name", "Emoji", "Category",
    "Amount", "Currency", "Local amount", "Local currency", "Notes and #tags",
    "Address", "Receipt", "Description", "Category split", "Money Out", "Money In",
]

MONZO_ROWS = [
    # 零金额的「Active card check」——不是真交易，该落成中性而不是被算成支出。
    ["tx_001", "24/09/2026", "07:47:46", "Card payment", "Apple", "🍎 ", "Entertainment",
     "0.00", "GBP", "0.00", "GBP", "Active card check", "Hollyhill Estate", "",
     "APPLE.COM/BILL         CORK          IRL", "", "0.00", ""],
    # 正数是收入
    ["tx_002", "28/09/2026", "08:41:12", "chaps", "", "", "Transfers",
     "1000.00", "GBP", "1000.00", "GBP", "TRANSFER 1K", "", "", "TRANSFER 1K", "", "", "1000.00"],
    # 负数是支出
    ["tx_003", "28/09/2026", "12:26:21", "Card payment", "Tesco", "🍏 ", "Groceries",
     "-3.85", "GBP", "-3.85", "GBP", "", "Park", "",
     "TESCO STORES 6636      BIRMINGHAM    GBR", "", "-3.85", ""],
    ["tx_004", "03/10/2026", "16:58:56", "Card payment", "Happy Lamb", "🍽", "Eating out",
     "-55.40", "GBP", "-55.40", "GBP", "", "21 Ladywell Walk", "",
     "Happy Lamb Hotpot Lady Birmingham    GBR", "", "-55.40", ""],
]


def monzo_csv(rows: list[list[str]] | None = None) -> bytes:
    """Monzo 导出：日期时间分列、金额带正负号、有币种列。

    表头在第一行，前面没有说明行——跟支付宝、微信的形态差别很大。
    """
    rows = MONZO_ROWS if rows is None else rows
    lines = [",".join(MONZO_HEADER)]
    lines.extend(
        ",".join('"' + cell.replace('"', '""') + '"' if "," in cell else cell for cell in row)
        for row in rows
    )
    # Monzo 导出带 BOM。
    return ("\ufeff" + "\n".join(lines) + "\n").encode("utf-8")


def alipay_csv(
    rows: list[tuple] | None = None,
    *,
    preamble: bool = True,
    footer: bool = True,
    encoding: str = "gb18030",
) -> bytes:
    """支付宝导出的明细 CSV。默认按真实情况带前言和末尾汇总。"""
    rows = ALIPAY_ROWS if rows is None else rows
    lines: list[str] = []
    if preamble:
        lines.extend(_ALIPAY_PREAMBLE)
    lines.append(ALIPAY_HEADER)
    lines.extend(",".join(row) for row in rows)
    if footer:
        lines.extend(_ALIPAY_FOOTER)
    return ("\n".join(lines) + "\n").encode(encoding)


def alipay_zip(rows: list[tuple] | None = None, *, footer: bool = True) -> bytes:
    """支付宝有时把明细和汇总一起打包成 zip。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("__MACOSX/._明细.csv", b"junk")
        archive.writestr(
            "202601_alipay/汇总.csv",
            "账户总览\n收入合计,12000.00\n支出合计,32.50\n".encode("gb18030"),
        )
        archive.writestr("202601_alipay/明细.csv", alipay_csv(rows, footer=footer))
    return buffer.getvalue()


def wechat_xlsx(rows: list[list] | None = None, *, preamble: bool = True) -> bytes:
    from openpyxl import Workbook

    rows = WECHAT_ROWS if rows is None else rows
    workbook = Workbook()
    sheet = workbook.active
    if preamble:
        sheet.append(["微信支付账单明细"])
        sheet.append(["微信昵称：[测试]"])
        sheet.append([])
    sheet.append(WECHAT_HEADER)
    for row in rows:
        sheet.append(row)
    sheet.append(["共3笔记录"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def bank_csv(
    rows: list[list[str]] | None = None,
    *,
    header: list[str] | None = None,
    encoding: str = "utf-8",
) -> bytes:
    """一份「单列带正负号」的银行流水，没有唯一流水号——用来验证内容哈希去重。"""
    header = header or ["交易日期", "交易摘要", "对方户名", "交易金额", "余额"]
    rows = rows if rows is not None else [
        ["20260110", "消费", "某超市", "-88.00", "1234.56"],
        ["20260111", "工资", "某公司", "+8000.00", "9234.56"],
        ["20260112", "转出", "张三", "-200.00", "9034.56"],
    ]
    lines = [
        "个人账户交易明细",
        "账号:[6222****1234]",
        ",".join(header),
        *(",".join(row) for row in rows),
        "合计,,,-1712.00,",
    ]
    return ("\n".join(lines) + "\n").encode(encoding)
