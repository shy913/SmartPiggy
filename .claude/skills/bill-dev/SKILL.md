---
name: bill-dev
description: 在本仓库（本地记账 Web 应用）上改代码时用。包含干活的循环、部署方式、项目约定，以及一组「改坏了不会报错」的不变量。改 bill 的任何功能、界面、模板、测试之前先读这份。
---

# 记账（bill）开发

本地 Flask + SQLite 记账应用：导入支付宝 / 微信 / 银行账单，记录成结构化收支。
后端只出 JSON（`api.py`），界面是一个 React SPA（`frontend/`）。

**README.md 是权威文档** —— 每一项口径、取舍和「为什么」都写在那儿，改功能前先读
相关那一节（币种、时区、大额、去重、手改、AA 合并、加银行）。这份技能只说
README 里没有的：怎么干活、怎么上线、哪里容易改坏。

## 干活的循环

1. **先进 worktree**（EnterWorktree）。在共享 checkout 里改会被拒。
2. 改代码。改到界面就在 `frontend/` 里改。
3. 两边都要绿：
   - `PYTHONPATH=src /root/bill/.venv/bin/python -m pytest`（**worktree 里必须带
     `PYTHONPATH=src`**，否则测的是 `/root/bill/src` 那份旧代码，全绿是假的）
   - `npm --prefix frontend test` 和 `npm --prefix frontend run build`
4. 落回 `/root/bill`、构建前端、重启服务（见下）。
5. `curl` 验一下真的起来了。

## 部署

**代码就跑在 `/root/bill` 本身**，不是从别处拷进去的。所以「部署」= 让改动出现在
`/root/bill` 的工作副本里 + **构建前端** + 重启进程。在 worktree 里干活时，
前两步是合并回去再跑 `./deploy.sh`。

**前端产物不进版本库**（`.gitignore` 里），要现构建：

```bash
cd /root/bill && ./deploy.sh --restart
```

它构建到旁边的目录、成了再换上去、并留着上一份（`app.last`）——构建失败时线上
那份原封不动。**别手动 `npm run build` 到 `app/`**：失败时那里会被留在半截状态，
而 `/healthz` 照样报活，等于白屏了探针却说没事。

服务是个**手起的后台进程**，没有 systemd：

```bash
ps -eo pid,cmd | grep "[v]env/bin/bill"     # 认准 PID（可能不止一个）
kill <pid>
cd /root/bill && setsid nohup .venv/bin/bill >> ~/.local/share/bill/bill.log 2>&1 < /dev/null &
sleep 2 && curl -s localhost:8001/healthz
```

**别用 `pkill -f 'venv/bin/bill'` 收这个进程。** 模式匹配认字符串不认进程：
`pkill -f '\.venv/bin/bill'` 会连你自己这条命令的 shell 一起杀掉（命令行里就带着
这串），剩下的部分根本不执行 —— 服务停在那儿没人起。**一律按 pid kill。**

`/healthz` 不需要登录，是唯一能拿来做存活检查的入口。日志追加在
`~/.local/share/bill/bill.log`。

**别把改动停在分支上。** 用户不合并分支 —— 改完就落上去、构建、重启、验。

### 界面能真渲染出来看

这台机器上有 playwright 的无头 chromium / firefox / webkit（`uv run --with playwright
python 脚本.py`），**能量尺寸、能截图**——不用再靠读 HTML 猜。改界面先量先截再说。
但**好不好看仍交给用户**，我要负责的是「尺寸对不对、有没有塌」这类能客观验证的事。

起冒烟实例（**别碰真账本**）：

```bash
BILL_DATA_DIR=<临时目录> BILL_DB=<临时目录>/bill.db PYTHONPATH=src \
  HOST=127.0.0.1 PORT=8099 /root/bill/.venv/bin/python -m bill.web
```

真账本要登录，所以别指望 curl 线上页面能看到内容。收工**按 PID kill**。

## 容易改坏、而且改坏了不报错的地方

