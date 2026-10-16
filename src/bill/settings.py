"""本地设置。目前只有汇率换算一件事。

跟列映射一样走 TOML：内置一份默认值，用户目录里放一份同名文件就整体覆盖它。
读取时带上文件的修改时间做缓存键，所以改了文件下次请求就生效，不用重启。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

from . import config, dates
from .errors import BillError

__all__ = ["Settings", "load"]

DEFAULT_DISPLAY = "GBP"
DEFAULT_TIMEZONE = "Europe/London"
_CODE = re.compile(r"^[A-Z0-9]{1,8}$")


@dataclass(frozen=True)
class Settings:
    display_currency: str = DEFAULT_DISPLAY
    # 「1 单位显示币种 = 多少该币种」，例如 {"CNY": 8.9} 表示 1 GBP = 8.9 CNY。
    rates: dict[str, float] = field(default_factory=dict)
    # 所有时间都换算成这个时区显示（IANA 名字）。
    timezone: str = DEFAULT_TIMEZONE

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

        return Settings(display_currency=display, rates=rates, timezone=timezone)

    return Settings()
