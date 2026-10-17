import { useEffect, useState } from "react";

/** 亮一小会儿就撤。
 *
 *  常亮到下次操作会跟「当前行」这类语义混淆，需求要的是「高亮一下」。
 *
 *  记的是「哪个目标已经过期」，不是「现在亮不亮」：这样 target 一出现就立刻
 *  亮，不用再等一次 `setActive` 的渲染。换目标时旧的过期记号自然对不上。 */
export function useFlash(subject: string | null, ms = 2000): boolean {
  const [expiredFor, setExpiredFor] = useState<string | null>(null);

  useEffect(() => {
    if (subject === null) return;
    const timer = setTimeout(() => setExpiredFor(subject), ms);
    return () => clearTimeout(timer);
  }, [subject, ms]);

  return subject !== null && expiredFor !== subject;
}
