"""本地设置：配色、汇率换算、时区、大额阈值、合并。

跟列映射一样走 TOML：内置一份默认值，用户目录里放一份同名文件就整体覆盖它。
**设置页保存时写的就是用户目录那份**（见 `save`），改了下次请求就生效，不用重启。
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from typing import Any

from . import config, dates, themes
from .errors import BillError

__all__ = [
    "Settings",
    "as_form",
    "is_currency_code",
    "load",
    "read_form",
    "render_toml",
    "save",
]

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
    # 界面配色分两维：风格与深浅，取值见 themes.py。
    theme_style: str = themes.DEFAULT_STYLE
    theme_mode: str = themes.DEFAULT_MODE
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
    # 导入后自动检测并合并 AA。
    merge_enabled: bool = True
    # 顺带把「收支平衡」的两笔（一笔支出 + 之后一笔等额收入）也合上，净额是 0。
    merge_balanced: bool = True
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


def is_currency_code(value: str) -> bool:
    """像不像个币种代码（``CNY``、``GBP``）。

    设置里的 ``display`` 和手动记账表单里的币种共用这一套判断——两处各写一份
    正则的话，早晚会有一处松一格。

    **不 trim**：这里判的是「能不能原样拿去用」，而调用方拿到的还是原来那个值。
    在这里宽容地把 ``" GBP "`` 放过去，那个带空格的字符串就会被当成币种存下来、
    跟谁也对不上，而且一路不报错——要收拾空格请在调用方收拾干净。
    """
    return bool(_CODE.match(value or ""))


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

        ui = parsed.get("ui") or {}
        # 新配置写 style + mode；旧配置只有 theme（"dark-teal" 这种）。
        # 旧键仍然认，并按 LEGACY 映射成同一套观感——不因为改了配置结构就把
        # 用户原来的深浅悄悄换掉。
        legacy = str(ui.get("theme") or "")
        if legacy and "style" not in ui and "mode" not in ui:
            try:
                style, mode = themes.LEGACY[legacy]
            except KeyError:
                themes.get_style(legacy)  # 只会抛错，消息里列出可选风格
                raise  # pragma: no cover —— 上面那行必然抛错
        else:
            style = str(ui.get("style") or themes.DEFAULT_STYLE)
            mode = str(ui.get("mode") or themes.DEFAULT_MODE)
        # 同上：认不出的名字当场报，别悄悄换个样子。
        themes.get_style(style)
        themes.get_mode(mode)

        # 逐键回落到默认值。**不能**写成「段缺失就关闭」：用户文件是**整体覆盖**
        # 内置文件的，而 `load()` 读到第一个能读的文件就 return——官方文档教的
        # 做法就是在用户目录放一份 settings.toml，所以多数老用户的文件里根本
        # 没有 [large]。按段回落会让功能恰好在这批人身上静默失效。
        large = parsed.get("large") or {}
        large_out = _threshold(large.get("out", DEFAULT_LARGE_OUT), "out")
        large_in = _threshold(large.get("in", DEFAULT_LARGE_IN), "in")

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
            theme_style=style,
            theme_mode=mode,
            display_currency=display,
            rates=rates,
            timezone=timezone,
            large_out_cents=large_out,
            large_in_cents=large_in,
            merge_enabled=bool(merge.get("enabled", True)),
            merge_balanced=bool(merge.get("balanced", True)),
            merge_window_hours=window,
            merge_tolerance=tolerance,
            merge_keywords=keywords,
        )

    return Settings()


# --------------------------------------------------------------------------
# 设置页：表单 <-> Settings
# --------------------------------------------------------------------------


def as_form(conf: Settings) -> dict[str, Any]:
    """把一份设置摊成表单初值。

    汇率那几行**多留两个空位**：这个应用不带 JS，没有「加一行」这种交互，
    想加一个币种就得有现成的空格可填。
    """
    rates = [{"code": code, "rate": f"{rate:g}"} for code, rate in sorted(conf.rates.items())]
    rates += [{"code": "", "rate": ""} for _ in range(max(0, 3 - len(rates)))]
    return {
        "theme_style": conf.theme_style,
        "theme_mode": conf.theme_mode,
        "display": conf.display_currency,
        "timezone": conf.timezone,
        "rates": rates,
        "large_out": _major(conf.large_out_cents),
        "large_in": _major(conf.large_in_cents),
        "merge_enabled": conf.merge_enabled,
        "merge_balanced": conf.merge_balanced,
        "merge_window_hours": f"{conf.merge_window_hours:g}",
        "merge_tolerance": f"{conf.merge_tolerance:g}",
        "merge_keywords": "、".join(conf.merge_keywords),
    }


def read_form(data) -> tuple[Settings | None, dict[str, Any], list[str]]:
    """把设置页那张表单读成一份 Settings。

    返回 ``(设置, 回显用的初值, 错误清单)``；有错时第一项是 None。回显的那份
    是**原样收下用户填的东西**，不是解析结果——填错了不该让人重填一遍。

    这里只做校验，不落盘：写文件是 `save` 的事，两件事分开才好一条一条测。
    """
    values = _echo(data)
    errors: list[str] = []

    theme_style = (data.get("theme_style") or "").strip() or themes.DEFAULT_STYLE
    try:
        themes.get_style(theme_style)
    except BillError as error:
        errors.append(str(error))

    theme_mode = (data.get("theme_mode") or "").strip() or themes.DEFAULT_MODE
    try:
        themes.get_mode(theme_mode)
    except BillError as error:
        errors.append(str(error))

    display = (data.get("display") or "").strip().upper()
    if not is_currency_code(display):
        errors.append(f"显示币种「{display}」不像个币种代码（比如 GBP、CNY）。")

    timezone = (data.get("timezone") or "").strip() or DEFAULT_TIMEZONE
    try:
        dates.zone(timezone)
    except Exception:  # noqa: BLE001 —— dates.zone 认不出来就报错，这里只转成人话
        errors.append(f"时区「{timezone}」认不出来，要写 IANA 名字，比如 Europe/London。")

    rates: dict[str, float] = {}
    for index, row in enumerate(values["rates"], start=1):
        code = row["code"].strip().upper()
        text = row["rate"].strip()
        if not code and not text:
            continue
        if not is_currency_code(code):
            errors.append(f"第 {index} 行的币种「{row['code']}」不像个币种代码。")
            continue
        if code == display:
            errors.append(f"第 {index} 行的 {code} 就是显示币种本身，不用配汇率。")
            continue
        if code in rates:
            errors.append(f"{code} 配了不止一次。")
            continue
        try:
            rate = float(text)
        except ValueError:
            errors.append(f"{code} 的汇率「{text}」不是个数字。")
            continue
        if rate <= 0:
            errors.append(f"{code} 的汇率必须大于 0，现在是 {rate:g}。")
            continue
        rates[code] = rate

    large_out = _form_threshold(values["large_out"], "支出", errors)
    large_in = _form_threshold(values["large_in"], "收入", errors)

    enabled = bool(data.get("merge_enabled"))
    balanced_merge = bool(data.get("merge_balanced"))
    try:
        window = float(values["merge_window_hours"] or DEFAULT_WINDOW_HOURS)
        if window <= 0:
            raise ValueError
    except ValueError:
        errors.append("合并窗口得是个正数小时，比如 12。")
        window = DEFAULT_WINDOW_HOURS
    try:
        tolerance = float(values["merge_tolerance"] or 0)
        if tolerance < 0:
            raise ValueError
    except ValueError:
        errors.append("合并容差不能是负数。")
        tolerance = DEFAULT_TOLERANCE

    # 关键词用顿号或逗号隔开都行。留空 = 关掉那道过滤，这是**明确**的选择，
    # 所以不在这里拦——只在预览页那一侧说清楚就好（见 README）。
    keywords = tuple(
        word.strip()
        for word in re.split(r"[、,，]", values["merge_keywords"])
        if word.strip()
    )

    if errors:
        return None, values, errors

    return (
        Settings(
            theme_style=theme_style,
            theme_mode=theme_mode,
            display_currency=display,
            rates=rates,
            timezone=timezone,
            large_out_cents=large_out,
            large_in_cents=large_in,
            merge_enabled=enabled,
            merge_balanced=balanced_merge,
            merge_window_hours=window,
            merge_tolerance=tolerance,
            merge_keywords=keywords,
        ),
        values,
        [],
    )


def _echo(data) -> dict[str, Any]:
    """表单原样收下，只把结构摆成模板好用的样子。"""
    codes = data.getlist("rate_code")
    rates = data.getlist("rate_value")
    rows = [
        {"code": code, "rate": rate}
        for code, rate in zip(codes + [""] * len(rates), rates)
    ]
    while len(rows) < 3:
        rows.append({"code": "", "rate": ""})
    return {
        "theme_style": (data.get("theme_style") or "").strip(),
        "theme_mode": (data.get("theme_mode") or "").strip(),
        "display": (data.get("display") or "").strip(),
        "timezone": (data.get("timezone") or "").strip(),
        "rates": rows,
        "large_out": (data.get("large_out") or "").strip(),
        "large_in": (data.get("large_in") or "").strip(),
        "merge_enabled": bool(data.get("merge_enabled")),
        "merge_balanced": bool(data.get("merge_balanced")),
        "merge_window_hours": (data.get("merge_window_hours") or "").strip(),
        "merge_tolerance": (data.get("merge_tolerance") or "").strip(),
        "merge_keywords": (data.get("merge_keywords") or "").strip(),
    }


def _major(cents: int) -> str:
    """最小单位 -> 主单位，写成不带小数的样子（0 就写 0）。"""
    return f"{cents / 100:g}"


def _form_threshold(text: str, label: str, errors: list[str]) -> int:
    """表单里的阈值（显示币种的主单位）。空 = 0 = 这一侧不启用。"""
    try:
        return _threshold(text or 0, label)
    except BillError as error:
        errors.append(str(error))
        return 0


# --------------------------------------------------------------------------
# 落盘
# --------------------------------------------------------------------------

# 保存时整份重写用户目录里那份配置。**不去原文件里改那几行**：手写的行内注释、
# 缩进、键的顺序都可能有上百种写法，靠正则去认早晚会认错，而认错的代价是留下一
# 份读不了的 TOML——下一次请求整个应用起不来。整份重写至少保证写出来的东西
# 一定读得回来（写完当场重新读一遍，见 save）。
#
# 代价是用户原先写的注释没了，所以覆盖之前把原文件留一份 .bak（只留一次，
# 不覆盖更早的那份）。
_TOML = '''\
# 由「设置」页写出。这个文件把内置的默认设置整份盖住。
# 也可以直接手改这里，改完刷新页面就生效，不用重启。

# 所有时间都换算成这个时区显示（IANA 名字）。
# 注意它必须写在所有表头**之前**：TOML 里表头之后的键都属于那张表。
timezone = "{timezone}"

[currency]
# 所有金额都换算成这个币种显示与统计。
display = "{display}"

# 汇率：**1 单位 display 币种 = 多少该币种**。
# 没在这里列出、也不等于 display 的币种不会被合并，仍按原币种单独成组显示。
rates = {rates}

[large]
# 单笔到什么数算「大额」，不进收入 / 支出合计（汇总页底部单独列出）。
# 单位是上面那个 display 币种的**主单位**；0 表示这一侧不启用。
out = {large_out}
in = {large_in}

[merge]
# 合并：一笔支出加上之后跟着来的收款，合并成一行按净额算。
enabled = {merge_enabled}
# 收支平衡：一笔支出之后一笔跟它等额的收入，净额是 0，也一起合上。
balanced = {merge_balanced}
window_hours = {merge_window}
tolerance = {merge_tolerance}
# 转入要像「别人还你钱」才参与自动合并。留空 = 关掉这道过滤。
keywords = [{merge_keywords}]

[ui]
# 界面风格：{style_names}；深浅：{mode_names}。
style = "{theme_style}"
mode = "{theme_mode}"
'''


def render_toml(conf: Settings) -> str:
    """把一份设置渲染成配置文件。"""
    rates = ", ".join(f"{code} = {rate:g}" for code, rate in sorted(conf.rates.items()))
    keywords = ", ".join(f'"{word}"' for word in conf.merge_keywords)
    return _TOML.format(
        timezone=conf.timezone,
        display=conf.display_currency,
        # 空的时候就写成 `{}`，别留成 `{  }` 那种像是漏填了什么的样子。
        rates=f"{{ {rates} }}" if rates else "{}",
        large_out=_major(conf.large_out_cents),
        large_in=_major(conf.large_in_cents),
        merge_enabled="true" if conf.merge_enabled else "false",
        merge_balanced="true" if conf.merge_balanced else "false",
        merge_window=f"{conf.merge_window_hours:g}",
        merge_tolerance=f"{conf.merge_tolerance:g}",
        merge_keywords=keywords,
        theme_style=conf.theme_style,
        theme_mode=conf.theme_mode,
        style_names="、".join(themes.style_names()),
        mode_names="、".join(themes.mode_names()),
    )


def save(conf: Settings) -> None:
    """写进用户目录那份 settings.toml（先备份、再原子替换）。

    写完**当场读一遍**。写出去的文件要是读不回来（比如某个值里混进了引号），
    下一次请求整个应用都起不来——那种状态不该由一次「保存成功」造成。
    """
    path = config.user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_toml(conf)

    backup = path.with_name(path.name + ".bak")
    if path.exists() and not backup.exists():
        # 只留一次：再存一次也不会把最早那份顶掉。
        try:
            backup.write_bytes(path.read_bytes())
        except OSError as error:
            raise BillError(f"备份 {path.name} 失败：{error}") from None

    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except OSError as error:
        raise BillError(f"写 {path} 失败：{error}") from None

    # 读回来确认一遍。读不回来说明写出去的东西是坏的，当场报出来，
    # 别等下一次请求才炸。
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise BillError(f"写出去的 {path.name} 读不回来：{error}") from None
