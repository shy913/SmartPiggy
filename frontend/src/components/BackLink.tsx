import { Link } from "react-router-dom";

/** 从列表点进某一页之后，顶上那条回程链接。
 *
 *  吸在视口顶上而不是随页面滚走：合并组页可能很长，这一笔的明细也可能拉出
 *  好几屏；翻到哪里都该够得到「回去」。
 *
 *  合并组页和「这一笔」共用这一份，免得两处慢慢各长各的、位置和手感不一样。
 *  `-mx-4` 抵掉 <main> 的左右留白，让底色铺满整行，滚过去的内容不会从字缝里
 *  透出来。 */
export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <div className="sticky top-0 z-10 -mx-4 mb-3 bg-bg/95 px-4 py-2 backdrop-blur">
      <Link
        className="inline-flex items-center rounded-md py-1 pr-2 text-accent underline"
        to={to}
      >
        ← {label}
      </Link>
    </div>
  );
}
