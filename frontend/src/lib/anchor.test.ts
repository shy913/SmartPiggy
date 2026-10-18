import { afterEach, describe, expect, it } from "vitest";

import { anchorKey, moveAnchor, saveAnchor, takeAnchor } from "./anchor";

afterEach(() => sessionStorage.clear());

describe("列表访问锚点", () => {
  it("取一次就删：回来只恢复一次，再刷新不该又跳一遍", () => {
    const key = anchorKey("/transactions", "?start=&end=");
    saveAnchor(key, "txn-3");

    expect(takeAnchor(key)).toMatchObject({ target: "txn-3" });
    expect(takeAnchor(key)).toBeNull();
  });

  it("太旧的锚点丢掉——隔了半天再回来，用户早忘了自己点到哪儿", () => {
    const key = anchorKey("/transactions", "");
    sessionStorage.setItem(
      key,
      JSON.stringify({ y: 100, target: "txn-3", at: Date.now() - 31 * 60 * 1000 }),
    );
    expect(takeAnchor(key)).toBeNull();
  });

  it("坏数据不抛，当作没有锚点", () => {
    const key = anchorKey("/transactions", "");
    sessionStorage.setItem(key, "{not json");
    expect(() => takeAnchor(key)).not.toThrow();
    expect(takeAnchor(key)).toBeNull();
  });

  it("key 把筛选算进去：换一段筛选就是另一个列表", () => {
    expect(anchorKey("/transactions", "?start=2026-10-01")).not.toBe(
      anchorKey("/transactions", ""),
    );
  });

  it("搬家时位置和目标一起走，原地址那份删掉", () => {
    // 改一笔之后回去的地址是重新拼的，键变了；高亮不能因此丢掉。
    const from = "/transactions";
    const to = "/transactions?start=2026-10-01&end=";
    sessionStorage.setItem(
      anchorKey("/transactions", ""),
      JSON.stringify({ y: 480, target: "txn-3", at: Date.now() }),
    );

    moveAnchor(from, to, "txn-3");

    expect(
      takeAnchor(anchorKey("/transactions", "?start=2026-10-01&end=")),
    ).toMatchObject({ y: 480, target: "txn-3" });
    expect(takeAnchor(anchorKey("/transactions", ""))).toBeNull();
  });

  it("从别的页面进来时不顺走那一页的锚点", () => {
    // 从合并组点进某一笔再改，搬走的只能是交易列表的锚点。
    sessionStorage.setItem(
      anchorKey("/groups", "?group=7"),
      JSON.stringify({ y: 300, target: "member-21", at: Date.now() }),
    );

    moveAnchor("/groups?group=7", "/transactions?start=2026-10-01&end=", "txn-3");

    expect(takeAnchor(anchorKey("/groups", "?group=7"))).toMatchObject({
      target: "member-21",
    });
    expect(
      takeAnchor(anchorKey("/transactions", "?start=2026-10-01&end=")),
    ).toMatchObject({ target: "txn-3" });
  });
});
