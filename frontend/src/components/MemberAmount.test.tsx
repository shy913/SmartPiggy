import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { MemberAmount } from "./MemberAmount";
import type { MemberRow } from "../api/types";

function member(overrides: Partial<MemberRow> = {}): MemberRow {
  return {
    id: 1,
    group_id: 8,
    occurred_at: "2026-10-15 13:29:59",
    direction: "out",
    amount_cents: 69000,
    currency: "CNY",
    counterparty: "月亮（代付）",
    description: "",
    effective_category: "",
    display_cents: 7753,
    display_currency: "GBP",
    emoji: "",
    ...overrides,
  };
}

/** 整格渲染出来的字。这一格只有一两个数，直接比整串最不容易放过多余的那份。 */
function text(row: MemberRow, displayCurrency = "GBP"): string {
  return render(<MemberAmount member={row} displayCurrency={displayCurrency} />).container
    .textContent!;
}

describe("合并组里一笔的金额", () => {
  it("换算过的：主数是显示币种，后面另跟一份原币金额", () => {
    // ¥690 那一笔。以前这里是「-77.53 CNY」——一个英镑的数顶着人民币的标签。
    expect(text(member())).toBe("-77.53-690.00 CNY");
  });

  it("本来就是显示币种的那笔不跟第二份，也不标币种", () => {
    // £7.92 那笔：它本身就是显示币种，印两份就是同一个数印两遍。
    expect(text(member({ amount_cents: 792, currency: "GBP", display_cents: 792 }))).toBe(
      "-7.92",
    );
  });

  it("没配汇率的币种：主数就是原数，标出币种但不跟第二份", () => {
    // display_cents 对没配汇率的币种是原样返回的，再跟一份「原币金额」只是把
    // 同一个数印两遍；但这个数确实不是显示币种，必须标出来——不然它混在显示
    // 币种的数字里，长得一模一样。
    expect(text(member({ currency: "USD", display_currency: "USD", display_cents: 12345 }))).toBe(
      "-123.45 USD",
    );
  });

  it("进账带 +，方向一眼看得出来", () => {
    expect(text(member({ direction: "in", amount_cents: 1357, display_cents: 152 }))).toBe(
      "+1.52+13.57 CNY",
    );
  });

  it("中性行按 0 算，跟它在净额里的份量一致", () => {
    expect(text(member({ direction: "neutral", amount_cents: 1357, display_cents: 152 }))).toBe(
      "0.00+13.57 CNY",
    );
  });

  it("显示币种换成人民币之后，换算过的是英镑那笔", () => {
    // 判断「换没换过」看的是服务端算出来的 display_currency，不是写死的 GBP。
    const gbp = member({
      amount_cents: 792,
      currency: "GBP",
      display_cents: 7049,
      display_currency: "CNY",
    });
    expect(text(gbp, "CNY")).toBe("-70.49-7.92 GBP");
  });
});
