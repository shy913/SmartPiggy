"""AA / 拼单的自动检测。

纯函数：只做判断，不碰数据库、不读设置。金额由调用方**换算到显示币种**再传进来，
所以这里能直接比大小，跨币种的 AA（购物用英镑卡、朋友从微信还钱）也能成立。

两条规则，判的都是「一笔支出 + 它之后一小段时间里的收款」这一个形状里的上下两半：

1. **AA**：一笔支出，加上之后若干笔等额转入，每个人头分到的份额跟每笔转入都对得上。
   算例：三个人吃饭，你垫了 £120，两位朋友各转 £40 给你。净额 £40 才是你真实的
   消费——账本里那三个数单独看都对，合起来是错的。
2. **收支平衡**：一笔支出，之后**一笔**跟它等额的收入。算例：−£4.49 之后五小时
   +£4.49。钱原封不动回来了（退款、转出去又转回来、自己两个账户之间倒一下），
   真实收支是 0——账本里却是「支出 4.49 + 收入 4.49」，两头虚高。

两条的净额不会撞车：AA 的净额恒等于你自己那一份（小于支出），收支平衡的净额恒为 0。
形状上也是分开的——「一笔全额回来」和「几个人各摊一份」在数学上是两类。

几条刻意的取舍：

- **转入不能早于支出**，而且就在同一天左右。别人还钱总是发生在这笔消费之后；
  放宽到「前后都算」会把先垫钱后消费的两个人混成一组。同一时刻是允许的——
  银行流水大多只有日期，同一天的几笔时间戳本来就一模一样。
- **中性行不参与**。它本来就不计收支，并进来改变不了净额，只会让人以为算错了。
- **容差以绝对额为主，小份额再压一道比例上限**（默认 ±0.5 且不超过份额的 25%）：
  朋友按汇率或者四舍五入多转一点很常见，绝对额在大额上最稳；但金额很小时
  ±0.5 可能超过份额本身（£1.12 分成两人，份额只有 £0.56），这时候比例上限
  才挡得住把不相干的两笔凑成一组。
- **宁可漏也不误合**。漏掉的结果是「没合并」，你看得见，手动勾一下就完事；
  误合的结果是统计被安静地改错。所以除了等额数学，还要求转入**看起来像还钱**
  （见 keywords）。
- **收支平衡只认「一笔对一笔」**。几笔收款凑成一笔支出的那种形状（−£120 后面
  跟着三个 +£40）跟「四个人吃饭、三个朋友各还一份」在数学上完全分不开，按平衡
  判会得到 0、按 AA 判会得到 £30，两个都不算错——这种情况交给手动合并，别猜。
"""

from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = [
    "MAX_DEVIATION_RATIO",
    "MAX_SPLIT",
    "ROLLUP_EXCLUDE_KEYWORDS",
    "Candidate",
    "Rollup",
    "RollupRow",
    "Row",
    "balanced",
    "candidates",
    "detect",
    "rollup_key",
    "rollups",
]

# 最多算到几个人分。再多人一起摊的账，等额关系本来也难凑巧成立。
MAX_SPLIT = 10

# 每笔偏差的上限还要再乘上这个比例，跟绝对容差取小。
#
# 绝对容差在小额上会失控：一笔 £1.12 的支出分成两人，份额只有 £0.56，而
# ±£0.5 相当于份额的九成——一笔 £0.12 的收款也能凑成「两人 AA」。大额则相反，
# 一份 £40 的份额，25% 是 £10，远大于绝对容差，这个比例等于不起作用。
# 两头各管一段，正是「按消费大小」该有的样子。
MAX_DEVIATION_RATIO = 0.25

