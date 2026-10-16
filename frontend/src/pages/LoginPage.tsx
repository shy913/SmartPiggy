import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, login } from "../api/client";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { ErrorText, Field, Input } from "../components/ui/bits";

export function LoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const client = useQueryClient();

  const submit = useMutation({
    mutationFn: () => login(password),
    onSuccess: () => {
      setPassword("");
      setError("");
      // 会话变了，前面那些「没登录」的查询结果全都过期了。
      client.invalidateQueries();
    },
    onError: (failure) => {
      setError(failure instanceof ApiError ? failure.message : "登录失败，再试一次。");
    },
  });

  return (
    <div className="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-4">
      <Card>
        <h1 className="mb-4 text-lg font-semibold">登录</h1>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            submit.mutate();
          }}
        >
          <Field label="密码">
            <Input
              type="password"
              name="password"
              autoFocus
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
          <ErrorText>{error}</ErrorText>
          <Button type="submit" disabled={submit.isPending}>
            进去
          </Button>
        </form>
      </Card>
    </div>
  );
}
