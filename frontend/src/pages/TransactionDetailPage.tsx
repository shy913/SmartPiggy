import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, deleteTransaction, getTransaction, hideTransaction, unhideTransaction } from "../api/client";
import { Button } from "../components/ui/button";
import { Card, Section } from "../components/ui/card";
import { ErrorText, Tag } from "../components/ui/bits";
import { Table, TableScroll, TBody, TD, TH, TR } from "../components/ui/table";
import { formatAmount, minute } from "../lib/format";

/** 空值留个破折号：一片空白分不清「没有」和「没查出来」。 */
function show(value: unknown): string {
  const text = String(value ?? "").trim();
  return text === "" ? "—" : text;
}

/** 列表点过来时把原地址和「从哪儿来」一并带上，返回才能回到原来那一屏。 */
type BackState = { from?: string; backLabel?: string } | null;

export function TransactionDetailPage() {
  const { id } = useParams();
  const txnId = Number(id);
  const navigate = useNavigate();
  const location = useLocation();
  const nav = location.state as BackState;
  const client = useQueryClient();

  const { data, isPending, error } = useQuery({
    queryKey: ["transaction", txnId],
    queryFn: () => getTransaction(txnId),
  });

  const act = useMutation({
    mutationFn: async (action: "hide" | "unhide" | "delete") => {
      if (action === "hide") return hideTransaction(txnId);
      if (action === "unhide") return unhideTransaction(txnId);
      return deleteTransaction(txnId);
    },
    onSuccess: (_result, action) => {
      client.invalidateQueries();
      // 删完仍回到原来那个列表，筛选不丢；行没了，位置恢复会自然落空。
      if (action === "delete") navigate(nav?.from ?? "/transactions");
    },
  });

  if (isPending) return <p className="text-muted">正在读取…</p>;
  if (error)
    return (
      <div>
        <ErrorText>{error instanceof ApiError ? error.message : "读不出来。"}</ErrorText>
        <Link
          className="mt-3 inline-block text-accent underline"
          to={nav?.from ?? "/transactions"}
        >
          ← {nav?.backLabel ?? "交易记录"}
        </Link>
      </div>
    );
  if (!data) return null;

  const tx = data.transaction;
  const hidden = tx.hidden === 1;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        {/* 从列表点进来时回到逐字相同的地址（筛选、limit 都在），位置和行由
            sessionStorage 里的锚点接上。直接打开这一页才落到这一笔**现在待的
            那个月**——它可能已经被并进别个月的组了。 */}
        <Link
          className="text-accent underline"
          to={nav?.from ?? `/transactions?start=${data.back_month}-01&end=`}
        >
          ← {nav?.backLabel ?? "交易记录"}
        </Link>
      </div>

      <h1 className="mb-4 text-xl font-semibold">这一笔</h1>

      <Card>
        <div className="flex flex-wrap items-baseline gap-3">
          <span className="tabular text-2xl">
            {formatAmount(tx.direction === "out" ? -tx.amount_cents : tx.amount_cents)}
          </span>
          <span className="text-muted">{tx.currency}</span>
          {tx.is_large ? <Tag>大额</Tag> : null}
          {hidden ? <Tag>已隐藏</Tag> : null}
          {tx.edited_at ? <Tag>手改过</Tag> : null}
        </div>
        <div className="mt-2 text-sm text-muted">{minute(tx.occurred_at)}</div>
      </Card>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          variant="outline"
          onClick={() =>
            // 把来路带给表单：改完要回的是原来那一屏，锚点才知道往哪儿搬。
            navigate(`/transactions/${txnId}/edit`, { state: nav })
          }
        >
          改一笔
        </Button>
        <Button
          variant="outline"
          disabled={act.isPending}
          onClick={() => act.mutate(hidden ? "unhide" : "hide")}
        >
          {hidden ? "还原" : "隐藏"}
        </Button>
        <Button
          variant="danger"
          disabled={act.isPending}
          onClick={() => {
            if (confirm("删掉这一笔？删了就找不回来了。")) act.mutate("delete");
          }}
        >
          删除
        </Button>
      </div>

      {act.error ? (
        <div className="mt-3">
          <ErrorText>{act.error instanceof ApiError ? act.error.message : "没成。"}</ErrorText>
        </div>
      ) : null}

      <Section title="明细">
        <TableScroll>
          <Table>
            <TBody>
              <Row label="交易对方" value={tx.counterparty} />
              <Row label="对方账号" value={tx.counterparty_account} />
              <Row label="商品说明" value={tx.description} />
              <Row label="备注" value={tx.memo} />
              <Row label="支付方式" value={tx.payment_method} />
              <Row label="交易状态" value={tx.status} />
              <Row label="账户" value={tx.account_name} />
              <Row label="分类" value={tx.effective_category} />
              <Row label="账单分类" value={tx.source_category} />
              <Row label="原金额" value={`${formatAmount(tx.amount_cents)} ${tx.currency}`} />
              <Row label="订单号" value={tx.source_txn_id} />
              <Row label="商户单号" value={tx.merchant_txn_id} />
            </TBody>
          </Table>
        </TableScroll>
      </Section>

      {data.record ? (
        <Section title="来自哪一次导入">
          <p className="text-sm">
            <Link className="text-accent underline" to={`/imports/${data.record.id}`}>
              {String(data.record.filename)}
            </Link>
            <span className="ml-2 text-muted">{String(data.record.uploaded_at ?? "")}</span>
          </p>
        </Section>
      ) : null}

      {Object.keys(data.raw).length ? (
        <Section title="账单原文">
          <TableScroll>
            <Table>
              <TBody>
                {Object.entries(data.raw).map(([key, value]) => (
                  <Row key={key} label={key} value={value} />
                ))}
              </TBody>
            </Table>
          </TableScroll>
        </Section>
      ) : null}
    </div>
  );
}

function Row({ label, value }: { label: string; value: unknown }) {
  return (
    <TR>
      <TH className="w-32 align-top text-muted">{label}</TH>
      <TD className="break-words">{show(value)}</TD>
    </TR>
  );
}
