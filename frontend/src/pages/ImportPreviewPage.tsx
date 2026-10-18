import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  cancelPreview,
  confirmPreview,
  getPreview,
  setPreviewProfile,
} from "../api/client";
import { Button } from "../components/ui/button";
import { Card, Section } from "../components/ui/card";
import { ErrorText, Hint, Tag } from "../components/ui/bits";
import { NumTD, NumTH, Table, TableScroll, TBody, TD, TH, THead, TR } from "../components/ui/table";
import { formatAmount } from "../lib/format";

export function ImportPreviewPage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [error, setError] = useState("");

  const { data, isPending, error: loadError } = useQuery({
    queryKey: ["preview", token],
    queryFn: () => getPreview(token),
    retry: false,
  });

  const change = useMutation({
    mutationFn: (profile: string) => setPreviewProfile(token, profile),
    onSuccess: (preview) => client.setQueryData(["preview", token], preview),
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure.message : "换配置没成。"),
  });

  const confirm = useMutation({
    mutationFn: () => confirmPreview(token),
    onSuccess: (body) => {
      // 先把这份预览从缓存里**摘掉**再刷新别的：服务端确认之后就把 token 丢了，
      // 留着它的话，一次全量刷新会顺手重取这个查询，拿回一个 410 —— 一条没用
      // 的请求，外加控制台里一条红字。
      client.removeQueries({ queryKey: ["preview", token] });
      client.invalidateQueries({ queryKey: ["imports"] });
      client.invalidateQueries({ queryKey: ["summary"] });
      client.invalidateQueries({ queryKey: ["transactions"] });
      navigate(`/imports/${body.import_id}`);
    },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure.message : "没导进去。"),
  });

  // 预览没了（超时、被顶掉、服务重启过）。它是**曾经存在、现在过期**的东西，
  // 所以不是 404，也不该长得像「你找错了地址」。
  if (loadError instanceof ApiError && loadError.code === "preview_expired") {
    return (
      <div className="py-16 text-center">
        <p className="text-muted">{loadError.message}</p>
        <Link className="mt-3 inline-block text-accent underline" to="/import">
          回上传页 →
        </Link>
      </div>
    );
  }
  if (isPending) return <p className="text-muted">正在解析…</p>;
  if (loadError) return <ErrorText>{loadError.message}</ErrorText>;
  if (!data) return null;

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">导入预览</h1>

      <Card>
        <div className="flex flex-wrap items-baseline gap-3">
          <span className="font-medium">{data.filename}</span>
          <span className="text-sm text-muted">{Math.round(data.size_bytes / 1024)} KB</span>
          {data.detected ? (
            <Tag tone="accent">认出来了：{data.detected.name}</Tag>
          ) : (
            <Tag>没认出格式</Tag>
          )}
        </div>

        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm text-muted">
            <span>按哪份配置读</span>
            <select
              className="h-9 rounded-md border border-border bg-bg px-2 text-sm text-text"
              value={data.profile_name}
              disabled={change.isPending}
              onChange={(event) => {
                setError("");
                change.mutate(event.target.value);
              }}
            >
              {data.profiles.map((profile) => (
                <option key={profile.name} value={profile.name}>
                  {profile.title}
                </option>
              ))}
            </select>
          </label>
        </div>

        {data.notes.map((note) => (
          <Hint key={note}>{note}</Hint>
        ))}
        {data.error ? (
          <div className="mt-3">
            <ErrorText>{data.error}</ErrorText>
          </div>
        ) : null}
        {error ? (
          <div className="mt-3">
            <ErrorText>{error}</ErrorText>
          </div>
        ) : null}
        {data.error || !data.detected ? (
          <Hint>
            <Link className="text-accent underline" to="/profiles">
              到列映射里对一下列名 →
            </Link>
          </Hint>
        ) : null}
      </Card>

      {data.error && data.raw_preview.length ? (
        <Section title="文件开头的原始内容">
          {/* 认不出表头时把这个摊出来——这就是拿去改配置的信息。 */}
          <TableScroll>
            <Table>
              <TBody>
                {data.raw_preview.map((line) => (
                  <TR key={line.row_number}>
                    <TD className="w-10 text-muted">{line.row_number}</TD>
                    <TD className="whitespace-pre-wrap font-mono text-xs">
                      {line.cells.join(" | ")}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableScroll>
        </Section>
      ) : null}

      {data.error ? null : (
        <>
          <Section title="这一份会带来什么">
            <div className="flex flex-wrap gap-2 text-sm">
              <Tag tone="accent">新记录 {data.counts.new}</Tag>
              <Tag>已存在跳过 {data.counts.duplicate}</Tag>
              <Tag>可能重复 {data.counts.possible}</Tag>
              <Tag>不计收支 {data.counts.neutral}</Tag>
              {data.counts.hidden ? <Tag>其中隐藏 {data.counts.hidden}</Tag> : null}
              {data.counts.problem ? <Tag tone="out">有问题 {data.counts.problem}</Tag> : null}
            </div>
            <Hint>
              {data.totals.length === 0
                ? "没有要新增的收支。"
                : data.totals.length === 1
                  ? `将新增收入 ${formatAmount(data.totals[0].income)}、支出 ${formatAmount(
                      data.totals[0].expense,
                    )}（${data.totals[0].currency}）。`
                  : `将新增：${data.totals
                      .map(
                        (item) =>
                          `${item.currency} 收入 ${formatAmount(item.income)}、支出 ${formatAmount(
                            item.expense,
                          )}`,
                      )
                      .join("；")}。`}
            </Hint>
          </Section>

          {data.issues.length ? (
            <Section title={`要注意的地方（共 ${data.issues_total} 条）`}>
              <ul className="m-0 list-none p-0 text-sm">
                {data.issues.map((issue, index) => (
                  <li key={`${issue.code}-${index}`} className="py-1">
                    <span className={issue.level === "error" ? "text-danger" : "text-warn"}>
                      {issue.row_number ? `第 ${issue.row_number} 行：` : ""}
                      {issue.message}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}

          <Section
            title="逐行"
            hint={data.truncated ? `只列前 ${data.max_rows} 行，条数按全量算` : undefined}
          >
            <TableScroll>
              <Table>
                <THead>
                  <TR>
                    <TH>时间</TH>
                    <TH>对方 / 说明</TH>
                    <NumTH>金额</NumTH>
                    <TH>状态</TH>
                  </TR>
                </THead>
                <TBody>
                  {data.rows.map((row) => (
                    <TR key={row.row_number} className={row.duplicate ? "text-muted" : undefined}>
                      <TD className="whitespace-nowrap">{row.occurred_at.slice(0, 16)}</TD>
                      <TD>{row.counterparty || row.description}</TD>
                      <NumTD className={row.direction === "in" ? "text-in" : row.direction === "neutral" ? "text-muted" : "text-out"}>
                        {formatAmount(row.direction === "out" ? -row.amount_cents : row.amount_cents)}
                        {row.currency ? ` ${row.currency}` : ""}
                      </NumTD>
                      <TD>
                        {row.duplicate ? <Tag>已存在</Tag> : null}
                        {row.possible_duplicate ? <Tag>可能重复</Tag> : null}
                        {row.status === "hidden" ? <Tag>已隐藏</Tag> : null}
                        {row.status === "neutral" ? <Tag>不计收支</Tag> : null}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableScroll>
          </Section>
        </>
      )}

      {!data.error ? (
        <div className="mt-6 flex flex-wrap gap-2">
          <Button
            disabled={confirm.isPending || data.counts.new === 0}
            onClick={() => {
              setError("");
              confirm.mutate();
            }}
          >
            导入 {data.counts.new} 条新记录
          </Button>
          <Button
            variant="outline"
            onClick={async () => {
              await cancelPreview(token);
              navigate("/import");
            }}
          >
            取消
          </Button>
        </div>
      ) : null}
    </div>
  );
}
