import { describe, expect, it } from "vitest";

import { THEMES, THEME_NAMES, type Theme } from "./themes";

// 这一组断言原来长在 Python 那边（tests/test_charts.py），比对的是 themes.py 与
// style.css 两份颜色。配色收敛成 theme.json 之后「两份值不一致」不可能再发生了，
// 那些**对照式**的断言跟着没了意义。
//
// 但下面这两条是**配色本身的质量**，跟「哪一层渲染」无关，必须活下来：
// 收入/支出两色要分得开，每个颜色当正文读得清。它们是这份配色的验收标准，
// 换一套新配色时首先要过的就是这里。

function channels(colour: string): [number, number, number] {
  const value = colour.replace("#", "");
  return [
    parseInt(value.slice(0, 2), 16) / 255,
    parseInt(value.slice(2, 4), 16) / 255,
    parseInt(value.slice(4, 6), 16) / 255,
  ];
}

function linear(channel: number): number {
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
}

/** WCAG 对比度。 */
export function contrast(a: string, b: string): number {
  const luminance = (colour: string) => {
    const [red, green, blue] = channels(colour).map(linear);
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  };
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

/** OKLab 色差（×100），跟原来那套校验脚本同一个度量。 */
export function deltaE(a: string, b: string): number {
  const oklab = (colour: string): [number, number, number] => {
    const [red, green, blue] = channels(colour).map(linear);
    const long = (0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue) ** (1 / 3);
    const medium = (0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue) ** (1 / 3);
    const short = (0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue) ** (1 / 3);
    return [
      0.2104542553 * long + 0.793617785 * medium - 0.0040720468 * short,
      1.9779984951 * long - 2.428592205 * medium + 0.4505937099 * short,
      0.0259040371 * long + 0.7827717662 * medium - 0.808675766 * short,
    ];
  };
  const [x, y] = [oklab(a), oklab(b)];
  return 100 * Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
}

describe("配色", () => {
  it("收入与支出在每一套主题下都分得开", () => {
    // 一对太近的颜色等于没有颜色——图例和标签都救不回来。
    for (const name of THEME_NAMES) {
      const theme: Theme = THEMES[name];
      expect(deltaE(theme.income, theme.expense), `${name} 的收入 / 支出色太接近`).toBeGreaterThanOrEqual(15);
    }
  });

  it("每一套主题的每个颜色当正文都读得清", () => {
    // 这些颜色全都当过文字：表格里的金额、链接、警告。
    // 「柱子分得开」和「数字看得清」是两个要求，只满足前一个不够。
    const roles: (keyof Theme)[] = [
      "text",
      "muted",
      "accent",
      "danger",
      "warn",
      "income",
      "expense",
      "neutral",
    ];
    for (const name of THEME_NAMES) {
      const theme = THEMES[name];
      for (const role of roles) {
        for (const surface of ["bg", "panel"] as const) {
          const ratio = contrast(String(theme[role]), theme[surface]);
          expect(ratio, `${name} 的 ${String(role)} 在 ${surface} 上只有 ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
        }
      }
    }
  });

  it("分类色板相邻两色分得开，而且在面板上看得见", () => {
    // 饼图的扇区靠颜色区分，相邻两块必须一眼能分开。这里钉的是**正常视觉**
    // 的下限（15）；色盲模拟那一关在选色时用 dataviz 的 validator 跑过，
    // 结果写进了 theme.json 的注释性顺序里——改了颜色就重跑一遍。
    for (const name of THEME_NAMES) {
      const theme = THEMES[name];
      expect(theme.series.length, `${name} 的分类色板太短`).toBeGreaterThanOrEqual(6);
      for (let index = 0; index < theme.series.length; index += 1) {
        const colour = theme.series[index];
        expect(colour, `${name} 的第 ${index + 1} 个分类色不是 hex`).toMatch(/^#[0-9a-f]{6}$/i);
        // 图形元素只需要 3:1；文字才要 4.5:1。
        const ratio = contrast(colour, theme.panel);
        expect(ratio, `${name} 的分类色 ${colour} 在面板上只有 ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(3);
        const next = theme.series[index + 1];
        if (next) {
          expect(
            deltaE(colour, next),
            `${name} 的分类色 ${colour} 与 ${next} 太接近`,
          ).toBeGreaterThanOrEqual(15);
        }
      }
    }
  });

  it("四套主题都在，而且各自带一个模式", () => {
    expect(THEME_NAMES).toEqual(["dark", "light", "dark-teal", "light-teal"]);
    for (const name of THEME_NAMES) {
      // 标签不在这里：它是给人看的名字，由服务端出（见 tests/test_theme_sources.py）。
      expect(["dark", "light"]).toContain(THEMES[name].mode);
      expect(THEMES[name].bg).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });
});
