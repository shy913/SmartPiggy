import { Link } from "react-router-dom";

import type { ImportRow } from "../api/types";
import { NumTD, NumTH, Table, TableScroll, TBody, TD, TH, THead, TR } from "../components/ui/table";

export function ImportsTable({ imports }: { imports: ImportRow[] }) {
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
            <TR key={row.id}>
              <TD>
                <Link className="text-accent underline" to={`/imports/${row.id}`}>
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
