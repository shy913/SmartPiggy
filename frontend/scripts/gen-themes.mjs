// 从 theme.json 生成主题的 CSS 变量块和一份 TS 常量。
//
// **为什么要有这一步**：配色原来存了两份——Python 的 themes.py 给服务端画的
// SVG 用，style.css 给 HTML 用——因为 SVG 拿不到 CSS 变量。两份值必须一样，
// 于是得靠一个测试逐项比对，还专门写了注释提醒「加一套主题要动两处」。
//
// 图表搬到客户端之后这个约束就没了：CSS 和图表组件都从 theme.json 读。
// 于是「两份值不一致」在构造上就不可能——这比再补一条测试可靠。
//
// 现在是「风格 × 深浅」两维：每套风格都有 dark / light 两版。`auto` 不是第三
// 版颜色，而是「跟着浏览器的 prefers-color-scheme 在这两版之间选」——所以它
// 由纯 CSS 的媒体查询表达，服务端外壳也就不用知道浏览器偏好，第一帧就是对的。

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const themeDir = join(here, "..", "src", "theme");
const data = JSON.parse(readFileSync(join(themeDir, "theme.json"), "utf8"));

// json 里的键名 → CSS 变量名。收入 / 支出两色在 CSS 里一直叫 --in / --out。
const VARIABLES = [
  ["bg", "--bg"],
  ["panel", "--panel"],
  ["panel_hover", "--panel-hover"],
  ["border", "--border"],
  ["code_bg", "--code-bg"],
  ["text", "--text"],
  ["muted", "--muted"],
  ["accent", "--accent"],
  ["on_accent", "--on-accent"],
  ["danger", "--danger"],
  ["warn", "--warn"],
  ["income", "--in"],
  ["expense", "--out"],
  ["neutral", "--neutral"],
  ["grid", "--grid"],
  ["axis", "--axis"],
];

const header = `/* 这个文件是生成的，别手改——改 src/theme/theme.json 再跑 npm run themes。
   生成脚本：frontend/scripts/gen-themes.mjs */

`;

// 分类色板是数组，不能走上面那张「键 → 变量」表。饼图这类有多个并列序列的图
// 按固定顺序取色，颜色跟着**实体**走、不跟着排名走（见 charts.tsx）。
const variables = (theme, { scheme }) => [
  `  color-scheme: ${scheme};`,
  ...VARIABLES.map(([key, variable]) => `  ${variable}: ${theme[key]};`),
  ...(theme.series ?? []).map((colour, index) => `  --series-${index + 1}: ${colour};`),
];

const blocks = [];

// 兜底：页面还没有 data-style / data-mode 时（测试、以后可能的内嵌页面）用
// 默认风格 + 跟随系统。这条在后面的属性选择器面前 specificity 更低，所以
// 真正的外壳注入之后不会抢戏。
const fallback = data.styles[data.default_style];
blocks.push(
  `:root {\n${variables(fallback.light, { scheme: "light dark" }).join("\n")}\n}`,
  `@media (prefers-color-scheme: dark) {\n  :root {\n${variables(fallback.dark, { scheme: "dark" })
    .map((line) => `  ${line}`)
    .join("\n")}\n  }\n}`,
);

for (const [style, halves] of Object.entries(data.styles)) {
  const selector = (mode) => `:root[data-style="${style}"][data-mode="${mode}"]`;

  // 明确选了深或浅：不理会浏览器。
  for (const mode of ["light", "dark"]) {
    blocks.push(
      `${selector(mode)} {\n${variables(halves[mode], { scheme: mode }).join("\n")}\n}`,
    );
  }

  // 跟随系统：先给浅色那版，再由媒体查询在系统偏好深色时盖成深色那版。
  // 两块必须是同一个选择器、媒体块在后，后者才会赢。
  blocks.push(
    `${selector("auto")} {\n${variables(halves.light, { scheme: "light dark" }).join("\n")}\n}`,
  );
  blocks.push(
    `@media (prefers-color-scheme: dark) {\n  ${selector("auto")} {\n${variables(halves.dark, {
      scheme: "dark",
    })
      .map((line) => `  ${line}`)
      .join("\n")}\n  }\n}`,
  );
}

writeFileSync(join(themeDir, "themes.css"), header + blocks.join("\n\n") + "\n", "utf8");

const styles = Object.keys(data.styles);
const names = styles.flatMap((style) => [`${style}-dark`, `${style}-light`]);

// TS 侧的常量。图表按「解析后的名字」（如 "cyberpunk-dark"）取一套完整颜色；
// 名字里带风格，所以换风格时图表的分类色也跟着换。
const ts = `// 这个文件是生成的，别手改——改 src/theme/theme.json 再跑 npm run themes。

export type ThemeStyle = ${styles.map((name) => JSON.stringify(name)).join(" | ")};

export type ThemeMode = "auto" | "dark" | "light";

/** 解析后的具体主题：风格 + 实际深浅。auto 不会出现在这里。 */
export type ThemeName = ${names.map((name) => JSON.stringify(name)).join("\n  | ")};

// 标签不在这里：它们是给人看的名字，由服务端（themes.py）出，设置页从
// /api/meta 取——两边各写一遍「标准」「跟随系统」这种话没有意义。
export type Theme = {
${VARIABLES.map(([key]) => `  ${key}: string;`).join("\n")}
  // 分类图（饼图等）的固定色序。按实体分配，不按排名循环。
  series: string[];
  mode: "dark" | "light";
};

export const DEFAULT_STYLE: ThemeStyle = ${JSON.stringify(data.default_style)};
export const DEFAULT_MODE: ThemeMode = ${JSON.stringify(data.default_mode)};
export const STYLE_NAMES: ThemeStyle[] = ${JSON.stringify(styles)};
export const MODE_NAMES: ThemeMode[] = ["auto", "dark", "light"];

export const THEMES: Record<ThemeName, Theme> = ${JSON.stringify(
  Object.fromEntries(
    styles.flatMap((style) => [
      [`${style}-dark`, data.styles[style].dark],
      [`${style}-light`, data.styles[style].light],
    ]),
  ),
  null,
  2,
)};

/**
 * 把「风格 + 深浅设置」解析成一套具体主题。
 *
 * \`mode = "auto"\` 时看浏览器的 prefers-color-scheme；调用方负责在它变化时
 * 重新解析（见 App.tsx 里的媒体查询监听）。放在这里而不是 CSS 里，是因为
 * 图表组件要拿到具体的一套颜色，没法读 CSS 变量。
 */
export function resolveThemeName(
  style: ThemeStyle,
  mode: ThemeMode,
  prefersDark: boolean,
): ThemeName {
  const half = mode === "auto" ? (prefersDark ? "dark" : "light") : mode;
  return \`\${style}-\${half}\` as ThemeName;
}
`;

writeFileSync(join(themeDir, "themes.ts"), ts, "utf8");
console.log(
  `主题已生成：${styles.join("、")}（每套 dark / light，另有 auto 跟随系统）`,
);
