// 金额与时间的显示。跟服务端那两个格式化函数逐字对应，
// 免得同一个数在页面上出现两种写法。

/**
 * 币种最小单位 → 显示用字符串。跟 `bill/money.py` 的 `format_amount` 同口径。
 *
 * 与币种无关：人民币的「分」和英镑的便士都是 1/100，所以除了币种符号之外显示
 * 方式一样。币种由界面另外标出来——它是账目的属性，不该藏进数字里。
 *
 * 传进来的一定是**整数**（接口那边保证 JSON 里没有浮点）。这里刻意不做
 * `cents / 100` 那种换算：先除再格式化会引入二进制浮点的尾数，
 * 12.30 会变成 12.300000000000001。所以照服务端的老办法整除取余。
 */
export function formatAmount(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const whole = Math.trunc(Math.abs(cents) / 100);
  const fraction = Math.abs(cents) % 100;
  return `${sign}${whole.toLocaleString("en-US")}.${String(fraction).padStart(2, "0")}`;
}

/**
 * 带正号的金额。列表上没有单独的「收支」列，`. 符号 + 颜色` 就说清了方向；
 * 只写 `12.30` 的话，进账和出账长得一样。
 */
export function formatSigned(cents: number): string {
  const text = formatAmount(cents);
  return cents > 0 ? `+${text}` : text;
}

/** `'2026-10-14 18:40:07'` → `'2026-10-14 18:40'`：掐到分钟。跟 `dates.minute` 同口径。 */
export function minute(moment: string | null | undefined): string {
  const text = (moment ?? "").trim();
  if (text.length >= 16 && text[10] === " " && text[13] === ":") {
    return text.slice(0, 16);
  }
  return text;
}

/** 日期那一列只要日子，不要钟点。 */
export function day(moment: string | null | undefined): string {
  return (moment ?? "").slice(0, 10);
}