@dataclass(frozen=True)
class Row:
    """检测用的一行。只带判断需要的字段，不是账本里那条记录。"""

    id: int
    occurred_at: str  # 'YYYY-MM-DD HH:MM:SS'
    direction: str  # in | out | neutral
    # 换算到**显示币种**的金额（分），恒为正。换算在 repo 里做。
    display_cents: int
    # 判断「像不像别人还你钱」的文字：对方 + 说明 + 分类 + 交易类型。
    # 交易类型只有微信账单有（群收款、转账、红包），它不是分类，但比分类更
    # 直接地说明「这是钱回来了」。
    text: str = ""
    # 已经属于某个合并组了，不再参与这一轮。
    grouped: bool = False


@dataclass(frozen=True)
class Candidate:
    """检测出来的一组。还没落库——建不建组由调用方定。"""

    anchor_id: int
    member_ids: tuple[int, ...]  # 那几笔转入，按时间先后
    deviation: int  # 转入跟它应该对上的数差多少（分），越小越像
    anchor_at: str = ""
    total_cents: int = 0  # 支出金额（显示币种分）
    # 哪条规则判出来的："aa" 还是 "balance"。落库和展示都用不着它（两者的净额
    # 由金额本身决定），留着是为了让报告和用例能说清「这一组是怎么来的」。
    kind: str = "aa"

    @property
    def ids(self) -> tuple[int, ...]:
        return (self.anchor_id, *self.member_ids)

    @property
    def size(self) -> int:
        """一共几个人分。两人 AA 是 2。

        只对 AA 说得通。收支平衡那一组是「一笔支出 + 一笔收款」，这里会得到 2，
        但那是跟 AA 的两人形同、数字相同的巧合，不是「两个人分」的意思。
        """
        return len(self.member_ids) + 1


# 无论用户把关键词放宽成什么，说明里出现这几个词的记录都不参与月度汇总。
# 「余额宝-自动转入」跟「收益发放」在同一个交易对方名下，只按对方名或只按
# 宽泛的关键词去合，会把几千块的转入当成利息收入——屏幕上完全看不出来。
ROLLUP_EXCLUDE_KEYWORDS = ("自动转入",)


@dataclass(frozen=True)
class RollupRow:
    """月度汇总的候选行。只带分组需要的字段。"""

    id: int
    occurred_date: str  # 'YYYY-MM-DD'，已经是显示时区的日期
    account_id: int
    currency: str
    counterparty: str
    description: str
    grouped: bool = False


@dataclass(frozen=True)
class Rollup:
    """一个「同账户 + 同对方 + 同月 + 同币种」的收益桶。"""

    key: str
    title: str
    month: str
    anchor_id: int
    member_ids: tuple[int, ...]

    @property
    def ids(self) -> tuple[int, ...]:
        return (self.anchor_id, *self.member_ids)


