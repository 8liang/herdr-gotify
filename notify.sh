#!/usr/bin/env bash
#
# Herdr Gotify Notifications — event hook for pane.agent_status_changed.
#
# Herdr runs this with the plugin directory as its working directory and
# injects HERDR_PLUGIN_EVENT_JSON (the event payload), HERDR_PLUGIN_CONTEXT_JSON
# (workspace/tab/pane context), HERDR_PLUGIN_CONFIG_DIR and friends.
#
# It posts to Gotify when an agent turns blocked or done; every other status
# (idle / working / unknown) is ignored to avoid notification spam.

set -u

# =========================
# 配置加载
# =========================
#
# Precedence (later wins): config.env next to this script (plugin root, local
# development), then $HERDR_PLUGIN_CONFIG_DIR/config.env (installed plugin).
# A variable already present in the environment is left untouched.

CONFIG="${HERDR_PLUGIN_CONFIG_DIR:-}"
CONFIG_FILE="$(dirname "$0")/config.env"

if [ -n "$CONFIG" ] && [ -f "$CONFIG/config.env" ]; then
    CONFIG_FILE="$CONFIG/config.env"
fi

if [ -f "$CONFIG_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
    set +a
fi

# Everything below is optional; the values here are the defaults.

GOTIFY_URL="${GOTIFY_URL:-}"
GOTIFY_TOKEN="${GOTIFY_TOKEN:-}"

GOTIFY_PRIORITY_BLOCKED="${GOTIFY_PRIORITY_BLOCKED:-8}"
GOTIFY_PRIORITY_DONE="${GOTIFY_PRIORITY_DONE:-5}"

NOTIFY_BLOCKED="${NOTIFY_BLOCKED:-true}"
NOTIFY_DONE="${NOTIFY_DONE:-true}"

# Show a local Herdr desktop notification before sending to Gotify.
GOTIFY_ALSO_HERDR_NOTIFY="${GOTIFY_ALSO_HERDR_NOTIFY:-false}"

# Herdr binary used for `herdr notification show`; HERDR_BIN_PATH is injected.
HERDR_BIN="${HERDR_BIN_PATH:-herdr}"

# =========================
# 工具函数
# =========================

read_json() {
    python3 -c '
import json, os, sys

raw = os.environ.get(sys.argv[1], "")
try:
    data = json.loads(raw)
except Exception:
    sys.exit(0)

value = data
for key in sys.argv[2].split("."):
    if isinstance(value, dict) and key in value:
        value = value[key]
    else:
        sys.exit(0)

if isinstance(value, str):
    print(value)
elif isinstance(value, (int, float)):
    print(value)
elif value is True:
    print("true")
elif value is False:
    print("false")
' "$1" "$2"
}

# json_field "<ENV_NAME>" "<dotted.path>" — first match wins.
json_field() {
    local value
    value="$(read_json "$1" "$2")" || true
    printf '%s' "$value"
}

lower() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

first_nonempty() {
    local value
    for value in "$@"; do
        [ -n "$value" ] && { printf '%s' "$value"; return 0; }
    done
    return 0
}

# =========================
# 事件解析
# =========================

EVENT="${HERDR_PLUGIN_EVENT_JSON:-}"
[ -z "$EVENT" ] && exit 0
[ "$EVENT" = "{}" ] && exit 0

# Event payloads changed shape across Herdr releases; accept the layouts we
# know about. The bus event has { event, data: { agent_status, ... } }; older
# hooks placed the fields at the top level.

STATUS="$(json_field HERDR_PLUGIN_EVENT_JSON "data.agent_status")"
[ -z "$STATUS" ] && STATUS="$(json_field HERDR_PLUGIN_EVENT_JSON "agent_status")"
[ -z "$STATUS" ] && STATUS="$(json_field HERDR_PLUGIN_EVENT_JSON "status")"

STATUS="$(lower "$STATUS")"

# Only blocked and done are worth a push notification.
case "$STATUS" in
    blocked) [ "$NOTIFY_BLOCKED" = "true" ] || exit 0 ;;
    done)    [ "$NOTIFY_DONE" = "true" ] || exit 0 ;;
    *)       exit 0 ;;
esac

