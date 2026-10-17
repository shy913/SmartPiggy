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

/** 写一份指定位置和目标的锚点。`moveAnchor` 要搬原来的位置，所以单独拆出来。 */
function writeAnchor(key: string, y: number, target: string | null) {
  try {
    sessionStorage.setItem(key, JSON.stringify({ y, target, at: Date.now() }));
  } catch {
    // 隐私模式不可写。恢复不了就算了，不能因此让链接点不动。
  }
}

/** 写在**点走的那一刻**：这时 `window.scrollY` 才是用户正在看的位置。 */
export function saveAnchor(key: string, target: string | null) {
  writeAnchor(key, window.scrollY, target);
}

/** `?` 前后拆开。不用 `new URL`：这里拿到的可能只是一个相对路径，也没有
 *  协议和主机，拆成 pathname + search 就够了。 */
function splitUrl(url: string): [string, string] {
  const index = url.indexOf("?");
  return index === -1 ? [url, ""] : [url.slice(0, index), url.slice(index)];
}

/** 把锚点从一屏搬到另一屏。
 *
 *  改一笔之后要回去的地址是重新拼出来的（这一笔现在待的那个月），跟离开列表
 *  时那一屏的地址往往对不上——多几个参数或少几个参数，键就换了，锚点接不上，
 *  回去既不定位也不高亮。搬过去之后，目标换成刚改的那一笔。
 *
 *  位置照搬原来的：月份变了的话像素位置不一定还对得上，但恢复之后目标行要是
 *  不在视野里，`useReturnAnchor` 会把它居中，兜得住。从合并组、导入详情进来
 *  时 `from` 不是列表，不能把那一页的锚点顺走。 */
export function moveAnchor(
  fromUrl: string | null | undefined,
  toUrl: string,
  target: string,
) {
  const [toPath, toSearch] = splitUrl(toUrl);
  let y = window.scrollY;
  if (fromUrl) {
    const [fromPath, fromSearch] = splitUrl(fromUrl);
    if (fromPath === toPath) {
      const previous = takeAnchor(anchorKey(fromPath, fromSearch));
      if (previous) y = previous.y;
    }
  }
  writeAnchor(anchorKey(toPath, toSearch), y, target);
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
