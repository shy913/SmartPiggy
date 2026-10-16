import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getProfiles } from "../api/client";
import { Card, Section } from "../components/ui/card";
import { ErrorText, Hint } from "../components/ui/bits";

export function ProfilesPage() {
  const { data, isPending, error } = useQuery({
    queryKey: ["profiles"],
    queryFn: getProfiles,
  });

  if (isPending) return <p className="text-muted">正在读取…</p>;
  if (error) return <ErrorText>{error.message}</ErrorText>;
  if (!data) return null;

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">列映射</h1>

      {data.staged ? (
        <Hint>
          刚读过「{data.staged.filename}」。下面列的是**实际读到的表头**，照着它改配置最快。
        </Hint>
      ) : null}

      {data.headers.length ? (
        <Section title="刚读到的那份文件的真实表头">
          <p className="font-mono text-sm break-words">{data.headers.join(" | ")}</p>
        </Section>
      ) : null}

      <Section title="现有的配置">
        <div className="flex flex-col gap-3">
          {data.profiles.map((profile) => (
            <Card key={profile.name}>
              <div className="flex flex-wrap items-baseline gap-3">
                <span className="font-medium">{profile.title}</span>
                <span className="font-mono text-xs text-muted">{profile.name}</span>
                <span className="text-xs text-muted">{profile.origin}</span>
              </div>
              {profile.problems.length ? (
                <ul className="m-0 mt-2 list-none p-0 text-sm text-warn">
                  {profile.problems.map((problem) => (
                    <li key={problem}>{problem}</li>
                  ))}
                </ul>
              ) : null}
            </Card>
          ))}
        </div>
      </Section>

      <Section title="配置放在哪">
        <p className="text-sm text-muted">你自己的：{data.user_dir}</p>
        <p className="text-sm text-muted">内置的：{data.builtin_dir}</p>
        <Hint>
          加一家银行不用改代码，写一份 TOML 放到上面那个自己的目录里就行。
        </Hint>
      </Section>

      {/* 这一页不在主导航里，得留条路回去。 */}
      <Hint>
        <Link className="text-accent underline" to="/settings">
          回设置 →
        </Link>
        {"　"}
        <Link className="text-accent underline" to="/import">
          回导入 →
        </Link>
      </Hint>
    </div>
  );
}
