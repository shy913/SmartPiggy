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
- **容差是绝对额**（默认 ±0.5），不是百分比：朋友按汇率或者四舍五入多转一点很常见，
  而按比例算的话，小额支出会宽松到把不相干的两笔凑上。
- **宁可漏也不误合**。漏掉的结果是「没合并」，你看得见，手动勾一下就完事；
  误合的结果是统计被安静地改错。所以除了等额数学，还要求转入**看起来像还钱**
  （见 keywords）。
- **收支平衡只认「一笔对一笔」**。几笔收款凑成一笔支出的那种形状（−£120 后面
  跟着三个 +£40）跟「四个人吃饭、三个朋友各还一份」在数学上完全分不开，按平衡
  判会得到 0、按 AA 判会得到 £30，两个都不算错——这种情况交给手动合并，别猜。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = [
    "MAX_SPLIT",
    "Candidate",
    "Row",
    "balanced",
    "candidates",
    "detect",
]

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


def balanced(
    rows: Iterable[Row],
    *,
    window_hours: float = 12.0,
    tolerance_cents: int = 50,
    keywords: Sequence[str] = (),
) -> list[Candidate]:
    """找出所有「收支平衡」的组合：一笔支出 + 之后一笔跟它等额的收入。

    跟 AA 共用窗口、容差和「收款得像钱回来了」那道过滤（见 keywords），只有形状
    不同：AA 是几个人各摊一份，这里是一笔全额抵掉。只有一笔对一笔——
    几笔凑成一笔支出的那种，跟几个人平摊在数学上分不开，交给手动合并。
    """
    usable = [row for row in rows if _usable(row)]
    parsed: dict[int, datetime] = {}
    for row in usable:
        moment = _parse(row.occurred_at)
        if moment is not None:
            parsed[row.id] = moment

    expenses = [row for row in usable if row.direction == "out" and row.id in parsed]
    loud = _matches_keyword(keywords)
    pool = [
        row
        for row in usable
        if row.direction == "in" and row.id in parsed and loud(row.text)
    ]

    window = timedelta(hours=window_hours)
    found: list[Candidate] = []
    for expense in sorted(expenses, key=lambda row: (parsed[row.id], row.id)):
        at = parsed[expense.id]
        # 「不早于」而不是「严格晚于」，跟 AA 一样：银行流水大多只有日期，
        # 同一天的几笔时间戳本来就一模一样。
        nearby = [row for row in pool if at <= parsed[row.id] <= at + window]
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
        if deviation > tolerance_cents:
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
    found = candidates(
        rows,
        window_hours=window_hours,
        tolerance_cents=tolerance_cents,
        keywords=keywords,
        max_split=max_split,
    )
    if balanced_merge:
        found += balanced(
            rows,
            window_hours=window_hours,
            tolerance_cents=tolerance_cents,
            keywords=keywords,
        )

    parsed: dict[int, datetime] = {}
    for row in rows:
        moment = _parse(row.occurred_at)
        if moment is not None:
            parsed[row.id] = moment
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


def _parse(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, _TIME_FORMAT)
    except (TypeError, ValueError):
        return None
