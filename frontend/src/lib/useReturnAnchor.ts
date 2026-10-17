import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";

import { anchorKey, takeAnchor, type Anchor } from "./anchor";

/** 从列表点进详情再回来时，接上滚动位置和「刚点的那一条」。
 *
 *  `ready` 必须是「数据到了、行已经渲染出来」。页面还矮的时候 `scrollTo` 会被
 *  夹到 0，等于没恢复；等表长出来再滚才有意义。
 *
 *  返回的目标可以在页面上这样用：`flash && target === "txn-12"`。没有锚点时
 *  返回 null——新打开列表不该有东西在闪。 */
export function useReturnAnchor(
  ready: boolean,
  onRestore?: (anchor: Anchor) => void,
): string | null {
  const location = useLocation();
  const key = anchorKey(location.pathname, location.search);
  const [target, setTarget] = useState<string | null>(null);

  // 调用方通常传内联函数。存进 ref，免得每次都当成新依赖重跑恢复。
  const onRestoreRef = useRef(onRestore);
  onRestoreRef.current = onRestore;

  useEffect(() => {
    if (!ready) return;
    const anchor = takeAnchor(key);
    if (!anchor) return;
    window.scrollTo(0, anchor.y);
    onRestoreRef.current?.(anchor);
    setTarget(anchor.target);
  }, [ready, key]);

  // 保险：数据变了导致目标行移位、或它本来就在屏外时补一刀。目标不存在
  // （比如删除后返回）就只保留原来的像素位置，不硬找。
  useEffect(() => {
    if (!target) return;
    const element = document.querySelector(`[data-anchor-id="${target}"]`);
    if (!element) return;
    const rect = element.getBoundingClientRect();
    if (rect.top < 0 || rect.bottom > window.innerHeight) {
      element.scrollIntoView?.({ block: "center" });
    }
  }, [target]);

  return target;
}
