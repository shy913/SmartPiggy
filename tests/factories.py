"""测试样本用代码造，不往仓库里塞二进制。

GBK 字节放在 git 里既不好评审也不好手改；这边一个函数就能生成。
等真实样本到了，改一行参数就能对上——这正是「拿到样本后调整成本要低」
那条要求的落点。
"""

from __future__ import annotations

import io
import zipfile

import pytest

__all__ = [
    "ALIPAY_HEADER",
    "BOC_HEADER",
    "BOC_ROWS",
    "MONZO_HEADER",
    "WECHAT_HEADER",
    "alipay_csv",
    "alipay_zip",
    "bank_csv",
    "boc_credit_pdf",
    "boc_pdf",
    "boc_text_pdf",
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


# --------------------------------------------------------------------------
# 中国银行 PDF
#
# 用 reportlab 现造，不把真实账单塞进仓库。中文必须用 TrueType 字体：
# reportlab 自带的 CID 字体（STSong-Light）在 pdfminer 里会抽成 ``(cid:…)``，
# 拿来做断言毫无意义。这台机器上有文泉驿，没有就跳过这些用例。
# --------------------------------------------------------------------------

BOC_HEADER = [
    "记账日期", "记账时间", "币别", "金额", "余额", "交易名称",
    "渠道", "网点名称", "附言", "对方账户名", "对方卡号/账号", "对方开户行",
]

BOC_ROWS = [
    # 对方是英文名：国际汇款到自己的 Monzo，应该中性。
    ["2026-10-14", "19:00:46", "英镑", "-3,012.00", "659.74", "国际汇款",
     "手机银行", "---", "TRANSFER 3K", "HONGYANG SHEN",
     "GB84MONZ0400\n58", "MONZO BANK LIMITED"],
    # 外部消费：携程。
    ["2026-10-09", "03:15:33", "人民币", "-2,539.96", "110,235.31", "网上快捷支付",
     "银企对接", "---", "携程", "程支付-携程旅行网", "Z2000731000015N", "---"],
    # 结息：外部收入。
    ["2026-09-21", "01:58:58", "人民币", "2.00", "118,275.27", "结息",
     "其他", "---", "---", "---", "---", "---"],
    # 对方是本人中文名：转给自己另一张卡，应该中性。
    ["2026-06-18", "13:59:22", "英镑", "-500.00", "700.00", "转账",
     "手机银行", "---", "---", "沈宏阳", "4002210800385820", "中行祁连山路支行"],
    # 退款：不是收入，应该中性。
    ["2026-05-14", "02:46:54", "人民币", "4,029.00", "5,000.00", "网上快捷退款",
     "银企对接", "---", "携程", "程支付-携程旅行网", "80001030000701", "---"],
    # 工资：外部收入。
    ["2026-03-06", "16:22:51", "人民币", "1,800.00", "9,000.00", "小额定期",
     "支付清算部", "---", "工资", "上海中智外包", "121914180210505", "招行徐汇支行"],
    # 美元结息：第三种币别。
    ["2026-09-21", "01:59:26", "美元", "0.01", "530.01", "结息",
     "其他", "---", "---", "---", "---", "---"],
    # 对方是「支付宝-沈宏阳」：名字里带着平台前缀，按子串也应该判中性。
    ["2026-01-03", "22:34:26", "人民币", "-20,000.00", "100,000.00", "网上快捷支付",
     "银企对接", "---", "支付宝", "支付宝-沈宏阳", "Z2007933000010N", "---"],
]

_BOC_FONT_NAME = "WQY-ZenHei"
_BOC_FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"


def _boc_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if _BOC_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont(_BOC_FONT_NAME, _BOC_FONT_PATH, subfontIndex=0)
        )
    return _BOC_FONT_NAME


