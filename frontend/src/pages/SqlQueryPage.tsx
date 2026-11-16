import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { ApiError, runQuery } from "../api/client";
import type { QueryResult } from "../api/types";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Empty, ErrorText, Hint } from "../components/ui/bits";
import {
  Table,
  TableScroll,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "../components/ui/table";

const EXAMPLE = "SELECT * FROM transactions ORDER BY occurred_at DESC LIMIT 50";

export function SqlQueryPage() {
  const [sql, setSql] = useState(EXAMPLE);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState("");

  const run = useMutation({
    mutationFn: runQuery,
    onSuccess: (body) => {
      setResult(body);
      setError("");
    },
    onError: (failure) => {
      setResult(null);
      setError(failure instanceof ApiError ? failure.message : "查询没跑通。");
    },
  });

  const submit = () => {
    if (run.isPending) return;
    run.mutate(sql);
  };

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">查询</h1>

      <Card>
        <textarea
          aria-label="SQL"
          value={sql}
          onChange={(event) => setSql(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              submit();
            }
          }}
          rows={6}
          spellCheck={false}
          className="w-full resize-y rounded-md border border-border bg-bg px-3 py-2 font-mono text-sm text-text"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button onClick={submit} disabled={run.isPending}>
            {run.isPending ? "正在查…" : "运行"}
          </Button>
          <span className="text-sm text-muted">Ctrl / ⌘ + Enter</span>
        </div>
        <div className="mt-3">
          <ErrorText>{error}</ErrorText>
        </div>
      </Card>

      {result ? (
        <Card className="mt-4">
          <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            <span>{result.rows.length} 行</span>
            <span>{result.elapsed_ms} ms</span>
          </div>
          {result.truncated ? (
            <Hint className="mb-3 mt-0">只显示前 {result.rows.length} 行。</Hint>
          ) : null}
          {result.rows.length ? (
            <TableScroll>
              <Table>
                <THead>
                  <TR>
                    {result.columns.map((name, index) => (
                      <TH key={index}>{name}</TH>
                    ))}
                  </TR>
                </THead>
                <TBody>
                  {result.rows.map((cells, rowIndex) => (
                    <TR key={rowIndex}>
                      {cells.map((value, columnIndex) => (
                        <TD
                          key={columnIndex}
                          className="whitespace-nowrap font-mono"
                        >
                          {value === null ? (
                            <span className="text-muted">NULL</span>
                          ) : (
                            String(value)
                          )}
                        </TD>
                      ))}
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableScroll>
          ) : (
            <Empty>没有结果。</Empty>
          )}
        </Card>
      ) : null}
    </div>
  );
}
