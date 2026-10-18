import type { MouseEvent } from "react";
import { Link } from "react-router-dom";

import { useBack } from "../lib/useBack";

type BackProps = {
  to: string;
  label: string;
  /** 有应用内历史就真退一步，而不是再压一条历史。 */
  back?: boolean;
  /** 只能 replace 到 `to` 时一并带过去的来源信息。 */
  state?: unknown;
};

/** 修饰键/中键点击交给浏览器：新标签打开、Ctrl+点击仍然走 href。 */
function plainLeftClick(event: MouseEvent<HTMLAnchorElement>) {
  return (
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey
  );
}

/** 回程动作本身：带边框的胶囊，箭头用强调色。
 *
 *  **不是一条蓝色下划线**：它是个动作，不是正文里的引用。默认字色压到 muted，
 *  箭头用强调色；悬停才整颗亮起来——不跟页面上的金额抢注意力，但一眼看得出
 *  可以点。
 *
 *  单独导出是为了给那些不需要吸顶的地方（导入详情、读取出错时的兜底）也用
 *  同一副长相，免得几处慢慢各长各的。 */
export function BackPill({ to, label, back = false, state }: BackProps) {
  const goBack = useBack();
  return (
    <Link
      to={to}
      state={state}
      onClick={(event) => {
        if (!back || !plainLeftClick(event)) return;
        // 必须自己挡掉 Link 的导航，否则会先退一步又 push 一条 `to`。
        event.preventDefault();
        goBack(to, { state });
      }}
      className="group inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-panel px-3.5 text-sm text-muted transition-colors hover:border-accent/50 hover:bg-panel-hover hover:text-text"
    >
      <span
        aria-hidden
        className="text-base leading-none text-accent transition-transform group-hover:-translate-x-0.5"
      >
        ←
      </span>
      <span>{label}</span>
    </Link>
  );
}

/** 从列表点进某一页之后，顶上那条回程链接。
 *
 *  吸在视口顶上而不是随页面滚走：合并组页可能很长，这一笔的明细也可能拉出
 *  好几屏；翻到哪里都该够得到「回去」。
 *
 *  合并组页、「这一笔」和交易记录共用这一份。`-mx-4` 抵掉 <main> 的左右留白，
 *  让底色铺满整行，滚过去的内容不会从字缝里透出来。
 */
export function BackLink(props: BackProps) {
  return (
    // top 用顶栏量出来的高度：顶栏常驻之后，贴在 top:0 会被它整个盖住，
    // 「回去」就点不到了。高度随折行/安全区变，所以只能读变量。
    <div
      className="sticky z-10 -mx-4 mb-3 bg-bg/90 px-4 py-2 backdrop-blur"
      style={{ top: "var(--bill-header-height, 57px)" }}
    >
      <BackPill {...props} />
    </div>
  );
}
