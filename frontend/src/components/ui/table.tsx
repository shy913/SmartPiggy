import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

/** 表格外面这一层负责横向滚动。
 *
 *  手机上不把表等比缩小——那只会让字小到看不清。让它保持可读的尺寸、
 *  用户横向滑一下，这是全站一贯的做法。 */
export function TableScroll({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("overflow-x-auto", className)} {...props} />;
}

export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    <table
      className={cn("w-full border-collapse text-sm tabular", className)}
      {...props}
    />
  );
}

export function THead({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn("text-muted", className)} {...props} />;
}

export function TBody({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={className} {...props} />;
}

export function TR({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr className={cn("border-b border-border last:border-0", className)} {...props} />
  );
}

export function TH({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "whitespace-nowrap px-2 py-2 text-left font-medium first:pl-0 last:pr-0",
        className,
      )}
      {...props}
    />
  );
}

export function TD({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-2 py-2 first:pl-0 last:pr-0", className)} {...props} />;
}

/** 数字那一列：右对齐 + 等宽数字，位数才对得齐。 */
export function NumTD({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <TD className={cn("text-right whitespace-nowrap", className)} {...props} />;
}

export function NumTH({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return <TH className={cn("text-right", className)} {...props} />;
}
