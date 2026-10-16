"""界面参数：把地址栏/表单里的东西读成取数用的几个值。

这些以前长在 ``web.py`` 里。搬到这儿是因为它们现在有**两个**调用方——服务端
渲染的旧页面，和 JSON 接口。同一套语义在两个地方各写一份，迟早会有一处改歪：
改歪的后果不是报错，是「列表页和汇总页对同一个时间区间各算各的」。

这里只做「字符串 → 值」的翻译，不碰数据库、不碰模板。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import dates

# 交易记录页一次列多少条。这一页不分页——「下一页」会把人从刚才那一屏拽走，
# 而账目是要一路翻着看的。改成一次给一段，底下挂一个「继续下滑加载更多」，
# 点一下这一段接着往下长，位置不动。
LIST_STEP = 200

# 汇总页的四种区间。「最近 x 天」没给数、或者给了一个认不出的数时的落点：
# 三十天差不多是一个「最近的开销」——再长就该用「按月」了。
SUMMARY_MODES = ("month", "year", "days", "all")
DEFAULT_DAYS = 30

_DIRECTION_WORDS = {"in": "收入", "out": "支出", "neutral": "中性"}
_HIDDEN_WORDS = {"0": "不含隐藏", "1": "只看隐藏"}


def as_int(value) -> int | None:
    """表单里来的 id。认不出来就当没有——勾选列表本来就可能被改坏。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def default_range(conf) -> dict[str, str]:
    """筛选栏没给条件时落回的那个区间：当月 1 号到今天。

    按**显示时区**算「今天」，不按服务器本地时间：``occurred_date`` 入库时
    就按显示时区换算过（见 dates.convert_timezone），拿另一套时钟去比，月初和
    月末那两天会差出一天——屏幕上就是「昨天的账不见了」。

    返回的形状跟 ``dates.month_range`` 一样（``start`` / ``end``），两者都是
    「一个区间」，链接和取数都直接展开用。
    """
    today = datetime.now(conf.tz()).date()
    return {"start": today.replace(day=1).isoformat(), "end": today.isoformat()}


