"""去重键。

「重复导入同一份账单会不会把账翻倍」全看这里，所以规则写得比别处啰嗦。

分两层：

1. **订单号**。profile 指了订单号列、且这格有值，就用它。支付宝的
   ``交易订单号``、微信的 ``交易单号``都是全平台唯一的，重复导出同一笔仍是同号。
2. **内容哈希**。没有订单号时（多数银行流水），对时间、方向、金额和几项
   描述文字取 sha256。

关键的一条修正：光看 key 相同就判重复会**误杀**真交易——同一分钟里两笔
一模一样的三块钱消费，或者退款行复用了原订单号，都会被静默吞掉。所以
「同 key 但内容不同」以及「同一份文件里 key 重复出现」都改判为**不同的交易**，
给它们加上 ``#2``、``#3`` 序号。只有 key 和内容都对得上，才算真的重复。
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Collection, Mapping

from .models import Transaction

__all__ = ["DEFAULT_ID_NULLS", "HASH_FIELDS", "assign", "base_key", "content_hash"]

_SEP = "\x1f"

# 订单号列里出现这些值等于「没有订单号」。
DEFAULT_ID_NULLS = frozenset(
    {"", "/", "\\", "--", "-", "0", "nan", "none", "null", "无", "不适用"}
)

# 参与内容哈希的文字字段。
#
# 只放**重新导出时不会变**的字段。摘要是这里最容易踩的坑：不少银行每次导出
# 都会把日期、网点、流水备注之类的拼进摘要（「消费」变成「消费 某超市 2026-01-10」），
# 一旦把摘要算进指纹，同一笔钱在区间重叠的两次导出里就成了两笔，长期记账会
# 越滚越多。备注同理，它本来就是给人改的。
#
# 代价是「同一天、同一方向、同一金额、同一对方」的几笔会落到同一个指纹上——
# 这由 assign() 的序号机制兜住（#2、#3），不会互相吞掉。
HASH_FIELDS = (
    "counterparty",
    "source_txn_id",
    "merchant_txn_id",
)


def content_hash(txn: Transaction) -> str:
    """这一行的内容指纹，不带序号。"""
    parts = [txn.occurred_at, str(txn.direction), str(txn.amount_cents)]
    parts.extend(getattr(txn, name) or "" for name in HASH_FIELDS)
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()[:32]


def base_key(
    txn: Transaction,
    *,
    id_value: str = "",
    id_nulls: Collection[str] = DEFAULT_ID_NULLS,
) -> str:
    """还没加序号之前的去重键。"""
    natural = (id_value or "").strip()
    if natural:
        lowered = natural.lower()
        if lowered not in _lowered(id_nulls):
            return f"id:{natural}"
    return f"h:{content_hash(txn)}"


def assign(
    pairs: list[tuple[str, str]],
    *,
    existing: Mapping[str, str],
) -> list[tuple[str, bool]]:
    """给一批行分配最终的去重键，并判断哪些是和库里重复的。

    ``pairs`` 是 ``[(base_key, content_hash)]``，顺序就是文件里的顺序。
    ``existing`` 是库里已有的 ``{dedup_key: content_hash}``。

    返回 ``[(最终 key, 是否与库里重复)]``。

    序号分配只看文件内部：同一份文件里 base_key 第 n 次出现就得到 ``#n``。
    因为导出顺序是稳定的，重传同一份文件算出来的序号也稳定，
    所以第二次导入时每个 key 都能在库里原样找到。
    """
    used: Counter[str] = Counter()
    assigned: list[tuple[str, bool]] = []

    for base, digest in pairs:
        seen = used[base]
        used[base] += 1
        key = base if seen == 0 else f"{base}#{seen + 1}"

        # 只有 key 和内容都对得上才算重复。
        stored = existing.get(key)
        assigned.append((key, stored is not None and stored == digest))

    return assigned


def _lowered(values: Collection[str]) -> frozenset[str]:
    return frozenset(value.lower() for value in values)
