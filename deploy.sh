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

  # `setsid --fork` 里的 `--fork` **不能省**。少了它，setsid 常常直接 exec 成
  # 那个服务进程——于是服务成了本脚本的子进程，脚本走到末尾就挂在 wait() 上
  # 不退（踩过：产物早换好了、页面也好了，但脚本一直不返回，管到 tail 的输出
  # 再也不吐）。--fork 保证中间隔一层，setsid 立刻退出，服务被 init 收养。
  setsid --fork "$ROOT/.venv/bin/bill" >> "$HOME/.local/share/bill/bill.log" 2>&1 < /dev/null &

  # 起来要一小会儿：轮询几次再下结论，别 sleep 两秒就断言。
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8001/healthz || true)"
    [ "$code" = "200" ] && break
    sleep 1
  done
  echo "healthz ${code:-连不上}"
  if [ "${code:-}" != "200" ]; then
    echo "服务没起来，看看 $HOME/.local/share/bill/bill.log" >&2
    exit 1
  fi
fi
