import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  dissolveGroup,
  listGroups,
  scanGroups,
  updateGroup,
} from "../api/client";
import type { GroupRow, MemberRow } from "../api/types";
import { MemberAmount } from "../components/MemberAmount";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Empty, ErrorText, Field, Hint, Input, Tag } from "../components/ui/bits";
import { formatAmount, minute } from "../lib/format";

export function GroupsPage() {
  const [params] = useSearchParams();
  const created = params.get("created");
  const client = useQueryClient();
  const [error, setError] = useState("");

  const { data, isPending, error: loadError } = useQuery({
    queryKey: ["groups"],
    queryFn: listGroups,
  });

  const scan = useMutation({
    mutationFn: scanGroups,
    onSuccess: (body) => {
      client.invalidateQueries();
      setError(body.created ? `新认出 ${body.created} 组。` : "没有新认出来的。");
    },
  });

  const dissolve = useMutation({
    mutationFn: dissolveGroup,
    onSuccess: () => client.invalidateQueries({ queryKey: ["groups"] }),
    onError: (failure) => setError(failure instanceof ApiError ? failure.message : "没取消成。"),
  });

  if (isPending) return <p className="text-muted">正在读取…</p>;
  if (loadError) return <ErrorText>{loadError.message}</ErrorText>;
  if (!data) return null;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">合并组</h1>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          disabled={scan.isPending}
          onClick={() => {
            setError("");
            scan.mutate();
          }}
        >
          重新扫一遍整个账本
        </Button>
      </div>

      {created ? <Hint>已合成一组。</Hint> : null}
      {error ? <ErrorText>{error}</ErrorText> : null}

      {data.groups.length === 0 ? (
        <Empty>
          还没有合并组。
          <Link className="ml-1 text-accent underline" to="/transactions">
            去交易记录勾选几笔 →
          </Link>
        </Empty>
      ) : (
        <div className="flex flex-col gap-4">
          {data.groups.map((group) => (
            <GroupCard
              key={group.id}
              group={group}
              members={data.members[String(group.id)] ?? []}
              displayCurrency={data.display_currency}
              onDissolve={() => dissolve.mutate(group.id)}
              onSaved={() => client.invalidateQueries({ queryKey: ["groups"] })}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function GroupCard({
  group,
  members,
  displayCurrency,
  onDissolve,
  onSaved,
}: {
  group: GroupRow;
  members: MemberRow[];
  displayCurrency: string;
  onDissolve: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(group.title);
  const [category, setCategory] = useState(group.category);
  const [note, setNote] = useState(group.note);
  const [error, setError] = useState("");

  const save = useMutation({
    mutationFn: () => updateGroup(group.id, { title, category, note }),
    onSuccess: onSaved,
    onError: (failure) => setError(failure instanceof ApiError ? failure.message : "没存上。"),
  });

  const effective = group.net_cents !== null;

  return (
    <Card>
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="tabular text-lg">
          {effective ? formatAmount(group.net_cents!) : "—"}
        </span>
        <span className="text-xs text-muted">{displayCurrency}</span>
        <span className="text-sm text-muted">{group.member_count} 笔 · {minute(group.first_at)}</span>
        <Tag>{group.origin === "manual" ? "手动合并" : "自动认出"}</Tag>
      </div>

      {effective ? null : (
        // 组里有一笔的币种没配汇率，净额算不出来，整组就不生效（也不在账本里）。
        // 说清缺哪个币种，比留一个空数字有用。
        <p className="mt-2 text-sm text-warn">
          这组没生效：{group.stuck || "有币种没配汇率"} 没配汇率，净额算不出来。
        </p>
      )}

      <details className="mt-3">
        <summary className="cursor-pointer text-sm text-muted">
          展开这 {group.member_count} 笔
        </summary>
        <ul className="m-0 mt-2 list-none p-0 text-sm">
          {members.map((member) => (
            <li key={member.id} className="flex flex-wrap items-center gap-3 py-1">
              <span className="text-muted">{minute(member.occurred_at)}</span>
              <Link className="text-accent underline" to={`/transactions/${member.id}`}>
                {member.counterparty || member.description}
              </Link>
              <MemberAmount member={member} displayCurrency={displayCurrency} />
            </li>
          ))}
        </ul>
      </details>

      <form
        className="mt-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          setError("");
          save.mutate();
        }}
      >
        <Field label="这一组叫什么">
          <Input value={title} onChange={(event) => setTitle(event.target.value)} />
        </Field>
        <Field label="分类">
          <Input value={category} onChange={(event) => setCategory(event.target.value)} />
        </Field>
        <Field label="备注">
          <Input value={note} onChange={(event) => setNote(event.target.value)} />
        </Field>
        <Button type="submit" variant="outline" size="sm" disabled={save.isPending}>
          保存
        </Button>
        <Button
          type="button"
          variant="danger"
          size="sm"
          onClick={() => {
            if (confirm("取消这一组？这几笔会各自回到账本里。")) onDissolve();
          }}
        >
          取消合并
        </Button>
      </form>

      {error ? (
        <div className="mt-2">
          <ErrorText>{error}</ErrorText>
        </div>
      ) : null}
    </Card>
  );
}
