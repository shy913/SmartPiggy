import type { MemberRow } from "../api/types";
import { formatSigned } from "../lib/format";
import { cn } from "../lib/utils";

/**
 * 合并组里一笔的金额。两份数，各管一头：
 *
 * - **主数**是换算到显示币种的，跟组头那一行的净额一个口径——展开之后这几笔
 *   加起来，正好是上面那个净额；
 * - **原币金额**跟在后面，只有真的换算过的那几笔才有，用来跟账单原文、跟微信
 *   里那条转账对回去（`详情页`也留着一份）。
 *
 * 「换算过没有」看服务端给这一行算出来的 `display_currency`，**不拿「币种跟
 * 显示币种不一样」去猜**：没配汇率的币种，金额是原样返回的，那时候原币金额就是
 * 主数本身，再跟一份只是把同一个数印两遍。反过来，这种没配汇率的行主数还是它
 * 自己那个币种，必须在后面标出来——一个别的币种的数字混在显示币种的数字里，
 * 看着一模一样。
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
  // 中性行按 0 算，跟列表页一个口径，也跟它在这个组净额里的份量一致——
  // 净额那边也是按 0 收进去的。
  const value = member.direction === "neutral" ? 0 : member.display_cents;
  const cents = member.direction === "out" ? -value : value;
  const original = member.direction === "out" ? -member.amount_cents : member.amount_cents;
  const converted = member.display_currency !== member.currency;

  return (
    <span className={cn("inline-flex flex-wrap items-baseline gap-1.5", className)}>
      <span className="tabular">
        {formatSigned(cents)}
        {!converted && member.currency !== displayCurrency ? ` ${member.currency}` : ""}
      </span>
      {converted ? (
        // 比主数再淡一档：交易记录页那一格里整行都是灰的，同色同字号两个数
        // 挨在一起会糊成一团。
        <span className="tabular text-xs text-muted/70">
          {formatSigned(original)} {member.currency}
        </span>
      ) : null}
    </span>
  );
}