def rollup_key(account_id: int, counterparty: str, month: str, currency: str) -> str:
    """收益组的稳定身份。落库、查重和 veto 都用它。"""
    return json.dumps(
        [
            int(account_id),
            (counterparty or "").strip().casefold(),
            month,
            (currency or "").upper(),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _meaningful_name(text: str) -> bool:
    """对方名是不是真有个名字。

    银行流水里不少记录把「对方」那一列填成一串横线（``-------------------``）。
    拿它当组标题会得到「------------------- · 2026-09」，所以这种就退回用命中的
    关键词（「结息」）当标签。
    """
    return any(char.isalnum() for char in (text or ""))


def rollups(rows: Iterable[RollupRow], *, keywords: Sequence[str] = ()) -> list[Rollup]:
    """把说明命中关键词的利息按自然月分桶。

    中性收益和已经记成收入的利息（银行「结息」常见）都走这里；方向由调用方的
    SQL 先筛好。**不设「至少两笔」的门槛**：调用方需要看到只有一笔的桶，才能把
    它们追加到已经存在的同月组里。要不要建新组由调用方决定。

    ``keywords`` 为空时一条都不匹配——月度汇总不是猜出来的，必须由设置里那几个
    词明确点名。分组按账户、对方（没有像样的对方名时用关键词）、月份、币种四项，
    跨账户/跨币种不混。
    """
    wanted = tuple(
        (str(word).strip(), str(word).strip().lower())
        for word in keywords
        if str(word).strip()
    )
    if not wanted:
        return []

    buckets: dict[str, tuple[str, list[RollupRow]]] = {}
    for row in rows:
        if row.grouped:
            continue
        month = (row.occurred_date or "")[:7]
        if len(month) != 7:
            continue
        text = (row.description or "").lower()
        matched = next((word for word, lowered in wanted if lowered in text), None)
        if matched is None:
            continue
        if any(word in text for word in ROLLUP_EXCLUDE_KEYWORDS):
            continue
        name = (row.counterparty or "").strip()
        label = name if _meaningful_name(name) else matched
        key = rollup_key(row.account_id, label, month, row.currency)
        bucket = buckets.setdefault(key, (label, []))
        bucket[1].append(row)

    found: list[Rollup] = []
    for key, (label, members) in buckets.items():
        members.sort(key=lambda row: (row.occurred_date, row.id))
        first = members[0]
        month = first.occurred_date[:7]
        found.append(
            Rollup(
                key=key,
                title=f"{label} · {month}",
                month=month,
                anchor_id=first.id,
                member_ids=tuple(row.id for row in members[1:]),
            )
        )
    found.sort(key=lambda item: (item.month, item.anchor_id))
    return found


def candidates(
    rows: Iterable[Row],
    *,
    window_hours: float = 12.0,
    tolerance_cents: int = 50,
    keywords: Sequence[str] = (),
    max_split: int = MAX_SPLIT,
    parsed: dict[int, datetime] | None = None,
) -> list[Candidate]:
    """找出所有像 AA 的组合，按时间先后返回。

    ``keywords`` 非空时，转入的文字要命中其中之一才参与——这道过滤挡的是形状巧合
    （薪水入账和一笔两倍于它的消费恰好构成「两人 AA」的形状）。传空序列就只看数学。

    ``parsed`` 是调用方已经算好的 ``{id: 时间}``。``detect`` 两条规则一起跑时传进来，
    省得同一批时间戳解析三遍。不传就自己算。
    """
    usable = [row for row in rows if _usable(row)]
    expenses = [row for row in usable if row.direction == "out"]
    incomes = [row for row in usable if row.direction == "in"]

    if parsed is None:
        parsed = _parsed(usable)
    expenses = [row for row in expenses if row.id in parsed]
    incomes = [row for row in incomes if row.id in parsed]

    loud = _matches_keyword(keywords)
    pool = [row for row in incomes if loud(row.text)]
    # 按时间排好，窗口就能用二分切出来。逐笔扫全池在十万行账本上是 O(支出×收入)
    # ——全库扫描要一分钟。窗口内的候选本来只有几条，排一次序是值得的。
    pool.sort(key=lambda row: (parsed[row.id], row.id))
    pool_times = [parsed[row.id] for row in pool]

    window = timedelta(hours=window_hours)
    found: list[Candidate] = []
    for expense in sorted(expenses, key=lambda row: (parsed[row.id], row.id)):
        at = parsed[expense.id]
        nearby = pool[
            bisect_left(pool_times, at) : bisect_right(pool_times, at + window)
        ]
        best = _best_split(
            expense,
            at=at,
            pool=nearby,
            parsed=parsed,
            tolerance_cents=tolerance_cents,
            max_split=max_split,
        )
        if best is not None:
            found.append(best)

    return _claim(found, parsed)


def balanced(
    rows: Iterable[Row],
    *,
    window_hours: float = 12.0,
    tolerance_cents: int = 50,
    keywords: Sequence[str] = (),
    parsed: dict[int, datetime] | None = None,
) -> list[Candidate]:
    """找出所有「收支平衡」的组合：一笔支出 + 之后一笔跟它等额的收入。

    跟 AA 共用窗口、容差和「收款得像钱回来了」那道过滤（见 keywords），只有形状
    不同：AA 是几个人各摊一份，这里是一笔全额抵掉。只有一笔对一笔——
    几笔凑成一笔支出的那种，跟几个人平摊在数学上分不开，交给手动合并。
    """
    usable = [row for row in rows if _usable(row)]
    if parsed is None:
        parsed = _parsed(usable)

    expenses = [row for row in usable if row.direction == "out" and row.id in parsed]
    loud = _matches_keyword(keywords)
    pool = [
        row
        for row in usable
        if row.direction == "in" and row.id in parsed and loud(row.text)
    ]
    pool.sort(key=lambda row: (parsed[row.id], row.id))
    pool_times = [parsed[row.id] for row in pool]

    window = timedelta(hours=window_hours)
    found: list[Candidate] = []
    for expense in sorted(expenses, key=lambda row: (parsed[row.id], row.id)):
        at = parsed[expense.id]
        # 「不早于」而不是「严格晚于」，跟 AA 一样：银行流水大多只有日期，
        # 同一天的几笔时间戳本来就一模一样。
        nearby = pool[
            bisect_left(pool_times, at) : bisect_right(pool_times, at + window)
        ]
        if not nearby:
            continue
        # 差得最少的那个收款就是它在等的钱。都不够近就是没有。
        mate = min(
            nearby,
            key=lambda row: (
                abs(row.display_cents - expense.display_cents),
                parsed[row.id],
                row.id,
            ),
        )
        deviation = abs(mate.display_cents - expense.display_cents)
        # 跟 AA 一样：小额时绝对容差会显得极宽，比例上限兜底。
        allowed = min(
            tolerance_cents,
            int(expense.display_cents * MAX_DEVIATION_RATIO),
        )
        if deviation > allowed:
            continue
        found.append(
            Candidate(
                anchor_id=expense.id,
                member_ids=(mate.id,),
                deviation=deviation,
                anchor_at=expense.occurred_at,
                total_cents=expense.display_cents,
                kind="balance",
            )
        )

    return _claim(found, parsed)


def detect(
    rows: Iterable[Row],
    *,
    window_hours: float = 12.0,
    tolerance_cents: int = 50,
    keywords: Sequence[str] = (),
    max_split: int = MAX_SPLIT,
    balanced_merge: bool = True,
) -> list[Candidate]:
    """两条规则一起判，冲突一起解，返回这一轮该建的所有组。

    **必须一起解**：一笔收款同时被收支平衡和某次 AA 看中时，各判各的会各自都
    以为自己拿得到——落库时后一组才撞上「一笔只能属于一个组」的唯一约束，
    被静默跳过，于是这一轮少建一组、下一轮扫描又补上，账本在两次刷新之间会变。
    """
    rows = list(rows)
    parsed = _parsed(row for row in rows if _usable(row))
    found = candidates(
        rows,
        window_hours=window_hours,
        tolerance_cents=tolerance_cents,
        keywords=keywords,
        max_split=max_split,
        parsed=parsed,
    )
    if balanced_merge:
        found += balanced(
            rows,
            window_hours=window_hours,
            tolerance_cents=tolerance_cents,
            keywords=keywords,
            parsed=parsed,
        )

    return _claim(found, parsed)


def _best_split(
    expense: Row,
    *,
    at: datetime,
    pool: Sequence[Row],
    parsed: dict[int, datetime],
    tolerance_cents: int,
    max_split: int,
) -> Candidate | None:
    """给这一笔支出找最合适的人数。找不到返回 None。

    ``pool`` 已经按时间排好、且只含 ``at`` 到 ``at + 窗口`` 这一段（调用方用二分
    切出来的）。这里只做金额上的挑选。

    人头数从 1 试到 ``max_split``：转几笔就暗示着几个人分（你 + 转来的那几笔）。
    取**误差总和最小**的那一个人头数；误差一样时取人多的，因为「三个人各转一份」
    比「一个人转了一份」更像一次 AA。
    """
    if expense.display_cents <= 0:
        return None

    # 「不早于」而不是「严格晚于」：不少银行流水只有日期，同一天的几笔时间戳
    # 一模一样，按严格晚于算的话，当天的还款永远也匹配不上。
    nearby = pool
    if not nearby:
        return None

    best: Candidate | None = None
    for size in range(2, max_split + 1):
        share = expense.display_cents / size
        # 每笔与份额的差额：挑差额最小的那 size-1 笔，就一定是最优解
        # （目标函数就是差额之和，且彼此独立）。
        ranked = sorted(
            nearby,
            key=lambda row: (abs(row.display_cents - share), parsed[row.id], row.id),
        )
        chosen = ranked[: size - 1]
        if len(chosen) < size - 1:
            continue
        deviations = [abs(row.display_cents - share) for row in chosen]
        # 绝对容差管大额，比例上限管小额（见 MAX_DEVIATION_RATIO）。
        allowed = min(tolerance_cents, int(share * MAX_DEVIATION_RATIO))
        if max(deviations) > allowed:
            continue

        candidate = Candidate(
            anchor_id=expense.id,
            member_ids=tuple(
                row.id for row in sorted(chosen, key=lambda row: parsed[row.id])
            ),
            deviation=int(round(sum(deviations))),
            anchor_at=expense.occurred_at,
            total_cents=expense.display_cents,
        )
        if best is None or (candidate.deviation, -candidate.size) < (
            best.deviation,
            -best.size,
        ):
            best = candidate

    return best


def _claim(found: Sequence[Candidate], parsed: dict[int, datetime]) -> list[Candidate]:
    """一次转入只能属于一个组：冲突时让「更像」的那一组先拿。

    不像就抢不起来：两笔支出都想要同一笔转入时，误差小的那组先拿。剩下的那组
    要么退而找别的转入，要么这轮就不成组——重扫一遍结果也一样，不会今天合这个、
    明天合那个。

    误差一样时**收支平衡先拿**：一笔收款跟支出分毫不差，比「它正好是某笔支出的
    一半」更像一次钱绕了一圈回来（两者能同时成立，例如 −£80 和 −£40 都在窗口里，
    中间那笔 +£40 两边都要）。这个键只对两种规则同时在场时起作用，单跑 AA
    或者单跑收支平衡都不受影响。
    """
    ranked = sorted(
        found,
        key=lambda candidate: (
            candidate.deviation,
            0 if candidate.kind == "balance" else 1,
            -candidate.total_cents,
            parsed.get(candidate.anchor_id, datetime.min),
            candidate.anchor_id,
        ),
    )
    taken: set[int] = set()
    claimed: list[Candidate] = []
    for candidate in ranked:
        if taken.intersection(candidate.ids):
            continue
        taken.update(candidate.ids)
        claimed.append(candidate)

    claimed.sort(key=lambda candidate: (parsed.get(candidate.anchor_id), candidate.anchor_id))
    return claimed


def _matches_keyword(keywords: Sequence[str]):
    """文字里有没有「还钱」的意思。keywords 为空则一律放行。"""
    wanted = tuple(word.lower() for word in keywords if word)

    def look(text: str) -> bool:
        if not wanted:
            return True
        lowered = (text or "").lower()
        return any(word in lowered for word in wanted)

    return look


def _usable(row: Row) -> bool:
    return (
        not row.grouped
        and row.direction in ("in", "out")
        and row.display_cents > 0
    )


def _parsed(rows: Iterable[Row]) -> dict[int, datetime]:
    """把能用的一批行的发生时间解析出来，一次算好、多处共用。

    ``datetime.fromisoformat`` 比 ``strptime`` 快一个数量级，而库里那列本来就是
    定长的 ``YYYY-MM-DD HH:MM:SS``。格式不对的行照旧跳过（不进 ``parsed``），
    跟以前「解析失败就不参与」的行为一致。
    """
    parsed: dict[int, datetime] = {}
    for row in rows:
        moment = _parse(row.occurred_at)
        if moment is not None:
            parsed[row.id] = moment
    return parsed


def _parse(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
