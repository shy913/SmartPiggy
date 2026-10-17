import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  dissolveGroup,
  listGroups,
  scanGroups,
  updateGroup,
} from "../api/client";
import type { GroupRow, MemberRow } from "../api/types";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Empty, ErrorText, Field, Hint, Input, Tag } from "../components/ui/bits";
import { anchorKey, saveAnchor } from "../lib/anchor";
import { formatAmount, minute } from "../lib/format";
import { useFlash } from "../lib/useFlash";
import { useReturnAnchor } from "../lib/useReturnAnchor";
import { cn } from "../lib/utils";

export function GroupsPage() {
  const [params] = useSearchParams();
  const created = params.get("created");
  // 交易记录页点「管理」带过来的组：落到那一张卡片上。
  const focusGroup = Number(params.get("group")) || null;
  const client = useQueryClient();
  const [error, setError] = useState("");
  const [openGroups, setOpenGroups] = useState<Set<number>>(new Set());

  const { data, isPending, error: loadError } = useQuery({
    queryKey: ["groups"],
    queryFn: listGroups,
  });

  // 成员 id -> 组 id。返回时靠它知道该展开哪一组。
  const memberGroups = useMemo(() => {
    const map = new Map<number, number>();
    for (const [groupId, members] of Object.entries(data?.members ?? {})) {
      for (const member of members) map.set(member.id, Number(groupId));
    }
    return map;
  }, [data]);

  const ready = !isPending && !!data;
  const anchorTarget = useReturnAnchor(ready, (anchor) => {
    if (!anchor.target?.startsWith("member-")) return;
    const memberId = Number(anchor.target.slice("member-".length));
    const groupId = memberGroups.get(memberId);
    if (groupId !== undefined) {
      setOpenGroups((current) => new Set(current).add(groupId));
    }
  });
  const flashTarget = anchorTarget ?? (focusGroup ? `group-${focusGroup}` : null);
  const flashed = useFlash(flashTarget);

  // 从交易记录跳过来：组页不分页，目标卡片一定渲染出来了，直接居中。
  useEffect(() => {
    if (!ready || focusGroup === null) return;
    document
      .querySelector(`[data-anchor-id="group-${focusGroup}"]`)
      ?.scrollIntoView?.({ block: "center" });
  }, [ready, focusGroup]);

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

  const flashMemberId = flashTarget?.startsWith("member-")
    ? Number(flashTarget.slice("member-".length))
    : null;

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
          {data.groups.map((group) => {
            const groupFlashed =
              flashed &&
              (flashTarget === `group-${group.id}` ||
                (flashMemberId !== null && memberGroups.get(flashMemberId) === group.id));
            return (
              <div key={group.id} data-anchor-id={`group-${group.id}`}>
                <GroupCard
                  group={group}
                  members={data.members[String(group.id)] ?? []}
                  displayCurrency={data.display_currency}
                  open={openGroups.has(group.id)}
                  onToggle={(open) =>
                    setOpenGroups((current) => {
                      const next = new Set(current);
                      if (open) next.add(group.id);
                      else next.delete(group.id);
                      return next;
                    })
                  }
                  highlight={groupFlashed}
                  highlightMemberId={flashed ? flashMemberId : null}
                  onDissolve={() => dissolve.mutate(group.id)}
                  onSaved={() => client.invalidateQueries({ queryKey: ["groups"] })}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function GroupCard({
  group,
  members,
  displayCurrency,
  open,
  onToggle,
  highlight,
  highlightMemberId,
  onDissolve,
  onSaved,
}: {
  group: GroupRow;
  members: MemberRow[];
  displayCurrency: string;
  open: boolean;
  onToggle: (open: boolean) => void;
  highlight: boolean;
  highlightMemberId: number | null;
  onDissolve: () => void;
  onSaved: () => void;
}) {
  const location = useLocation();
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
    <Card
      data-flash={highlight ? "1" : undefined}
      className={cn(
        "transition-colors duration-700",
        highlight && "border-accent/50 bg-accent/5",
      )}
    >
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

      <details
        className="mt-3"
        open={open}
        onToggle={(event) => onToggle(event.currentTarget.open)}
      >
        <summary className="cursor-pointer text-sm text-muted">
          展开这 {group.member_count} 笔
        </summary>
        <ul className="m-0 mt-2 list-none p-0 text-sm">
          {members.map((member) => (
            <li
              key={member.id}
              data-anchor-id={`member-${member.id}`}
              data-flash={highlightMemberId === member.id ? "1" : undefined}
              className={cn(
                "flex flex-wrap items-center gap-3 py-1 transition-colors duration-700",
                highlightMemberId === member.id && "bg-accent/10",
              )}
            >
              <span className="text-muted">{minute(member.occurred_at)}</span>
              <Link
                className="text-accent underline"
                to={`/transactions/${member.id}`}
                state={{ from: `${location.pathname}${location.search}`, backLabel: "合并组" }}
                onClick={() =>
                  saveAnchor(anchorKey(location.pathname, location.search), `member-${member.id}`)
                }
              >
                {member.counterparty || member.description}
              </Link>
              <span className="tabular">
                {formatAmount(member.direction === "out" ? -member.display_cents : member.display_cents)}
                {member.currency !== displayCurrency ? ` ${member.currency}` : ""}
              </span>
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
