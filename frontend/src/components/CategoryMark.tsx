/**
 * 每一行开头那个分类 emoji。
 *
 * 没分类的也占住这个位置、给一个问号：空着会让两列的字忽左忽右，也看不出
 * 到底是「没有分类」还是「这一列没渲染出来」。问号是个记号，不是分类。
 */
export function CategoryMark({
  category,
  emoji,
}: {
  category?: string | null;
  emoji?: string;
}) {
  return <span title={category || "未分类"}>{emoji || "❓"}</span>;
}
