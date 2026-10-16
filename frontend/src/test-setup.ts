import "@testing-library/jest-dom/vitest";

// jsdom 里没有 ResizeObserver，而 Recharts 的 ResponsiveContainer 要它 ——
// 少了这个桩，凡是渲染到汇总页的用例都会当场抛，报的还是「ResizeObserver is
// not defined」这种跟被测算的东西毫无关系的错。
//
// 空实现就够：jsdom 量不出真实尺寸，图表在那里面本来也画不出来。这几条用例
// 要验的是数据到没到、字写对没写对，不是柱子多高。
class ResizeObserverStub implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub;
