#!/usr/bin/env bash
#
# 构建前端并把产物换上。
#
# 为什么不是「直接 build 到 app/」：构建失败时，`app/` 会被上一次的中间状态
# 覆盖或者清空，而 `/healthz` 照样报活——**线上变成一片白屏，探针却说着没事**。
# 所以先构建到一个旁边目录，成了再换上去，并且留着上一份。
#
# 用法：  ./deploy.sh          （构建并换上）
#         ./deploy.sh --restart （顺带按 PID 重启服务）
#
# 注意 `npm ci` 需要能连上 registry（这台机器上走的是镜像，见 README）。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
NODE_BIN="${NODE_BIN:-/opt/node/bin}"
APP="$ROOT/src/bill/static/app"
NEXT="$ROOT/src/bill/static/app.next"
LAST="$ROOT/src/bill/static/app.last"

if [ ! -x "$NODE_BIN/npm" ]; then
  echo "找不到 npm（$NODE_BIN/npm）。用 NODE_BIN=… 指一下。" >&2
  exit 1
fi

echo "==> 装依赖"
(cd "$ROOT/frontend" && "$NODE_BIN/npm" ci)

echo "==> 构建到旁边的目录（线上那份先不动）"
rm -rf "$NEXT"
(cd "$ROOT/frontend" && "$NODE_BIN/npm" run build -- --outDir "$NEXT" --emptyOutDir)

if [ ! -f "$NEXT/index.html" ]; then
  echo "构建出来了但没有 index.html，不换。" >&2
  exit 1
fi

echo "==> 换上去（先把现在这份留成 app.last）"
rm -rf "$LAST"
[ -d "$APP" ] && mv "$APP" "$LAST"
mv "$NEXT" "$APP"

echo "==> 换好了：$APP"
ls -la "$APP/assets" | tail -n +2

if [ "${1:-}" = "--restart" ]; then
  echo "==> 重启服务"
  # **按 PID kill，绝不 pkill -f**：那条模式会连这条命令自己的 shell、或者
  # 线上那个服务一起杀掉（两个都踩过）。
  pid="$(ps -eo pid,cmd | grep "[v]env/bin/bill" | awk '{print $1}' | head -n 1)"
  if [ -n "$pid" ]; then
    kill "$pid"
    sleep 1
  fi
  (cd "$ROOT" && setsid nohup .venv/bin/bill >> "$HOME/.local/share/bill/bill.log" 2>&1 < /dev/null &)
  sleep 2
  curl -s -o /dev/null -w 'healthz %{http_code}\n' http://127.0.0.1:8001/healthz
fi
