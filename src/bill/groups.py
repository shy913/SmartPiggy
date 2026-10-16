"""AA / 拼单的自动检测。

纯函数：只做判断，不碰数据库、不读设置。金额由调用方**换算到显示币种**再传进来，
所以这里能直接比大小，跨币种的 AA（购物用英镑卡、朋友从微信还钱）也能成立。

规则一句话：**一笔支出，加上它之后一小段时间里的若干笔等额转入，每个人头分到的
份额跟每笔转入都对得上，就是一次 AA。**

算例：三个人吃饭，你垫了 £120，两位朋友各转 £40 给你。
净额 £40 才是你真实的消费——账本里那三个数单独看都对，合起来是错的。

几条刻意的取舍：

- **转入不能早于支出**，而且就在同一天左右。别人还钱总是发生在这笔消费之后；
  放宽到「前后都算」会把先垫钱后消费的两个人混成一组。同一时刻是允许的——
  银行流水大多只有日期，同一天的几笔时间戳本来就一模一样。
- **中性行不参与**。它本来就不计收支，并进来改变不了净额，只会让人以为算错了。
- **容差是绝对额**（默认 ±0.5），不是百分比：朋友按汇率或者四舍五入多转一点很常见，
  而按比例算的话，小额支出会宽松到把不相干的两笔凑上。
- **宁可漏也不误合**。漏掉的结果是「没合并」，你看得见，手动勾一下就完事；
  误合的结果是统计被安静地改错。所以除了等额数学，还要求转入**看起来像还钱**
  （见 keywords）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["MAX_SPLIT", "Candidate", "Row", "candidates"]

# 最多算到几个人分。再多人一起摊的账，等额关系本来也难凑巧成立。
MAX_SPLIT = 10

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Row:
    """检测用的一行。只带判断需要的字段，不是账本里那条记录。"""

    id: int
    occurred_at: str  # 'YYYY-MM-DD HH:MM:SS'
    direction: str  # in | out | neutral
    # 换算到**显示币种**的金额（分），恒为正。换算在 repo 里做。
    display_cents: int
    # 判断「像不像别人还你钱」的文字：对方 + 说明 + 分类。
    text: str = ""
    # 已经属于某个合并组了，不再参与这一轮。
    grouped: bool = False


@dataclass(frozen=True)
class Candidate:
    """检测出来的一次 AA。还没落库——建不建组由调用方定。"""

    anchor_id: int
    member_ids: tuple[int, ...]  # 那几笔转入，按时间先后
    deviation: int  # 每笔转入与人均份额的差额之和（分），越小越像
    anchor_at: str = ""
    total_cents: int = 0  # 支出金额（显示币种分）

    @property
    def ids(self) -> tuple[int, ...]:
        return (self.anchor_id, *self.member_ids)

    @property
    def size(self) -> int:
        """一共几个人分。两人 AA 是 2。"""
        return len(self.member_ids) + 1


def candidates(
    rows: Iterable[Row],
    *,
    window_hours: float = 12.0,
    tolerance_cents: int = 50,
    keywords: Sequence[str] = (),
    max_split: int = MAX_SPLIT,
) -> list[Candidate]:
    """找出所有像 AA 的组合，按时间先后返回。

    ``keywords`` 非空时，转入的文字要命中其中之一才参与——这道过滤挡的是形状巧合
    （薪水入账和一笔两倍于它的消费恰好构成「两人 AA」的形状）。传空序列就只看数学。
    """
    usable = [row for row in rows if _usable(row)]
    expenses = [row for row in usable if row.direction == "out"]
    incomes = [row for row in usable if row.direction == "in"]

    parsed: dict[int, datetime] = {}
    for row in usable:
        moment = _parse(row.occurred_at)
        if moment is not None:
            parsed[row.id] = moment
    expenses = [row for row in expenses if row.id in parsed]
    incomes = [row for row in incomes if row.id in parsed]

    loud = _matches_keyword(keywords)
    pool = [row for row in incomes if loud(row.text)]

    found: list[Candidate] = []
    for expense in sorted(expenses, key=lambda row: (parsed[row.id], row.id)):
        best = _best_split(
            expense,
            at=parsed[expense.id],
            pool=pool,
            parsed=parsed,
            window=timedelta(hours=window_hours),
            tolerance_cents=tolerance_cents,
            max_split=max_split,
        )
        if best is not None:
            found.append(best)

    return _claim(found, parsed)


def _best_split(
    expense: Row,
    *,
    at: datetime,
    pool: Sequence[Row],
    parsed: dict[int, datetime],
    window: timedelta,
    tolerance_cents: int,
    max_split: int,
) -> Candidate | None:
    """给这一笔支出找最合适的人数。找不到返回 None。

    人头数从 1 试到 ``max_split``：转几笔就暗示着几个人分（你 + 转来的那几笔）。
    取**误差总和最小**的那一个人头数；误差一样时取人多的，因为「三个人各转一份」
    比「一个人转了一份」更像一次 AA。
    """
    if expense.display_cents <= 0:
        return None

    # 「不早于」而不是「严格晚于」：不少银行流水只有日期，同一天的几笔时间戳
    # 一模一样，按严格晚于算的话，当天的还款永远也匹配不上。
    nearby = [row for row in pool if at <= parsed[row.id] <= at + window]
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
        if max(deviations) > tolerance_cents:
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
    """
    ranked = sorted(
        found,
        key=lambda candidate: (
            candidate.deviation,
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


def _parse(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, _TIME_FORMAT)
    except (TypeError, ValueError):
        return None
