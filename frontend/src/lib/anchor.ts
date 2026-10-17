/** 离开列表时的位置，以及「刚点进去的那一条」。
 *
 *  放 sessionStorage 而不是 URL：高亮只对「刚回来这一下」有意义。进 URL 的话，
 *  `TransactionsPage` 会把整个 query 当 react-query 的 key（多一个参数就换一份
 *  缓存），而且这个纯界面状态还得跟着发给服务端。也不能放 `location.state`：
 *  浏览器后退回到的是列表那条历史记录，它的 state 是当初进去时的那份旧值。
 */

export type Anchor = {
  /** 离开时窗口的纵向滚动位置。 */
  y: number;
  /** 要亮的那一行，形如 `txn-12`、`group-3`、`import-7`；没有就 null。 */
  target: string | null;
  /** 写入时间，用来丢掉太旧的锚点。 */
  at: number;
};

const PREFIX = "bill:anchor:";

/** 隔了半天再回来就别跳了——用户早忘了自己点到哪儿。 */
const TTL = 30 * 60 * 1000;

/** 锚点挂在**列表自己的地址**上：同一串筛选 + limit 才算同一个列表，换筛选
 *  天然对不上，不需要额外的失效逻辑。
 *
 *  列表用「加大 limit」翻页，每次 `setParams(..., { replace: true })` 替换当前
 *  历史条目，所以离开时的地址和后退回来的地址是同一个。 */
export function anchorKey(pathname: string, search: string) {
  return `${PREFIX}${pathname}${search}`;
}

/** 写在**点走的那一刻**：这时 `window.scrollY` 才是用户正在看的位置。 */
export function saveAnchor(key: string, target: string | null) {
  try {
    sessionStorage.setItem(
      key,
      JSON.stringify({ y: window.scrollY, target, at: Date.now() }),
    );
  } catch {
    // 隐私模式不可写。恢复不了就算了，不能因此让链接点不动。
  }
}

/** 取一次就删。StrictMode 下 effect 会跑两遍，第二遍自然拿到 null，天然幂等。 */
export function takeAnchor(key: string): Anchor | null {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    sessionStorage.removeItem(key);
    const anchor = JSON.parse(raw) as Partial<Anchor> | null;
    if (
      !anchor ||
      typeof anchor.y !== "number" ||
      typeof anchor.at !== "number" ||
      Date.now() - anchor.at > TTL
    ) {
      return null;
    }
    return {
      y: anchor.y,
      target: typeof anchor.target === "string" ? anchor.target : null,
      at: anchor.at,
    };
  } catch {
    // 坏 JSON、存储被禁用：当作没有锚点，页面照常打开。
    return null;
  }
}
