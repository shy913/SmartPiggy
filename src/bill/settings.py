"""本地设置：汇率换算、时区、AA 合并。

跟列映射一样走 TOML：内置一份默认值，用户目录里放一份同名文件就整体覆盖它。
读取时带上文件的修改时间做缓存键，所以改了文件下次请求就生效，不用重启。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

from . import config, dates
from .errors import BillError

__all__ = ["Settings", "is_currency_code", "load"]

DEFAULT_DISPLAY = "GBP"
DEFAULT_TIMEZONE = "Europe/London"
DEFAULT_WINDOW_HOURS = 12.0
DEFAULT_TOLERANCE = 0.5
# 「转入得像别人还你钱」的关键词。跟 data/settings.toml 里那份必须一致：
# 覆盖了内置文件的用户（自己那份没写 [merge] 段）拿到的是这里的值，
# 两处不一致就会出现「改了内置文件、行为却没变」这种最难查的毛病。
# 有测试盯着这件事。
DEFAULT_KEYWORDS = (
    "转账",
    "红包",
    "收款",
    "还款",
    "代付",
    "AA",
    "拼单",
    "分账",
    "transfer",
    "repay",
    "split",
)
_CODE = re.compile(r"^[A-Z0-9]{1,8}$")


@dataclass(frozen=True)
class Settings:
    display_currency: str = DEFAULT_DISPLAY
    # 「1 单位显示币种 = 多少该币种」，例如 {"CNY": 8.9} 表示 1 GBP = 8.9 CNY。
    rates: dict[str, float] = field(default_factory=dict)
    # 所有时间都换算成这个时区显示（IANA 名字）。
    timezone: str = DEFAULT_TIMEZONE
    # 导入后自动检测并合并 AA。
    merge_enabled: bool = True
    # 收款落在支出之后多少小时内算同一次。
    merge_window_hours: float = DEFAULT_WINDOW_HOURS
    # 每笔收款与「人均份额」允许差多少（显示币种的单位，不是分）。
    merge_tolerance: float = DEFAULT_TOLERANCE
    # 转入要像「别人还你钱」才参与自动合并；空元组 = 不做这层过滤。
    merge_keywords: tuple[str, ...] = DEFAULT_KEYWORDS

    def tz(self):
        return dates.zone(self.timezone)

    def tolerance_cents(self) -> int:
        """容差换成整数分，跟账本里其他金额一个单位。"""
        return int(round(self.merge_tolerance * 100))

    def factors(self) -> dict[str, float]:
        """币种 -> 乘数。原金额乘上它就是显示币种的最小单位数。

        显示币种本身不在表里（乘 1），没配汇率的币种也不在里面——
        调用方要保证它们不会被按 1:1 混进合计。
        """
        factors: dict[str, float] = {}
        for code, rate in self.rates.items():
            clean = str(code).upper()
            if not is_currency_code(clean):
                raise BillError(f"汇率里的币种代码「{code}」不像个币种代码。")
            if rate <= 0:
                raise BillError(f"{clean} 的汇率必须大于 0，现在是 {rate}。")
            if clean != self.display_currency:
                factors[clean] = 1.0 / float(rate)
        return factors

    def describe(self) -> str:
        """给界面用的一句话，让换算规则是明摆着的。"""
        if self.rates:
            pairs = "、".join(
                f"1 {self.display_currency} = {rate:g} {code}"
                for code, rate in sorted(self.rates.items())
            )
            money = f"金额已按 {pairs} 换算为 {self.display_currency}"
        else:
            money = f"金额以 {self.display_currency} 计"
        return f"{money}；时间均已换算到 {self.timezone}。"

    def describe_merge(self) -> str:
        """自动合并现在按什么规则跑。合并结果不对时，先看这一句。"""
        if not self.merge_enabled:
            return "导入后不再自动合并，只有你手动勾选的才算。"
        if self.merge_keywords:
            scope = f"转入需命中「{'、'.join(self.merge_keywords)}」之一"
        else:
            scope = "任何转入都参与"
        return (
            f"导入后自动合并：支出之后 {self.merge_window_hours:g} 小时内的等额转入，"
            f"每笔与人均份额相差不超过 {self.merge_tolerance:g} "
            f"{self.display_currency}；{scope}。"
        )


def is_currency_code(value: str) -> bool:
    """像不像个币种代码（``CNY``、``GBP``）。

    设置里的 ``display`` 和手动记账表单里的币种共用这一套判断——两处各写一份
    正则的话，早晚会有一处松一格。
    """
    return bool(_CODE.match((value or "").strip().upper()))


def load() -> Settings:
    """读设置。用户文件存在就整个覆盖内置那份。

    **刻意不缓存。** 这个文件不到 1 KB，一次请求读几遍的开销可以忽略；而按
    修改时间做缓存键，在文件系统时间戳精度不够（或者编辑器做原子改名）时会
    漏掉改动，表现成「汇率改了没生效」这种很难查的毛病。为了省这点开销不值得。
    """
    for path in (config.user_settings_path(), config.builtin_settings_path()):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        try:
            parsed = tomllib.loads(data.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
            raise BillError(f"{path.name} 读不了：{error}")

        section = parsed.get("currency", {})
        display = str(section.get("display") or DEFAULT_DISPLAY).upper()
        if not is_currency_code(display):
            raise BillError(f"settings 里的 display 币种「{display}」不像个币种代码。")
        rates = {
            str(code).upper(): float(rate)
            for code, rate in (section.get("rates") or {}).items()
        }

        timezone = str(parsed.get("timezone") or DEFAULT_TIMEZONE)
        dates.zone(timezone)  # 名字写错就当场报，别等导入时才发现

        merge = parsed.get("merge", {})
        window = float(merge.get("window_hours", DEFAULT_WINDOW_HOURS))
        if window <= 0:
            raise BillError(f"merge 的窗口必须是正数小时，现在是 {window:g}。")
        tolerance = float(merge.get("tolerance", DEFAULT_TOLERANCE))
        if tolerance < 0:
            raise BillError(f"merge 的容差不该是负数，现在是 {tolerance:g}。")

        # 「没写这一项」和「写了个空的」必须分开：缺省要落到那份默认词表上，
        # 而显式写 `keywords = []` 是「我知道我在关掉这道过滤」。
        # 两者混为一谈的话，一份只写了汇率的 settings.toml 会**悄悄**把过滤关掉，
        # 自动合并于是比内置默认激进得多——正是那种不报错的错。
        raw_keywords = merge.get("keywords")
        if raw_keywords is None:
            keywords = DEFAULT_KEYWORDS
        else:
            keywords = tuple(
                str(word).strip() for word in raw_keywords if str(word).strip()
            )

        return Settings(
            display_currency=display,
            rates=rates,
            timezone=timezone,
            merge_enabled=bool(merge.get("enabled", True)),
            merge_window_hours=window,
            merge_tolerance=tolerance,
            merge_keywords=keywords,
        )

    return Settings()
