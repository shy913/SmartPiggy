import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** 合并类名：clsx 处理条件，tailwind-merge 让后写的同类工具类盖掉前面的。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
