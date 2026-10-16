import type { MemberRow } from "../api/types";
import { formatSigned } from "../lib/format";
import { cn } from "../lib/utils";

/**
 * 合并组里一笔的金额。**主数是原币金额**——它就是账单上那个数，跟对方转来的钱、
 * 跟微信里那条记录对得上；换过币种的那几笔后面再跟一份换算到显示币种的数，
 * 好跟组头那个净额对得上。
 *
 * 两份数各标各的币种。两个不同币种的数挨在一起而不标清楚，就可能被当成同一个
 * 币种——这一格先前正是这么错的：数换算了，标签却还是原币种，于是印出
 * 「-77.53 CNY」。原币种恰好就是显示币种时不标（跟列表页、组头一个口径）。
 *
 * 「换算过没有」看服务端给这一行算出的 `display_currency`，**不拿「币种跟显示
 * 币种不一样」去猜**：没配汇率的币种金额是原样返回的，那时候换算结果就是原数
 * 本身，再跟一份只是把同一个数印两遍。
 */
export function MemberAmount({
  member,
  displayCurrency,
  className,
}: {
  member: MemberRow;
  displayCurrency: string;
  className?: string;
}) {
  // 中性行按 0 显示，跟列表页一个口径，也跟它在这个组净额里的份量一致——净额
  // 那边也是按 0 收进去的。0 换哪种币种都是 0，后面那份就不用跟了。
  if (member.direction === "neutral") {
    return <span className={cn("tabular", className)}>0.00</span>;
  }

  const out = member.direction === "out";
  const original = out ? -member.amount_cents : member.amount_cents;
  const shown = out ? -member.display_cents : member.display_cents;
  const converted = member.display_currency !== member.currency;

  return (
    <span className={cn("inline-flex flex-wrap items-baseline gap-1.5", className)}>
      <span className="tabular">
        {formatSigned(original)}
        {member.currency !== displayCurrency ? ` ${member.currency}` : ""}
      </span>
      {converted ? (
        // 比主数淡一档：交易记录页那一格里整行都是灰的，同色同字号两个数
        // 挨在一起会糊成一团。
        <span className="tabular text-xs text-muted/70">
          {formatSigned(shown)} {member.display_currency}
        </span>
      ) : null}
    </span>
  );
}
