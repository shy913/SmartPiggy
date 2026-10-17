import { Link, useLocation } from "react-router-dom";

import type { ImportRow } from "../api/types";
import { NumTD, NumTH, Table, TableScroll, TBody, TD, TH, THead, TR } from "../components/ui/table";
import { anchorKey, saveAnchor } from "../lib/anchor";
import { cn } from "../lib/utils";

export function ImportsTable({
  imports,
  highlightedId = null,
}: {
  imports: ImportRow[];
  highlightedId?: number | null;
}) {
  const location = useLocation();
  const back = `${location.pathname}${location.search}`;
  return (
    <TableScroll>
      <Table>
        <THead>
          <TR>
            <TH>文件</TH>
            <TH>导入时间</TH>
            <NumTH>新增</NumTH>
            <NumTH>跳过</NumTH>
            <NumTH>警告</NumTH>
            <NumTH>合并</NumTH>
          </TR>
        </THead>
        <TBody>
          {imports.map((row) => (
            <TR
              key={row.id}
              data-anchor-id={`import-${row.id}`}
              data-flash={row.id === highlightedId ? "1" : undefined}
              className={cn(
                "transition-colors duration-700",
                row.id === highlightedId && "bg-accent/10",
              )}
            >
              <TD>
                <Link
                  className="text-accent underline"
                  to={`/imports/${row.id}`}
                  state={{ from: back, backLabel: "导入记录" }}
                  onClick={() =>
                    saveAnchor(anchorKey(location.pathname, location.search), `import-${row.id}`)
                  }
                >
                  {row.filename}
                </Link>
              </TD>
              <TD className="whitespace-nowrap text-muted">{row.uploaded_at?.slice(0, 16)}</TD>
              <NumTD>{row.inserted_count}</NumTD>
              <NumTD className="text-muted">{row.duplicate_count}</NumTD>
              <NumTD className={row.error_count ? "text-danger" : "text-muted"}>
                {row.warning_count}
              </NumTD>
              <NumTD className="text-muted">{row.merged_count}</NumTD>
            </TR>
          ))}
        </TBody>
      </Table>
    </TableScroll>
  );
}
