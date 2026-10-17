import { useNavigate } from "react-router-dom";

/** 回程链路上的一页记着下一层要回哪儿。
 *
 *  `from` / `backLabel` 是这一页回到哪、那一页叫什么；`up` 是回程页**自己的**
 *  来路。列表点进详情时把它一起带上，详情再退回列表，列表顶上的「← 汇总」
 *  才还在，不会被这一层吃掉。
 */
export type BackState = {
  from?: string;
  backLabel?: string;
  up?: BackState;
  /** 这一条是表单保存/取消或换筛选之后 push 出来的：
   *  前一条历史是表单或旧筛选，不是 `from`，不能 navigate(-1) 退回去。 */
  noHistoryBack?: boolean;
} | null;

/** BrowserRouter 把历史条目序号写在 `window.history.state.idx` 里：初次加载
 *  是 0，每次 push +1，replace 不变。新标签页直接打开、以及测试里的
 *  MemoryRouter 都没有这个序号。 */
export function hasInAppHistory(): boolean {
  const state = window.history.state as { idx?: number } | null;
  const idx = state?.idx;
  return typeof idx === "number" && idx > 0;
}

/** 回上一页。
 *
 *  真正的「返回」应该是退历史，而不是再压一条同样的地址——后者会让浏览器
 *  后退先退回刚离开的那一页，页面上的连续返回也跟着断。没有应用内历史时
 *  （刷新直接打开、新标签页）才 replace 到兜底地址，不把用户送出 SPA。 */
export function useBack() {
  const navigate = useNavigate();
  return (to: string, options?: { state?: unknown }) => {
    if (hasInAppHistory()) {
      navigate(-1);
      return;
    }
    navigate(to, { replace: true, state: options?.state });
  };
}