这一节最该看。下面每一条，违反了都不会有任何报错，只会静静地产生一个错数。

1. **去重键和内容指纹绝不重算。** 编辑功能绝不能碰 `dedup_key` / `content_hash`。
   那两个字段是「这条记录的身份」，重算之后重新导入同一份账单时比不中指纹，同一笔钱
   会被再插一遍 —— 账翻倍，屏幕上什么都不说。账户、来源分类、订单号同理，一律不给改。
   （`tests/test_editing.py`、`tests/test_api_write.py::test_editing_never_touches_the_dedup_key`）
2. **统计一律从 `repo.py` 的 `_effective_cte()` 取数**（那是合并后的净额口径）；
   写入和去重一律看真实的 `transactions` 表。序列号、唯一索引、指纹都长在那张表上。
3. **接口里绝不出现浮点金额。** 金额一律整数最小单位，字段名以 `_cents` 结尾；
   汇率也走字符串。浮点一过 JSON 就可能变成 `32.500000000000004`，前端再加一次
   就偏了，而屏幕上什么都看不出来。`tests/api.py::assert_no_floats` 递归扫每个响应，
   新的取数函数记得挂上它。
4. **界面上不写实现说明。** 不放「金额已按汇率换算」「时间均已换算到 X 时区」这类话，
   也不写「为什么这样设计」的辩解 —— 那读起来像提交说明。口径在设置页上是**控件本身**。
   （`tests/test_settings.py::test_no_developer_notes_leak_into_the_interface`，
   扫的是前端源码，扫之前先剥掉注释——注释里是可以讲道理的）
5. **没配汇率的币种绝不按 1:1 混算。** 它们单独成组列出来。硬凑会得到一个不报错的错数，
   比多一个分块危险得多。大额阈值同理：比不了就不判。**按金额排序、排行这类功能
   一律按显示币种分块**，绝不跨币种比大小。
6. **中性行（不计收支）不计入任何合计**，但仍照常入库、列表里看得见。
7. **配色只有一个来源**：`frontend/src/theme/theme.json`。CSS 变量块由它生成
   （`npm run themes`，构建前自动跑），图表组件也直接读它。哪些主题存在由
   `themes.py` 说（校验要它），标签也在那边 —— 两边对不上的话
   `tests/test_theme_sources.py` 会挂。**别把颜色抄进 Python**。

## 项目约定

- **金额一律整数最小单位**（人民币的分、英镑的便士），跟币种无关。
- **原始数据不覆盖**：`amount_cents` / `currency` / `raw_json` / `source_category`
  一直留着。换算是**查询时**做的，所以改汇率、改阈值随时重算，历史数据不会被动过。
  手改的分类写在另一列 `category`，没改过就回落到账单原文。
- **注释和界面文案都是中文。** 注释解释「为什么」，不解释「是什么」；取舍讲清楚代价。
- **加一家银行不用碰 Python**，只写 `profiles/*.toml`。`min_columns` 别往上调。
- **测试样本用 `tests/factories.py` 现造**，不往仓库里塞二进制。真实账单含个人信息，
  放 `tests/fixtures/real/`（已在 `.gitignore` 里），**不要提交**。
- 提交信息写中文，一句说清改了什么。

## 数据与凭据

| 内容 | 路径 |
| --- | --- |
| 账本数据库 | `~/.local/share/bill/bill.db` |
| 设置（配色 / 汇率 / 时区 / 大额 / 合并） | `~/.local/share/bill/settings.toml` |
| 登录密码与会话密钥 | `~/.local/share/bill/auth.toml` |
| 用户自己的列映射 | `~/.local/share/bill/profiles/*.toml` |

`bill.db` 是**真实账本**，不是样例数据 —— 别拿它当测试靶子，别删 `auth.toml`。

**登录密码由用户自己设**：把 `uv run bill --set-password` 交给他自己跑，不要替他
生成密码。设完不用重启（每次请求现读）。没设密码 = 不设防，会有提示条挂在每页顶上。