def boc_pdf(
    rows: list[list[str]] | None = None,
    *,
    password: str = "332150",
    repeat: int = 1,
) -> bytes:
    """一份中国银行形态的加密 PDF，表头每页重复。

    ``repeat`` 用来把行数放大到跨页，验证「每页重复表头只留一次」。
    """
    import pathlib

    if not pathlib.Path(_BOC_FONT_PATH).exists():
        pytest.skip("没有可用的中文字体，跳过 PDF 用例")

    from reportlab.lib import colors, pdfencrypt
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    rows = BOC_ROWS if rows is None else rows
    table = Table(
        [BOC_HEADER, *[row for row in rows for _ in range(repeat)]],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), _boc_font()),
                ("FONTSIZE", (0, 0), (-1, -1), 6),
                ("LEADING", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=18,
        rightMargin=18,
        topMargin=18,
        bottomMargin=18,
        encrypt=pdfencrypt.StandardEncryption(password, canPrint=0),
    )
    document.build([table])
    return buffer.getvalue()


BOC_CREDIT_HEADER = [
    "交易日\nTransaction Date",
    "银行记账日\nPosting Date",
    "卡号后四位\nLast Four\nDigits\nof Card Number",
    "交易描述\nDescription",
    "存入\nDeposit",
    "支出\nExpenditure",
]

BOC_CREDIT_RMB_ROWS = [
    # 网银还款：卡片和人民币/外币两个小节各出现一次，都应该判中性。
    ["2026-07-28", "2026-07-28", "5805", "BOCNET", "100.00", ""],
    ["2026-07-28", "2026-07-29", "5805", "支付宝-中国联通 CHN", "", "40.00"],
    ["2026-08-02", "2026-08-03", "5805", "微信-肯德基 CHN", "", "4.00"],
]

BOC_CREDIT_GBP_ROWS = [
    # 返现：收入。
    ["2026-09-15", "2026-09-17", "5805", "MCBOC1percentrebate CHN", "0.10", ""],
    # 人民币小节之后紧跟着外币小节，同一天同样金额也不能串币种。
    ["2026-09-16", "2026-09-17", "5805", "支付宝-China Mobile", "", "8.92"],
    # 存款利息：收入。
    ["2026-09-20", "2026-09-20", "", "存款利息", "0.03", ""],
]

BOC_CREDIT_CONTINUATION_ROWS = [
    # 溢缴款转出：中性。
    ["2026-09-26", "2026-09-26", "5805", "BOCNET", "", "800.00"],
    ["2026-09-30", "2026-10-02", "5805", "UBER *TRIPGBR", "", "6.96"],
    # 退款：收入，但不算消费。
    ["2026-10-07", "2026-10-10", "5805", "UBER *EATSGBR", "2.69", ""],
    # 还款：中性。
    ["2026-10-11", "2026-10-11", "5805", "BOCNET", "100.00", ""],
]


def boc_credit_pdf() -> bytes:
    """中国银行「信用卡电子合并账单」形态的 PDF。

    跟储蓄卡流水的区别都用这份样本覆盖：一页两张表、币种写在标题里、
    下一节跨页后不再重复表头、页眉还有几张不像交易表的汇总表。
    """
    import pathlib

    if not pathlib.Path(_BOC_FONT_PATH).exists():
        pytest.skip("没有可用的中文字体，跳过 PDF 用例")

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

    font = _boc_font()
    style = ParagraphStyle("heading", fontName=font, fontSize=9, leading=12)

    def grid(rows):
        table = Table(rows)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("LEADING", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        return table

    # 页眉汇总表：列名对不上，读取层应该跳过而不是当交易表。
    summary = grid(
        [
            ["到期还款日\nPayment Due Date", "账单日\nStatement Closing Date"],
            ["", "2026-09-16"],
        ]
    )

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18,
        rightMargin=18,
        topMargin=18,
        bottomMargin=18,
    )
    document.build(
        [
            summary,
            Paragraph("人民币交易明细/RMB Transaction Detailed List", style),
            grid([BOC_CREDIT_HEADER, *BOC_CREDIT_RMB_ROWS]),
            Paragraph("(GBP)外币交易明细/FCY Transaction Detailed List", style),
            grid([BOC_CREDIT_HEADER, *BOC_CREDIT_GBP_ROWS]),
            PageBreak(),
            # 跨页的续表：没有表头，靠列数接上上一节（外币）。
            grid(BOC_CREDIT_CONTINUATION_ROWS),
        ]
    )
    return buffer.getvalue()


def boc_text_pdf() -> bytes:
    """没有表格的文字版 PDF：用来验证「扫描件 / 没有表格」的可读报错。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.drawString(72, 750, "No table here.")
    pdf.save()
    return buffer.getvalue()
