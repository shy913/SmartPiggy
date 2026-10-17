import { afterEach, describe, expect, it } from "vitest";

import { anchorKey, saveAnchor, takeAnchor } from "./anchor";

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
});