# Context (workspace/tab/agent) may be nested in the event too.
AGENT="$(json_field HERDR_PLUGIN_EVENT_JSON "data.display_agent")"
[ -z "$AGENT" ] && AGENT="$(json_field HERDR_PLUGIN_EVENT_JSON "data.agent")"
[ -z "$AGENT" ] && AGENT="$(json_field HERDR_PLUGIN_EVENT_JSON "agent")"

PANE_ID="$(json_field HERDR_PLUGIN_EVENT_JSON "data.pane_id")"
[ -z "$PANE_ID" ] && PANE_ID="$(json_field HERDR_PLUGIN_EVENT_JSON "pane_id")"

WORKSPACE_ID="$(json_field HERDR_PLUGIN_EVENT_JSON "data.workspace_id")"
[ -z "$WORKSPACE_ID" ] && WORKSPACE_ID="$(json_field HERDR_PLUGIN_EVENT_JSON "workspace_id")"

TITLE_JSON="$(json_field HERDR_PLUGIN_EVENT_JSON "data.title")"
[ -z "$TITLE_JSON" ] && TITLE_JSON="$(json_field HERDR_PLUGIN_EVENT_JSON "title")"

# HERDR_PLUGIN_CONTEXT_JSON carries the friendly labels (workspace_label,
# tab_label, focused_pane_agent, ...). Use them when present so the phone shows
# "paopaocha · trade-api" instead of "w3 · w3:p2".
CTX="${HERDR_PLUGIN_CONTEXT_JSON:-}"

WORKSPACE_LABEL="$(json_field HERDR_PLUGIN_CONTEXT_JSON "workspace_label")"
TAB_LABEL="$(json_field HERDR_PLUGIN_CONTEXT_JSON "tab_label")"

# Agent label: event agent → context focused pane agent → fallback.
CTX_AGENT="$(json_field HERDR_PLUGIN_CONTEXT_JSON "focused_pane_agent")"
[ -z "$AGENT" ] && AGENT="$CTX_AGENT"

# =========================
# 通知内容
# =========================

case "$STATUS" in
    blocked)
        EMOJI="⚠️"
        LABEL="等待输入"
        DESC="Agent 正在等待你的输入或确认。"
        PRIORITY="$GOTIFY_PRIORITY_BLOCKED"
        ;;
    done)
        EMOJI="✅"
        LABEL="已完成"
        DESC="任务已完成。"
        PRIORITY="$GOTIFY_PRIORITY_DONE"
        ;;
esac

AGENT_LABEL="$(first_nonempty "$AGENT" "agent")"

# Workspace/context line, e.g. "paopaocha · trade-api" or "w3 · w3:p2".
WHERE="$(first_nonempty "$WORKSPACE_LABEL" "$WORKSPACE_ID" "workspace")"
if [ -n "$TAB_LABEL" ]; then
    WHERE="${WHERE} · ${TAB_LABEL}"
elif [ -n "$PANE_ID" ]; then
    WHERE="${WHERE} · ${PANE_ID}"
fi

# Agent task title when the pane reported one.
TASK="$(first_nonempty "$TITLE_JSON")"

TITLE="${EMOJI} ${AGENT_LABEL} ${LABEL}"
MESSAGE="${WHERE}"
[ -n "$TASK" ] && MESSAGE="$(printf '%s\n\n%s' "$MESSAGE" "$TASK")"

# =========================
# 发送
# =========================

if [ "$GOTIFY_ALSO_HERDR_NOTIFY" = "true" ]; then
    "$HERDR_BIN" notification show --title "$TITLE" --message "$MESSAGE" >/dev/null 2>&1 || true
fi

if [ -z "$GOTIFY_URL" ] || [ -z "$GOTIFY_TOKEN" ]; then
    echo "herdr-gotify: GOTIFY_URL and GOTIFY_TOKEN are not set; skipping" >&2
    exit 0
fi

# Stderr of a hook is captured by Herdr into the plugin command log, so a curl
# failure ends up in `herdr plugin log` rather than a silently lost message.
curl \
    --silent \
    --show-error \
    --fail \
    --max-time 10 \
    -X POST \
    "${GOTIFY_URL%/}/message?token=${GOTIFY_TOKEN}" \
    --data-urlencode "title=${TITLE}" \
    --data-urlencode "message=${MESSAGE}" \
    --data-urlencode "priority=${PRIORITY}" \
    >/dev/null || {
        echo "herdr-gotify: Gotify push failed (status=${STATUS}, title=${TITLE})" >&2
        exit 1
    }

exit 0
