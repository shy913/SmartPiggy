"""本地设置：汇率、时区、大额阈值。

跟列映射一样走 TOML：内置一份默认值，用户目录里放一份同名文件就整体覆盖它。
读取时带上文件的修改时间做缓存键，所以改了文件下次请求就生效，不用重启。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

from . import config, dates, money
from .errors import BillError

__all__ = ["Settings", "load"]

DEFAULT_DISPLAY = "GBP"
DEFAULT_TIMEZONE = "Europe/London"
_CODE = re.compile(r"^[A-Z0-9]{1,8}$")

# 「大额」阈值的默认值，单位是**显示币种的主单位**（500 就是 £500）。
# 注意它跟着 display 走：display 改成 CNY 之后，同样的数就变成 ¥500 / ¥2,000。
DEFAULT_LARGE_OUT = 500
DEFAULT_LARGE_IN = 2000


def _threshold(value: object, label: str) -> int:
    """把一个阈值（显示币种的主单位）换算成最小单位。0 表示这一侧不启用。"""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise BillError(
            f"大额阈值「{label}」不是个数字：{value!r}。不想要这一侧就写 0。"
        ) from None
    if amount < 0:
        raise BillError(f"大额阈值「{label}」不能是负数，现在是 {amount:g}。")
    return int(round(amount * 100))


@dataclass(frozen=True)
class Settings:
    display_currency: str = DEFAULT_DISPLAY
    # 「1 单位显示币种 = 多少该币种」，例如 {"CNY": 8.9} 表示 1 GBP = 8.9 CNY。
    rates: dict[str, float] = field(default_factory=dict)
    # 所有时间都换算成这个时区显示（IANA 名字）。
    timezone: str = DEFAULT_TIMEZONE
    # 单笔到这个数就算「大额」，不进合计（0 = 该侧不启用）。
    # 单位是**显示币种的最小单位**，而且只对换算得到显示币种的币种生效——
    # 没配汇率的币种比不了（见 repo._comparable）。
    large_out_cents: int = DEFAULT_LARGE_OUT * 100
    large_in_cents: int = DEFAULT_LARGE_IN * 100

    def tz(self):
        return dates.zone(self.timezone)

    def factors(self) -> dict[str, float]:
        """币种 -> 乘数。原金额乘上它就是显示币种的最小单位数。

        显示币种本身不在表里（乘 1），没配汇率的币种也不在里面——
        调用方要保证它们不会被按 1:1 混进合计。
        """
        factors: dict[str, float] = {}
        for code, rate in self.rates.items():
            clean = str(code).upper()
            if not _CODE.match(clean):
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

    def describe_large(self) -> str:
        """给界面用的一句话：哪些没算进合计。

        两侧都关着就返回空串——没有大额这回事时，页面上不该多出一句话。

        币种代码必须写出来：``format_amount`` 只印裸数字，光说「≥ 500」没人知道
        比的是哪个币种的 500，而同一个「500」在 GBP 账本和 CNY 账本下差着 8.9 倍。
        """
        sides = []
        if self.large_out_cents:
            sides.append(f"支出 ≥ {money.format_amount(self.large_out_cents)}")
        if self.large_in_cents:
            sides.append(f"收入 ≥ {money.format_amount(self.large_in_cents)}")
        if not sides:
            return ""
        return (
            f"单笔{'、'.join(sides)} {self.display_currency} 算大额，"
            "不计入合计，单列在页面底部。"
        )


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
        if not _CODE.match(display):
            raise BillError(f"settings 里的 display 币种「{display}」不像个币种代码。")
        rates = {
            str(code).upper(): float(rate)
            for code, rate in (section.get("rates") or {}).items()
        }

        timezone = str(parsed.get("timezone") or DEFAULT_TIMEZONE)
        dates.zone(timezone)  # 名字写错就当场报，别等导入时才发现

        # 逐键回落到默认值。**不能**写成「段缺失就关闭」：用户文件是**整体覆盖**
        # 内置文件的，而 `load()` 读到第一个能读的文件就 return——官方文档教的
        # 做法就是在用户目录放一份 settings.toml，所以多数老用户的文件里根本
        # 没有 [large]。按段回落会让功能恰好在这批人身上静默失效。
        large = parsed.get("large") or {}
        large_out = _threshold(large.get("out", DEFAULT_LARGE_OUT), "out")
        large_in = _threshold(large.get("in", DEFAULT_LARGE_IN), "in")

        return Settings(
            display_currency=display,
            rates=rates,
            timezone=timezone,
            large_out_cents=large_out,
            large_in_cents=large_in,
        )

    return Settings()
