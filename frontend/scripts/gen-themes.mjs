// 从 theme.json 生成主题的 CSS 变量块和一份 TS 常量。
//
// **为什么要有这一步**：配色原来存了两份——Python 的 themes.py 给服务端画的
// SVG 用，style.css 给 HTML 用——因为 SVG 拿不到 CSS 变量。两份值必须一样，
// 于是得靠一个测试逐项比对，还专门写了注释提醒「加一套主题要动两处」。
//
// 图表搬到客户端之后这个约束就没了：CSS 和图表组件都从 theme.json 读。
// 于是「两份值不一致」在构造上就不可能——这比再补一条测试可靠。

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

const blocks = Object.entries(data.themes).map(([name, theme]) => {
  const lines = VARIABLES.map(([key, variable]) => `  ${variable}: ${theme[key]};`);
  // :root 那一份同时是 dark：没有 data-theme 时的样子。SPA 由服务端在外壳里
  // 注入 data-theme，所以这条主要是给「注入之前的第一帧」和测试兜底的。
  const selector =
    name === data.default ? ":root,\n:root[data-theme=\"dark\"]" : `:root[data-theme="${name}"]`;
  return `${selector} {\n  color-scheme: ${theme.mode};\n${lines.join("\n")}\n}`;
});

writeFileSync(join(themeDir, "themes.css"), header + blocks.join("\n\n") + "\n", "utf8");

const ts = `// 这个文件是生成的，别手改——改 src/theme/theme.json 再跑 npm run themes。

export type ThemeName = ${Object.keys(data.themes).map((name) => JSON.stringify(name)).join(" | ")};

export const DEFAULT_THEME: ThemeName = ${JSON.stringify(data.default)};

// 标签不在这里：它是给人看的名字，由服务端（themes.py）出，设置页从
// /api/meta 取——两边各写一遍「深色」这种话没有意义。
export type Theme = {
${VARIABLES.map(([key]) => `  ${key}: string;`).join("\n")}
  mode: string;
};

export const THEMES: Record<ThemeName, Theme> = ${JSON.stringify(data.themes, null, 2)};

export const THEME_NAMES = Object.keys(THEMES) as ThemeName[];
`;

writeFileSync(join(themeDir, "themes.ts"), ts, "utf8");
console.log(`主题已生成：${Object.keys(data.themes).join("、")}`);