def clean_date(value: str | None) -> str:
    """筛选栏里的一个日期。认不出来就返回空串（＝那一端不限）。

    只认 ``'YYYY-MM-DD'`` 这一种写法，认出来之后**重新写一遍**：库里的
    ``occurred_date`` 是定长的，比较靠的是字典序，放过 ``'2026-1-5'`` 这种
    看着对、比出来错的写法，等于给出一个不报错的错结果。手改地址栏塞进来的
    东西也一样——与其在筛选栏里留一个查不出记录的隐形条件，不如当没给。
    """
    text = (value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def clean_month(value: str | None) -> str:
    """汇总页选的那个月份（``'YYYY-MM'``）。认不出来返回空串。

    跟 ``clean_date`` 同一个道理：认出来之后重新写一遍，别放过 ``'2026-1'``
    这种看着对、比出来错的写法——它就是靠字典序当月份边界用的。
    """
    text = (value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m")
    except ValueError:
        return ""
    return f"{parsed.year:04d}-{parsed.month:02d}"


def clean_year(value: str | None) -> str:
    """汇总页选的那个年份（四位）。认不出来返回空串。"""
    text = (value or "").strip()
    if len(text) != 4 or not text.isdigit():
        return ""
    return text


def clean_days(value: str | None) -> int:
    """「最近 x 天」里的那个 x。认不出来落回 ``DEFAULT_DAYS``。

    **不接受 0 或负数**：那会算出一个 ``start > end`` 的空区间，屏幕上写着
    「最近 0 天」而一条记录都没有——看着像「这些天真没记账」，实际是条件本身
    就不成立。与其给一个空屏，不如当没给。
    """
    number = as_int((value or "").strip())
    if number is None or number < 1:
        return DEFAULT_DAYS
    return number


def year_range(year: str) -> dict[str, str]:
    """``'2026'`` → ``{'start': '2026-01-01', 'end': '2026-12-31'}``。"""
    return {"start": f"{year}-01-01", "end": f"{year}-12-31"}


def recent_range(conf, days: int) -> dict[str, str]:
    """最近 ``days`` 天，**含今天在内**。

    「最近 30 天」读起来就是一端到今天、往回数 30 天的那个窗口，所以起点是
    今天往前 ``days - 1`` 天。按**显示时区**算今天——跟 ``default_range``
    同一个时钟，不然两处会在月初月末差出一天。
    """
    today = datetime.now(conf.tz()).date()
    return {
        "start": (today - timedelta(days=days - 1)).isoformat(),
        "end": today.isoformat(),
    }


def summary_range(conf, source) -> dict[str, str | int]:
    """汇总页的四种区间 → 一个具体的起止区间。

    数据层只认 ``start`` / ``end``（跟交易记录页同一套谓词），**「模式」是这一层
    的事**：这里把它折成两端日期，``repo`` 完全不知道有「模式」这回事。

    判的是参数**在不在**，跟 ``list_query`` 一样。但语义有个区别值得写下来：
    交易记录页里空值是有意义的（``?start=&end=`` ＝ 不限），这里**空值和垃圾值
    是同一件事**——都不是用户表达出来的意图，一律落回默认的当月。想要「不限」
    得显式写 ``range=all``。手改地址栏塞进来一个 ``?range=x``，最坏的结果应该
    是回到默认视图，而不是给出一个用户没要的、更大的数据窗口，屏幕上什么都不说。

    返回的 ``month`` / ``year`` / ``days`` 是**生效值**（不是原始输入），
    表单和链接直接拿去用，这样从「按月」切到「按年」不会把年份丢掉。
    """
    mode = (source.get("range") or "").strip() if "range" in source else ""
    if mode not in SUMMARY_MODES:
        mode = "month"

    today = datetime.now(conf.tz()).date()
    # 三个取值**一律填成生效值**，哪怕当前模式用不上：界面上的模式是链接，
    # 每条链都得带着另外两个值，否则从「按月」切到「按年」会发现自己手里没有
    # 年份，只能落回默认——用户看到的就是「刚切过去又被弹回来了」。
    month = clean_month(source.get("month")) or f"{today.year:04d}-{today.month:02d}"
    year = clean_year(source.get("year")) or f"{today.year:04d}"
    days = clean_days(source.get("days"))

    if mode == "all":
        return {
            "mode": mode, "month": month, "year": year, "days": days,
            "start": "", "end": "", "label": "全部记录",
        }

    if mode == "year":
        bounds = year_range(year)
        return {**bounds, "mode": mode, "month": month, "year": year,
                "days": days, "label": year}

    if mode == "days":
        bounds = recent_range(conf, days)
        return {**bounds, "mode": mode, "month": month, "year": year,
                "days": days, "label": f"最近 {days} 天"}

    bounds = dates.month_range(month)
    return {**bounds, "mode": mode, "month": month, "year": year,
            "days": days, "label": month}


def active_filter_labels(
    accounts,
    *,
    start="",
    end="",
    direction="",
    account_id=None,
    search="",
    hidden="",
) -> list[str]:
    """当前生效的筛选条件，写成几个短标签。

    筛选栏是能收起来的，收起来之后页面上就只剩一个标题——不把这些条件明写出来，
    这一屏看着就跟全部记录一模一样，而合计数又是按筛过的算的，两边对不上还
    找不到原因。账户写名字不写 id：id 是给人看的表格里最没用的一种数。

    顺序跟着筛选栏里那几个控件走，读到的和看到的对得上。
    """
    labels = []
    # 两个日期都在的时候只写一行「甲 至 乙」：它们是同一个条件的两个端点，
    # 拆成两条读起来像两个并排的条件。
    if start and end:
        labels.append(f"{start} 至 {end}")
    elif start:
        labels.append(f"{start} 起")
    elif end:
        labels.append(f"至 {end}")
    if account_id:
        labels.append(
            next(
                (row["name"] for row in accounts if row["id"] == account_id),
                "账户已不在",
            )
        )
    if word := _DIRECTION_WORDS.get(direction):
        labels.append(word)
    if word := _HIDDEN_WORDS.get(hidden):
        labels.append(word)
    if search:
        labels.append(f"搜「{search}」")
    return labels


def list_query(source, conf) -> dict:
    """从查询串或表单里取交易列表那几个筛选字段。认不出的 hidden 落回「照常列出」。

    ``conf`` 由调用方传进来，不在这里现读：一次请求里读两遍设置，万一中间那份
    文件被改了，同一个页面上两处会用着不同的显示币种。
    """
    hidden = (source.get("hidden") or "").strip()
    if hidden not in ("0", "1"):
        hidden = ""
    # 起止日期就是原来那个「月份」筛选，只是把一头拆成两头。没给参数时落回
    # 「当月 1 号到今天」——这一页平时看的就是本月的账。
    #
    # 判的是参数**在不在**，不是值空不空：清空两个输入框提交，得到的是
    # ``?start=&end=``，意思是「不限」，跟没给参数（＝回到默认的当月）是两件
    # 事。要分清这两种，只能看 key。
    if "start" in source or "end" in source:
        bounds = {
            "start": clean_date(source.get("start")),
            "end": clean_date(source.get("end")),
        }
    else:
        bounds = default_range(conf)
    return {
        **bounds,
        "direction": (source.get("direction") or "").strip(),
        "account_id": as_int(source.get("account") or ""),
        "search": (source.get("q") or "").strip(),
        "hidden": hidden,
        # limit 是「这一段给到哪」，不是页大小，所以只往上加：链接里手改出来的
        # 小数不至于把列表缩成一条。
        "limit": max(source.get("limit", type=int) or LIST_STEP, LIST_STEP),
    }


def list_hint(total: int, hidden_total: int) -> str:
    """列表页头那一行字。

    字写在服务端，是因为「隐藏」那一下不重载页面，得拿同一条字回去把页头换掉
    ——写两遍迟早会写歪一处。
    """
    line = f"共 {total} 条"
    if hidden_total:
        line += f"，其中 {hidden_total} 条已隐藏"
    return line + "。"
