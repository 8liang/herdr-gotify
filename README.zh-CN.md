# herdr-gotify

[English](README.md) · **简体中文**

[Herdr](https://herdr.dev) 插件：当 agent pane 变为 **blocked**（等待你的输入）
或 **done**（后台工作完成）时，向你的手机推送一条 [Gotify](https://gotify.net/)
通知。idle / working / unknown 状态转换会被忽略，因此不会轰炸你。

可选地，开启摘要模式后，hook 会读取 pane 最近输出（`herdr pane read`）、交给
大模型生成一句话摘要，并把摘要作为通知正文推送——这样你手机上显示的是
*agent 实际做了什么*，而不只是「它完成了」。

纯 Bash 事件 hook——无需构建，除 `bash`、`python3`、`curl` 外无任何依赖。
支持 macOS 和 Linux。

## 工作原理

插件清单注册了一个事件 hook：

```toml
[[events]]
on = "pane.agent_status_changed"
command = ["bash", "./notify.sh"]
```

每当 pane 的 agent 状态变化时，Herdr 以插件目录为工作目录运行 `notify.sh`，
并注入：

- `HERDR_PLUGIN_EVENT_JSON` — 事件负载（`pane_id`、`workspace_id`、
  `agent_status`、`agent`、`display_agent`、`title`、`state_labels`）。
- `HERDR_PLUGIN_CONTEXT_JSON` — 友好上下文，如 `workspace_label` 与
  `tab_label`，用于让消息显示为 `paopaocha · trade-api` 而非 `w3 · w3:p2`。
- `HERDR_PLUGIN_CONFIG_DIR`、`HERDR_PLUGIN_ROOT`、`HERDR_BIN_PATH` 等——参见
  [插件文档](https://herdr.dev/docs/plugins/)。

对于 `blocked`/`done` 以外的任何状态，以及当 `config.env` 和环境变量都没有提供
`GOTIFY_URL` 与 `GOTIFY_TOKEN` 时，`notify.sh` 都会立即退出。

当 `SUMMARY_ENABLED=true` 时，blocked/done 事件的流程为：

```text
状态 -> blocked/done
  -> notify.sh 从 HERDR_PLUGIN_EVENT_JSON 读取 pane_id
  -> herdr pane read <pane_id> --source recent-unwrapped --lines N
  -> pane 输出尾部 + 任务标题 -> summarize.py -> 大模型
  -> 一句话摘要成为 Gotify 消息正文
```

摘要的每一步都是尽力而为：读不到 pane、缺少 API key、模型端点不可达、超时或
模型返回空，都会回退为普通消息，因此通知永远不会丢失。

## 安装

要求 macOS 或 Linux 上的 Herdr ≥ 0.7.0，以及运行 Herdr server 的机器上有
`curl`。`python3` 仅用于解析事件 JSON；缺失时脚本会静默退化为无操作。

从 GitHub 安装（该插件也以 `herdr-plugin` 主题收录在
[Herdr marketplace](https://herdr.dev/plugins/) 中）：

```sh
herdr plugin install 8liang/herdr-gotify
```

Herdr 会克隆仓库、注册插件并创建其配置目录。然后在 Herdr 打印的目录中创建
配置文件：

```sh
CONFIG_DIR="$(herdr plugin config-dir 8liang.herdr-gotify)"
cp config.env.example "$CONFIG_DIR/config.env"
chmod 600 "$CONFIG_DIR/config.env"
$EDITOR "$CONFIG_DIR/config.env"
```

至少填写：

```sh
GOTIFY_URL="https://gotify.example.com"
GOTIFY_TOKEN="your-application-token"
```

在 Gotify 的 **Apps** → *Create Application* 中创建 token。它只授予该应用
推送权限。

确认插件已启用：

```sh
herdr plugin list
```

### 针对 0.8.2 之前的 Herdr 版本

如果你的 Herdr 版本早于提供插件配置目录的版本，请把 `config.env.example`
复制为 `notify.sh` 旁边的 `config.env`（hook 会把 `./config.env` 作为开发
回退读取）。

### 本地开发

在开发插件本身时，用 link 本地 checkout 代替安装：

```sh
herdr plugin link /path/to/herdr-gotify
```

本地 link 的插件必须先 unlink，`herdr plugin install` 才能管理同一插件 id：

```sh
herdr plugin unlink 8liang.herdr-gotify
```

## 测试

首先确认 Gotify 本身能接受推送：

```sh
curl -X POST \
  "https://gotify.example.com/message?token=YOUR_TOKEN" \
  -F "title=Herdr Test" \
  -F "message=Gotify 通知测试" \
  -F "priority=5"
```

然后用合成事件端到端演练 hook（无需真实 Herdr server——hook 只读取环境变量
并通过 HTTP 推送）：

```sh
cd /path/to/herdr-gotify
export HERDR_PLUGIN_CONFIG_DIR="$(mktemp -d)"
cp config.env.example "$HERDR_PLUGIN_CONFIG_DIR/config.env"
# 编辑 URL/token，然后：
HERDR_PLUGIN_EVENT_JSON='{"event":"pane_agent_status_changed","data":{"pane_id":"w1:p2","workspace_id":"w1","agent_status":"blocked","display_agent":"claude","title":"Implement pet trading API"}}' \
HERDR_PLUGIN_CONTEXT_JSON='{"workspace_label":"paopaocha","tab_label":"trade-api"}' \
bash notify.sh
```

在 Herdr 的插件日志中查看失败：

```sh
herdr plugin log list --plugin 8liang.herdr-gotify
```

## 配置

所有设置都可选；下表为默认值。把覆盖项放入插件配置目录下的 `config.env`
（由 `herdr plugin config-dir 8liang.herdr-gotify` 打印），或导出到 Herdr
server 进程的环境中。环境变量优先于 `config.env`。

| 变量                       | 默认值   | 作用                                                        |
| -------------------------- | -------- | ----------------------------------------------------------- |
| `GOTIFY_URL`               | —        | Gotify server 基础 URL，如 `https://gotify.example.com`。推送必需。 |
| `GOTIFY_TOKEN`             | —        | Gotify 应用 token。推送必需。                                |
| `GOTIFY_PRIORITY_BLOCKED`  | `8`      | blocked 通知的优先级（0–10）。                                |
| `GOTIFY_PRIORITY_DONE`     | `5`      | done 通知的优先级（0–10）。                                   |
| `NOTIFY_BLOCKED`           | `true`   | 设为 `false` 停止 blocked 通知。                             |
| `NOTIFY_DONE`              | `true`   | 设为 `false` 停止 done 通知。                                |
| `GOTIFY_ALSO_HERDR_NOTIFY` | `false`  | 同时显示本地 Herdr 桌面通知（`herdr notification show`）。    |
| `SUMMARY_ENABLED`          | `false`  | 设为 `true` 后总结 pane 最近输出并把摘要作为正文推送。        |
| `SUMMARY_PROTOCOL`         | `openai` | 大模型 API 协议：`openai`（OpenAI 兼容 chat/completions）或 `anthropic`（`/v1/messages`）。 |
| `SUMMARY_API_URL`          | —        | 模型服务基础 URL（如 `https://api.deepseek.com`）；自动追加 endpoint 路径。 |
| `SUMMARY_API_KEY`          | —        | 模型 API key（以 `Authorization: Bearer` 发送；Anthropic 用 `x-api-key`）。 |
| `SUMMARY_MODEL`            | —        | 模型名，如 `deepseek-chat`、`gpt-4o-mini`、`claude-3-5-haiku-latest`。 |
| `SUMMARY_LINES`            | `60`     | 为摘要读取的 pane 最近输出行数。                              |
| `SUMMARY_MAX_CHARS`        | `6000`   | 实际发送给模型的 pane 输出字符数。                            |
| `SUMMARY_TIMEOUT_MS`       | `30000`  | 大模型请求超时；超时/失败时改发普通消息。                     |
| `SUMMARY_LANG`             | `zh`     | 摘要语言：`zh` 或 `en`（用于内置提示词）。                    |
| `SUMMARY_PROMPT`           | —        | 自定义 system 提示词；留空则按 `SUMMARY_LANG` 使用内置提示词。 |

### 策略说明

Herdr 报告五种 agent 状态：`idle`、`working`、`blocked`、`done`、`unknown`。
`idle` 和 `unknown` 可能频繁出现，而 `done` 正是对它们的汇总，因此本插件
只在以下状态通知：

```text
blocked → 优先级 8
done    → 优先级 5
idle    → 不通知
working → 不通知
unknown → 不通知
```

### AI 摘要（可选）

设 `SUMMARY_ENABLED=true` 即可把 blocked/done 通知的正文替换为一句关于 agent
所做工作的摘要。hook 通过 Herdr socket API 读取 pane 最近输出尾部，经由
`summarize.py`（Python 3 标准库，无额外依赖）交给大模型，然后推送摘要。
支持两种协议：

- `SUMMARY_PROTOCOL=openai` — OpenAI 兼容 `chat/completions`；覆盖 OpenAI、
  DeepSeek、Qwen/DashScope、Ollama 及大多数其他网关。
- `SUMMARY_PROTOCOL=anthropic` — Anthropic `/v1/messages`。

`SUMMARY_API_URL` 是 *基础 URL*；endpoint 路径会自动追加
（`/v1/chat/completions` 或 `/v1/messages`）。DeepSeek 示例：

```sh
SUMMARY_ENABLED=true
SUMMARY_PROTOCOL=openai
SUMMARY_API_URL=https://api.deepseek.com
SUMMARY_API_KEY=sk-xxxxxxxx
SUMMARY_MODEL=deepseek-chat
```

摘要请求在 hook 内同步运行（受 `SUMMARY_TIMEOUT_MS` 限制），因此你收到的通知
已经包含摘要。由于 hook 是尽力而为的，通知*绝不会*丢失——任何失败（无
pane id、缺少配置、读不到 pane 输出、网络或超时、模型返回空）都会回退为普通
消息并记入插件日志。注意：在终端 alternate screen 上产生的输出可能无法通过
`herdr pane read` 读回；此时会使用普通消息。

## 文件

| 路径                    | 用途                                                            |
| ----------------------- | --------------------------------------------------------------- |
| `herdr-plugin.toml`     | 清单：注册 `pane.agent_status_changed` 事件 hook。               |
| `notify.sh`             | Hook：读取事件、过滤、（可选）摘要、推送到 Gotify。              |
| `summarize.py`          | 可选的大模型摘要器，`SUMMARY_ENABLED=true` 时使用。              |
| `config.env.example`    | 带文档说明的配置模板。                                            |

## 故障排查

**收不到通知。** 先确认 Gotify 能接受手动推送（见「测试」）。然后查看插件日志。
每条记录显示命令、退出码与捕获的 stdout/stderr，因此 curl 失败会以 hook 的
stderr 行呈现：

```sh
herdr plugin log list --plugin 8liang.herdr-gotify
```

缺少 `GOTIFY_URL`/`GOTIFY_TOKEN` 会在那里记录为跳过（exit 0），而非错误。

**密钥。** 永远不要提交 `config.env`。本地的那个已被 git 忽略；真正的配置位于
Herdr 插件配置目录（见「安装」），在仓库之外。
