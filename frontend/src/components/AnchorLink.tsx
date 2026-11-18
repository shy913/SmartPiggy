import type { MouseEvent, ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import { anchorKey, saveAnchor } from "../lib/anchor";

/** 只有普通左键才记录锚点：Ctrl/⌘/Shift/中键是在开新标签页，不该覆盖
 *  当前列表「刚才看到哪儿」。 */
function plainLeftClick(event: MouseEvent<HTMLAnchorElement>) {
  return (
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey
  );
}

/**
 * 从列表点进详情页的链接，离开前顺手记住当前位置和「刚点的那一条」。
 *
 * 所有列表里的详情链接都该用它。手写 `saveAnchor` 很容易漏，漏了之后只是
 * 返回时没有滚动定位、也没有高亮——屏幕上不会报错，所以很难发现。
 *
 * 目标那一页回来后用 `useReturnAnchor` 取一次，给对应的行写
 * `data-anchor-id={target}`、`data-flash`，再用类名亮一下即可。
 */
export function AnchorLink({
  target,
  to,
  state,
  title,
  className,
  children,
  onClick,
}: {
  /** 形如 `txn-12`、`group-3`、`suggestion-五金店`。 */
  target: string;
  to: string;
  state?: unknown;
  title?: string;
  className?: string;
  children: ReactNode;
  onClick?: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const location = useLocation();
  return (
    <Link
      to={to}
      state={state}
      title={title}
      className={className}
      onClick={(event) => {
        if (plainLeftClick(event)) {
          saveAnchor(
            anchorKey(location.pathname, location.search),
            target,
          );
        }
        onClick?.(event);
      }}
    >
      {children}
    </Link>
  );
}
