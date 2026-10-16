import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import { ApiError, listImports, uploadFile } from "../api/client";
import { Button } from "../components/ui/button";
import { Card, Section } from "../components/ui/card";
import { ErrorText, Hint } from "../components/ui/bits";
import { ImportsTable } from "./ImportsTable";

export function ImportPage() {
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement | null>(null);
  const [error, setError] = useState("");

  const recent = useQuery({ queryKey: ["imports", 8], queryFn: () => listImports(8) });

  const upload = useMutation({
    mutationFn: (file: File) => uploadFile(file),
    onSuccess: (preview) => {
      // token 写进地址：预览页有自己的地址，刷新、后退、存书签都回得来。
      navigate(`/import/preview/${preview.token}`);
    },
    onError: (failure) =>
      setError(failure instanceof ApiError ? failure.message : "传不上去。"),
  });

  const pick = (file: File | undefined) => {
    if (!file) return;
    setError("");
    upload.mutate(file);
  };

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">导入账单</h1>

      <Card>
        <div
          className="rounded-lg border border-dashed border-border px-6 py-10 text-center"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            pick(event.dataTransfer.files[0]);
          }}
        >
          <p className="mb-3 text-sm text-muted">
            把账单文件拖进来，或者选一个。支付宝 / 微信 / 银行流水的格式都认。
          </p>
          <input
            ref={input}
            type="file"
            className="hidden"
            accept=".csv,.txt,.xlsx,.xls,.zip"
            onChange={(event) => pick(event.target.files?.[0])}
          />
          <Button onClick={() => input.current?.click()} disabled={upload.isPending}>
            {upload.isPending ? "正在读…" : "选一个文件"}
          </Button>
        </div>
        {error ? (
          <div className="mt-3">
            <ErrorText>{error}</ErrorText>
          </div>
        ) : null}
      </Card>

      <Hint>
        认不出这份格式？{" "}
        <Link className="text-accent underline" to="/profiles">
          到列映射里对一下列名 →
        </Link>
      </Hint>

      <Section title="最近导入">
        {recent.data?.imports.length ? (
          <>
            <ImportsTable imports={recent.data.imports} />
            <Hint>
              <Link className="text-accent underline" to="/imports">
                看全部 →
              </Link>
            </Hint>
          </>
        ) : (
          <p className="text-muted">还没有导入过。</p>
        )}
      </Section>
    </div>
  );
}
