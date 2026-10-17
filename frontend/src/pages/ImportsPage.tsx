import { Link, useLocation, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, deleteImport, getImport, listImports } from "../api/client";
import { Button } from "../components/ui/button";
import { Card, Section } from "../components/ui/card";
import { ErrorText, Hint } from "../components/ui/bits";
import { NumTD, NumTH, Table, TableScroll, TBody, TD, TH, THead, TR } from "../components/ui/table";
import { anchorKey, saveAnchor } from "../lib/anchor";
import { formatAmount } from "../lib/format";
import { useFlash } from "../lib/useFlash";
import { useReturnAnchor } from "../lib/useReturnAnchor";
import { cn } from "../lib/utils";
import { ImportsTable } from "./ImportsTable";

export function ImportsPage() {
  const { data, isPending, error } = useQuery({
    queryKey: ["imports", 100],
    queryFn: () => listImports(100),
  });

  const flashTarget = useReturnAnchor(!isPending && !!data);
  const flashed = useFlash(flashTarget);
  const highlightedId =
    flashed && flashTarget?.startsWith("import-")
      ? Number(flashTarget.slice("import-".length))
      : null;

  if (isPending) return <p className="text-muted">正在读取…</p>;
  if (error) return <ErrorText>{error.message}</ErrorText>;
  if (!data) return null;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">导入记录</h1>
        <Link className="ml-auto text-sm text-accent underline" to="/import">
          再导一份 →
        </Link>
      </div>
      {data.imports.length ? (
        <ImportsTable imports={data.imports} highlightedId={highlightedId} />
      ) : (
        <p className="text-muted">还没有导入过。</p>
      )}
    </div>
  );
}

export function ImportDetailPage() {
  const { id } = useParams();
  const importId = Number(id);
  const location = useLocation();
  const nav = location.state as { from?: string; backLabel?: string } | null;
  const client = useQueryClient();

  const { data, isPending, error } = useQuery({
    queryKey: ["import", importId],
    queryFn: () => getImport(importId),
  });

  const flashTarget = useReturnAnchor(!isPending && !!data);
  const flashed = useFlash(flashTarget);

  const remove = useMutation({
    mutationFn: () => deleteImport(importId),
    onSuccess: () => client.invalidateQueries(),
  });

  if (isPending) return <p className="text-muted">正在读取…</p>;
  if (error) return <ErrorText>{error.message}</ErrorText>;
  if (!data) return null;

  const record = data.import_;

  return (
    <div>
      <Link className="text-accent underline" to={nav?.from ?? "/imports"}>
        ← {nav?.backLabel ?? "导入记录"}
      </Link>
      <h1 className="mb-4 mt-3 text-xl font-semibold">{record.filename}</h1>

      <Card>
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <Stat label="导入时间" value={record.uploaded_at?.slice(0, 16) ?? ""} />
          <Stat label="用的配置" value={record.profile_name} />
          <Stat label="文件里的行数" value={String(record.row_count)} />
          <Stat label="新增" value={String(record.inserted_count)} />
          <Stat label="跳过（已存在）" value={String(record.duplicate_count)} />
          <Stat label="自动合并" value={String(record.merged_count)} />
          <Stat label="警告" value={String(record.warning_count)} />
          <Stat label="错误" value={String(record.error_count)} />
        </div>
        <div className="mt-4">
          <Button
            variant="danger"
            size="sm"
            disabled={remove.isPending}
            onClick={() => {
              if (confirm("删掉这一次导入？它带进来的记录会一起删掉。")) remove.mutate();
            }}
          >
            删掉这一次导入
          </Button>
        </div>
        {remove.error ? (
          <div className="mt-3">
            <ErrorText>
              {remove.error instanceof ApiError ? remove.error.message : "没删成。"}
            </ErrorText>
          </div>
        ) : null}
      </Card>

      {data.issues.length ? (
        <Section title={`这次导入遇到的问题（${data.issues.length} 条）`}>
          <ul className="m-0 list-none p-0 text-sm">
            {data.issues.map((issue, index) => (
              <li key={index} className="py-1">
                <span className={issue.level === "error" ? "text-danger" : "text-warn"}>
                  {issue.row_number ? `第 ${issue.row_number} 行：` : ""}
                  {issue.message}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title="这次带进来的记录（前 50 条）">
        <Hint>这里看的是**原始记录**，不是合并之后的样子——合并组跨多次导入，本来就不属于任何一次。</Hint>
        <TableScroll>
          <Table>
            <THead>
              <TR>
                <TH>时间</TH>
                <TH>对方 / 说明</TH>
                <NumTH>金额</NumTH>
                <TH>已并入</TH>
              </TR>
            </THead>
            <TBody>
              {data.sample.map((row) => {
                const value = row as Record<string, unknown>;
                const cents = Number(value.display_cents ?? 0);
                const highlighted = flashed && flashTarget === `txn-${value.id}`;
                return (
                  <TR
                    key={String(value.id)}
                    data-anchor-id={`txn-${value.id}`}
                    data-flash={highlighted ? "1" : undefined}
                    className={cn(
                      "transition-colors duration-700",
                      highlighted && "bg-warn/20",
                    )}
                  >
                    <TD className="whitespace-nowrap">{String(value.occurred_at).slice(0, 16)}</TD>
                    <TD>
                      <Link
                        className="text-accent underline"
                        to={`/transactions/${value.id}`}
                        state={{
                          from: `${location.pathname}${location.search}`,
                          backLabel: "这次导入",
                        }}
                        onClick={() =>
                          saveAnchor(
                            anchorKey(location.pathname, location.search),
                            `txn-${value.id}`,
                          )
                        }
                      >
                        {String(value.counterparty || value.description || "")}
                      </Link>
                    </TD>
                    <NumTD>{formatAmount(value.direction === "out" ? -cents : cents)}</NumTD>
                    <TD className="text-muted">{value.group_id ? "是" : ""}</TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </TableScroll>
      </Section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex flex-col">
      <span className="text-xs text-muted">{label}</span>
      <span className="tabular">{value}</span>
    </span>
  );
}
