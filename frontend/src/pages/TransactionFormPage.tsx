import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createTransaction,
  getTransaction,
  newTransactionForm,
  updateTransaction,
} from "../api/client";
import type { FormPayload, FormValues } from "../api/types";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { ErrorText, Field, Input } from "../components/ui/bits";
import { moveAnchor } from "../lib/anchor";

const BLANK: FormValues = {
  occurred_at: "",
  direction: "out",
  amount: "",
  currency: "",
  category: "",
  account_name: "",
  counterparty: "",
  counterparty_account: "",
  description: "",
  memo: "",
  payment_method: "",
};

/**
 * 记一笔 / 改一笔。
 *
 * 两张表单**共用**这一份：字段、校验、文案都在服务端（`entry.read`），这里只管
 * 把值摆上去、把错误显示出来。分开写两份的话，同一个「金额必须非负」迟早会
 * 有一边写成别的说法。
 */
export function TransactionFormPage() {
  const { id } = useParams();
  const editing = id !== undefined;
  const txnId = editing ? Number(id) : null;
  const navigate = useNavigate();
  const location = useLocation();
  const client = useQueryClient();
  // 从交易记录点进来时带着原列表地址（「记一笔」是列表传的，改一笔由详情页
  // 转交），取消或改完都回那一屏，筛选还在、锚点也认得。
  const returnTo = (location.state as { from?: string } | null)?.from;

  // 两个接口回的是**同一个形状**（改一笔那个也带 errors / accounts /
  // currencies / categories / back_month），所以这一份表单能同时喂两种用途。
  const { data, isPending, error } = useQuery<FormPayload>({
    queryKey: editing ? ["transaction", txnId] : ["transaction-form"],
    queryFn: async () => (editing ? await getTransaction(txnId!) : await newTransactionForm()),
  });

  const [values, setValues] = useState<FormValues | null>(null);
  const [errors, setErrors] = useState<string[]>([]);

  // 初值只取一次：用户改过的内容不该被后台重新取回来的数据盖掉。
  const current = values ?? (data ? ({ ...BLANK, ...data.values } as FormValues) : null);

  const submit = useMutation({
    mutationFn: () =>
      editing ? updateTransaction(txnId!, current as unknown as Record<string, string>)
              : createTransaction(current as unknown as Record<string, string>),
    onSuccess: (body) => {
      client.invalidateQueries();
      const monthList = `/transactions?start=${body.month}-01&end=`;
      if (editing) {
        // 改完要回去的是这一笔现在待的那个月。地址是刚拼出来的，跟离开列表时
        // 那一屏的地址往往对不上，锚点得跟着搬过去，回去才会定位并亮一下。
        // 还在合并组里的话，列表上是一行合并记录，目标得指向那一组。
        const target = body.group_id ? `group-${body.group_id}` : `txn-${txnId}`;
        moveAnchor(returnTo, monthList, target);
      }
      // 刚记的这笔要是立刻被并进了别个月的组，就跳它现在待的那一个月。
      navigate(monthList);
    },
    onError: (failure) => {
      if (failure instanceof ApiError) {
        setErrors([failure.message]);
        const echoed = (failure.body as { values?: FormValues } | null)?.values;
        if (echoed) setValues({ ...BLANK, ...echoed });
      } else {
        setErrors(["没存上。"]);
      }
    },
  });

  if (isPending) return <p className="text-muted">正在读取…</p>;
  if (error) return <ErrorText>读不出来。</ErrorText>;
  if (!data || !current) return null;

  const set = (key: keyof FormValues) => (event: { target: { value: string } }) =>
    setValues({ ...current, [key]: event.target.value });

  return (
    <div className="max-w-2xl">
      <h1 className="mb-4 text-xl font-semibold">{editing ? "改一笔" : "记一笔"}</h1>
      <Card>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            setErrors([]);
            submit.mutate();
          }}
        >
          <Field label="日期时间">
            <Input type="datetime-local" value={current.occurred_at} onChange={set("occurred_at")} />
          </Field>

          <div className="flex flex-wrap gap-4">
            <Field label="收支">
              <select
                className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
                value={current.direction}
                onChange={set("direction")}
              >
                <option value="out">支出</option>
                <option value="in">收入</option>
                <option value="neutral">不计收支</option>
              </select>
            </Field>
            <Field label="金额">
              <Input inputMode="decimal" value={current.amount} onChange={set("amount")} />
            </Field>
            <Field label="币种">
              <select
                className="h-9 rounded-md border border-border bg-bg px-2 text-sm"
                value={current.currency}
                onChange={set("currency")}
              >
                {data.currencies.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <Field label="账户">
            {/* 账户不给改：改了等于把这笔账搬到另一个来源下面，去重和覆盖范围
                都跟着变——那是重新导入该干的事，不是「改一笔」。 */}
            <Input value={current.account_name} readOnly className="text-muted" />
          </Field>

          <Field label="分类">
            <Input list="form-categories" value={current.category} onChange={set("category")} />
            <datalist id="form-categories">
              {data.categories.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
          </Field>

          <div className="flex flex-wrap gap-4">
            <Field label="交易对方" className="min-w-40 flex-1">
              <Input value={current.counterparty} onChange={set("counterparty")} />
            </Field>
            <Field label="对方账号" className="min-w-40 flex-1">
              <Input value={current.counterparty_account} onChange={set("counterparty_account")} />
            </Field>
          </div>

          <Field label="商品说明">
            <Input value={current.description} onChange={set("description")} />
          </Field>
          <Field label="备注">
            <Input value={current.memo} onChange={set("memo")} />
          </Field>
          <Field label="支付方式">
            <Input value={current.payment_method} onChange={set("payment_method")} />
          </Field>

          {errors.length ? (
            <ul className="m-0 list-none rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
              {errors.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={submit.isPending}>
              保存
            </Button>
            {/* 从列表点进来就回原来那一屏（筛选、limit 都在），锚点也还认得；
                只有直接打开这一页时才退回到这一笔现在待的那个月。 */}
            <Link
              className="inline-flex h-9 items-center rounded-md px-3 text-sm text-muted hover:text-text"
              to={
                returnTo ??
                (editing && data.back_month
                  ? `/transactions?start=${data.back_month}-01&end=`
                  : "/transactions")
              }
            >
              取消
            </Link>
          </div>
        </form>
      </Card>
    </div>
  );
}
