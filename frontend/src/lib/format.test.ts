import { describe, expect, it } from "vitest";

import { formatAmount, minute } from "./format";

describe("formatAmount", () => {
  it("把最小单位写成人看的样子", () => {
    expect(formatAmount(3250)).toBe("32.50");
    expect(formatAmount(1200000)).toBe("12,000.00");
    expect(formatAmount(0)).toBe("0.00");
  });

  it("负数带减号，不丢", () => {
    expect(formatAmount(-88800)).toBe("-888.00");
    expect(formatAmount(-5)).toBe("-0.05");
  });

  it("先整除再补零，不走浮点", () => {
    // 12.30 元如果先做 1230 / 100，会变成 12.300000000000001。整除取余不会。
    expect(formatAmount(1230)).toBe("12.30");
    expect(formatAmount(10)).toBe("0.10");
    expect(formatAmount(1)).toBe("0.01");
  });
});

describe("minute", () => {
  it("掐到分钟", () => {
    expect(minute("2026-10-14 18:40:07")).toBe("2026-10-14 18:40");
  });

  it("认不出形状的原样放回去", () => {
    // 宁可显示长一点，也不要截出半个时间。
    expect(minute("2026-10-14")).toBe("2026-10-14");
    expect(minute("")).toBe("");
    expect(minute(null)).toBe("");
  });
});
