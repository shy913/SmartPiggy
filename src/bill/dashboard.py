"""首页概览：一个区间里的数、往前看的预测、往回看的平均。

这里只做**计算**，SQL 全在 ``repo``。之所以单独一层：预测和平均都要拿好几个
取数结果拼（本月、本年、过去 12 个完整月），塞进 ``api.py`` 会让那个只做
「取数 + 序列化」的地方开始写业务日期算术。

几条口径，跟别处一致：

- **大额与中性行不进这里的任何金额**——跟汇总页的合计同一个来路；
- **绝不跨币种相加**。调用方一次只问一个币种，预测、平均、趋势、排行都是
  那个币种自己的；
- 金额一律整数最小单位。比例不在服务端算，交给前端从两个整数得出。
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from . import categories, dates, params, repo

# 「月均 / 年均」看最近多少个**完整**月。完整 = 不含今天所在的那个月：
# 半个月的支出拿去平均，只会把月均拉低，而屏幕上看不出来。
AVERAGE_MONTHS = 12

# 一天一个点最多铺多少天。再长就换成按月，不然「最近 1000 天」会给出一千个
# 点，图上看不出东西，手机上还会卡。
MAX_DAY_BUCKETS = 120

# 单次排行一次给多少条。界面先露 5 条，「更多」时展开到这么多。
RANKING_LIMIT = 20


def _shift_month(day: date, months: int) -> date:
    """把这个月的 1 号往前/往后挪几个月。"""
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _month_text(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _parse(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _round_divide(numerator: int, denominator: int) -> int:
    """整数四舍五入。金额在整个后端都是整数，这里不引入浮点。"""
    if denominator <= 0:
        return 0
    return (numerator + denominator // 2) // denominator


def _empty_total(code: str) -> dict:
    return {
        "display_currency": code,
        "income": 0,
        "expense": 0,
        "neutral_amount": 0,
        "neutral_count": 0,
        "large_income": 0,
        "large_expense": 0,
        "large_count": 0,
        "total": 0,
    }


def _total_of(rows, code: str) -> dict:
    """从按币种分组的合计里取这一个币种。没有就是零，不是「没有这个字段」。"""
    for row in rows:
        if row["display_currency"] == code:
            return dict(row)
    return _empty_total(code)


def _natural_end(mode: str, span: dict, fallback: date) -> date:
    if mode == "month":
        return _parse(dates.month_range(span["month"])["end"]) or fallback
    if mode == "year":
        return date(int(span["year"]), 12, 31)
    if mode == "days":
        return _parse(span["end"]) or fallback
    return fallback


def _previous_span(mode: str, span: dict, today: date) -> dict | None:
    """上一个同长度区间。当前这个还没过完时，比的是**去年同期同一天**。

    「本月 1–18 日」跟「上月整月」比，读起来就是一条往下掉的线；比到同一天，
    才是「跟上个月比花得多还是少」。
    """
    if mode == "month":
        text = span["month"]
        year, number = int(text[:4]), int(text[5:7])
        previous = _shift_month(date(year, number, 1), -1)
        full = dates.month_range(_month_text(previous))
        if _parse(span["end"]) and _parse(span["end"]) >= today:
            day = min(today.day, calendar.monthrange(previous.year, previous.month)[1])
            return {
                "start": full["start"],
                "end": f"{_month_text(previous)}-{day:02d}",
                "label": "上月同期",
            }
        return {**full, "label": "上月"}
    if mode == "year":
        year = int(span["year"])
        full = {"start": f"{year - 1:04d}-01-01", "end": f"{year - 1:04d}-12-31"}
        if _parse(span["end"]) and _parse(span["end"]) >= today:
            day = min(
                today.day,
                calendar.monthrange(year - 1, today.month)[1],
            )
            return {
                "start": f"{year - 1:04d}-01-01",
                "end": f"{year - 1:04d}-{today.month:02d}-{day:02d}",
                "label": "上年同期",
            }
        return {**full, "label": "上年"}
    return None


def _forecast(total: dict, today: date) -> dict:
    """按今年/本月到目前为止的日均，外推整月、整年。

    只外推**支出**：收入常常是一笔一笔来的（工资、报销），把一次工资按天摊到
    年底会得出一个没有意义的数。支出是连续发生的，日均外推至少是可信的。
    """
    month_days = calendar.monthrange(today.year, today.month)[1]
    year_days = 366 if calendar.isleap(today.year) else 365
    spent_month = total["expense_month"]
    spent_year = total["expense_year"]
    return {
        "month": {
            "spent": spent_month,
            "projected": _round_divide(spent_month * month_days, today.day),
            "elapsed_days": today.day,
            "total_days": month_days,
        },
        "year": {
            "spent": spent_year,
            "projected": _round_divide(spent_year * year_days, today.timetuple().tm_yday),
            "elapsed_days": today.timetuple().tm_yday,
            "total_days": year_days,
        },
    }


def _averages(connection, code: str, today: date, cover) -> dict | None:
    """最近 12 个完整月的月均，以及按它折出来的年均。

    窗口会被账本的第一笔截短：账本只有 3 个月，就拿 3 个月平均，而不是拿
    12 个月去除——后者会把月均凭空压到四分之一，屏幕上还是一个像模像样的数。

    ``cover`` 由调用方传进来：它已经为了别的字段取过一遍 coverage 了。
    重复取一次在十万行的账本上要多花上百毫秒，而这两个数不可能不同。
    """
    first_day = _parse(cover["first_day"])
    if first_day is None:
        return None

    last_month_start = _shift_month(today.replace(day=1), -1)
    window_end = _last_day(last_month_start.year, last_month_start.month)
    first_month = first_day.replace(day=1)
    if first_month > window_end:
        return None

    window_start = _shift_month(today.replace(day=1), -AVERAGE_MONTHS)
    if window_start < first_month:
        window_start = first_month
    months = (
        (last_month_start.year - window_start.year) * 12
        + last_month_start.month
        - window_start.month
        + 1
    )
    rows = repo.by_month(
        connection,
        limit=None,
        start=window_start.isoformat(),
        end=window_end.isoformat(),
    )
    total = sum(row["expense"] for row in rows if row["display_currency"] == code)
    return {
        "monthly": _round_divide(total, months),
        "yearly": _round_divide(total * 12, months),
        "months": months,
        "from": _month_text(window_start),
        "to": _month_text(last_month_start),
        "total": total,
    }


def _trend(connection, code: str, mode: str, start: date, end: date, today: date) -> dict:
    """区间内的支出趋势。当前还没过完的区间只画到**今天**。

    画到月底/年底会给出一段贴着零的线——那不是「后来没花钱」，是「还没到」。
    同样的零，在图上是同一种形状，人眼分不出来。
    """
    if mode == "all":
        bucket = "month"
    elif (end - start).days > MAX_DAY_BUCKETS:
        bucket = "month"
    else:
        bucket = "day"

    visible_end = min(end, today) if end > today else end
    if visible_end < start:
        visible_end = start

    rows = repo.trend_series(
        connection,
        bucket=bucket,
        start=start.isoformat(),
        end=visible_end.isoformat(),
    )
    found = {
        row["bucket"]: row
        for row in rows
        if row["display_currency"] == code
    }

    def amount(key: str, name: str) -> int:
        row = found.get(key)
        return int(row[name]) if row is not None else 0

    points: list[dict] = []
    if bucket == "day":
        cursor = start
        while cursor <= visible_end:
            key = cursor.isoformat()
            points.append(
                {"key": key, "expense": amount(key, "expense"), "income": amount(key, "income")}
            )
            cursor += timedelta(days=1)
    else:
        cursor = start.replace(day=1)
        while cursor <= visible_end:
            key = _month_text(cursor)
            points.append(
                {"key": key, "expense": amount(key, "expense"), "income": amount(key, "income")}
            )
            cursor = _shift_month(cursor, 1)

    total = sum(point["expense"] for point in points)
    income_total = sum(point["income"] for point in points)
    peak = max(points, key=lambda point: point["expense"]) if points else None
    return {
        "bucket": bucket,
        "points": points,
        "total": total,
        "income_total": income_total,
        "peak": peak if peak and peak["expense"] > 0 else None,
        "average": _round_divide(total, len(points)),
        "income_average": _round_divide(income_total, len(points)),
    }


def build(connection, conf, source) -> dict:
    """一个区间的全部概览数字。``source`` 是地址栏那些参数。

    返回的 ``singles`` 还是数据库行，由 ``api`` 那层投影成界面的形状——
    记录的身份字段（dedup_key / content_hash / raw_json）不该因为换了个
    接口就漏出去。
    """
    today = datetime.now(conf.tz()).date()
    span = params.summary_range(conf, source)
    mode = span["mode"]

    # 首页只认设置里的显示币种。以前还允许地址栏的 `currency` 覆盖，结果
    # 设置写着英镑、首页却在看人民币——「统一到设置里的币种」就名不副实了。
    code = conf.display_currency
    codes = repo.currencies(connection)

    cover = repo.coverage(connection)
    has_data = bool(cover["total"])
    first_day = _parse(cover["first_day"])
    last_day = _parse(cover["last_day"])
    fallback = last_day or today

    start = _parse(span["start"]) or first_day or today
    end = _parse(span["end"]) or last_day or today
    natural_end = _natural_end(mode, span, fallback)
    current = start <= today <= natural_end
    partial = current and natural_end > today
    can_next = (
        (mode == "month" and span["month"] < _month_text(today))
        or (mode == "year" and int(span["year"]) < today.year)
    )

    bounds = {"start": span["start"] or None, "end": span["end"] or None}
    overall_rows = repo.overall_totals(connection, **bounds)
    totals = _total_of(overall_rows, code)
    # 没配汇率的币种不会被折进显示币种，而是各占一行。把这几行如实报出来，
    # 界面才好说「N 笔还没配汇率」——静默吞掉和按 1:1 混算一样不可接受。
    # 笔数用同一份 overall_totals 的 total：口径、区间、合并净额都与合计一致。
    unconverted = [
        {"currency": row["display_currency"], "count": row["total"]}
        for row in overall_rows
        if row["display_currency"] != code and row["total"] > 0
    ]

    # 日均：只除到「已经过去的天」为止。拿整月天数去除当前这个半截月，
    # 日均会偏低，而它下面那个预测正是拿它外推的。
    elapsed_end = min(end, today) if end > today else end
    elapsed_days = max((elapsed_end - start).days + 1, 0)

    # 预测的两个数（本月 / 本年）用**同一套**合计口径，只换窗口。
    month_scope = repo.overall_totals(
        connection,
        start=today.replace(day=1).isoformat(),
        end=today.isoformat(),
    )
    year_scope = repo.overall_totals(
        connection,
        start=today.replace(month=1, day=1).isoformat(),
        end=today.isoformat(),
    )
    forecast_input = {
        **_empty_total(code),
        "expense_month": _total_of(month_scope, code)["expense"],
        "expense_year": _total_of(year_scope, code)["expense"],
    }

    previous = _previous_span(mode, span, today)
    previous_expense = None
    if previous:
        previous_expense = _total_of(
            repo.overall_totals(connection, start=previous["start"], end=previous["end"]),
            code,
        )["expense"]

    # 分类不设上限：界面上只露前 20 类，但「还有 N 类未列出」要说得准——截断在
    # 取数这一层，那个数就只能靠猜。
    category_rows = repo.by_source_category(connection, limit=None, **bounds)
    ranked_categories = [
        {
            "name": row["category"],
            "emoji": categories.emoji(row["category"]),
            "expense": row["expense"],
            "total": row["total"],
        }
        for row in category_rows
        if row["display_currency"] == code and row["expense"] > 0
    ]

    # 单次排行只要前 20 条，但总数另取——不然「还有 N 笔」是拿截断后的列表数
    # 出来的，大账本上会少报。
    ranked = [
        row
        for row in repo.expense_ranking(
            connection, per_currency=RANKING_LIMIT, **bounds
        )
        if row["display_currency"] == code
    ]
    ranked_counts = repo.expense_ranking_counts(connection, **bounds)

    if mode == "all" and first_day and last_day:
        trend_start, trend_end = first_day, last_day
    else:
        trend_start, trend_end = start, end

    return {
        "today": today.isoformat(),
        "has_data": has_data,
        "currency": code,
        "currencies": codes,
        "multi_currency": len(codes) > 1,
        "display_currency": conf.display_currency,
        "unconverted": unconverted,
        "unconverted_count": sum(item["count"] for item in unconverted),
        "range": {
            **span,
            "natural_end": natural_end.isoformat(),
            "partial": partial,
            "current": current,
            "can_prev": mode in ("month", "year"),
            "can_next": can_next,
        },
        "stats": {
            "expense": totals["expense"],
            "income": totals["income"],
            "net": totals["income"] - totals["expense"],
            "total": totals["total"],
            "neutral_count": totals["neutral_count"],
            "large_income": totals["large_income"],
            "large_expense": totals["large_expense"],
            "large_count": totals["large_count"],
            "elapsed_days": elapsed_days,
            "daily_average": _round_divide(totals["expense"], elapsed_days),
        },
        "previous": (
            {**previous, "expense": previous_expense}
            if previous and previous_expense is not None
            else None
        ),
        "forecast": _forecast(forecast_input, today),
        "averages": _averages(connection, code, today, cover),
        "trend": _trend(connection, code, mode, trend_start, trend_end, today),
        "categories": ranked_categories,
        "singles": ranked,
        "single_count": ranked_counts.get(code, 0),
        "category_count": len(ranked_categories),
    }
