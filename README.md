# herdr-gotify

[Herdr](https://herdr.dev) plugin that pushes a [Gotify](https://gotify.net/)
notification to your phone when an agent pane turns **blocked** (waiting for
your input) or **done** (finished background work). Idle, working and unknown
transitions are ignored, so it never floods you.

A Bash event hook — no build step, no dependencies beyond `bash`, `python3`,
and `curl`. Works on macOS and Linux.

## How it works

The plugin manifest registers an event hook:

```toml
[[events]]
on = "pane.agent_status_changed"
command = ["bash", "./notify.sh"]
```

Whenever a pane's agent status changes, Herdr runs `notify.sh` with the plugin
directory as its working directory and injects:

- `HERDR_PLUGIN_EVENT_JSON` — the event payload (`pane_id`, `workspace_id`,
  `agent_status`, `agent`, `display_agent`, `title`, `state_labels`).
- `HERDR_PLUGIN_CONTEXT_JSON` — friendly context such as `workspace_label` and
  `tab_label`, used so the message reads `paopaocha · trade-api` instead of
  `w3 · w3:p2`.
- `HERDR_PLUGIN_CONFIG_DIR`, `HERDR_PLUGIN_ROOT`, `HERDR_BIN_PATH`, … — see the
  [plugins documentation](https://herdr.dev/docs/plugins/).

`notify.sh` exits immediately for any status other than `blocked`/`done`, and
when neither `config.env` nor the environment provides `GOTIFY_URL` and
`GOTIFY_TOKEN`.

## Install

Requires Herdr ≥ 0.7.0 on macOS or Linux, and `curl` on the machine running the
Herdr server. `python3` is only used to parse the event JSON; the script falls
back to a no-op if it is missing.

Install from GitHub (the plugin is also listed on the
[Herdr marketplace](https://herdr.dev/plugins/) under the `herdr-plugin`
topic):

```sh
herdr plugin install 8liang/herdr-gotify
```

Herdr clones the repository, registers the plugin, and creates its config
directory. Then create the config file in the directory Herdr prints:

```sh
CONFIG_DIR="$(herdr plugin config-dir 8liang.herdr-gotify)"
cp config.env.example "$CONFIG_DIR/config.env"
chmod 600 "$CONFIG_DIR/config.env"
$EDITOR "$CONFIG_DIR/config.env"
```

Fill in at least:

```sh
GOTIFY_URL="https://gotify.example.com"
GOTIFY_TOKEN="your-application-token"
```

Create the token in Gotify under **Apps** → *Create Application*. It grants
push-only access to that one app.

Verify the plugin is enabled:

```sh
herdr plugin list
```

### Note for Herdr releases before 0.8.2

If your Herdr version is older than the one that ships plugin config
directories, copy `config.env.example` to `config.env` next to `notify.sh`
instead (the hook reads `./config.env` as a development fallback).

### Developing locally

While working on the plugin itself, link the local checkout instead of
installing:

```sh
herdr plugin link /path/to/herdr-gotify
```

A locally linked plugin must be unlinked before `herdr plugin install` can
manage the same plugin id:

```sh
herdr plugin unlink 8liang.herdr-gotify
```

## Testing

First verify Gotify itself accepts pushes:

```sh
curl -X POST \
  "https://gotify.example.com/message?token=YOUR_TOKEN" \
  -F "title=Herdr Test" \
  -F "message=Gotify 通知测试" \
  -F "priority=5"
```

Then exercise the hook end-to-end with a synthetic event (no real Herdr server
needed — the hook only reads env and posts over HTTP):

```sh
cd /path/to/herdr-gotify
export HERDR_PLUGIN_CONFIG_DIR="$(mktemp -d)"
cp config.env.example "$HERDR_PLUGIN_CONFIG_DIR/config.env"
# edit URL/token, then:
HERDR_PLUGIN_EVENT_JSON='{"event":"pane_agent_status_changed","data":{"pane_id":"w1:p2","workspace_id":"w1","agent_status":"blocked","display_agent":"claude","title":"Implement pet trading API"}}' \
HERDR_PLUGIN_CONTEXT_JSON='{"workspace_label":"paopaocha","tab_label":"trade-api"}' \
bash notify.sh
```

Watch failures in Herdr's plugin log:

```sh
herdr plugin log list --plugin 8liang.herdr-gotify
```

## Configuration

All settings are optional; the table shows the defaults. Put overrides in
`config.env` under the plugin config directory (printed by `herdr plugin
config-dir 8liang.herdr-gotify`), or export them in the environment of the
Herdr server process. Environment variables take precedence over `config.env`.

| Variable                   | Default | What it does                                            |
| -------------------------- | ------- | ------------------------------------------------------- |
| `GOTIFY_URL`               | —       | Gotify server base URL, e.g. `https://gotify.example.com`. Required for push. |
| `GOTIFY_TOKEN`             | —       | Gotify application token. Required for push.            |
| `GOTIFY_PRIORITY_BLOCKED`  | `8`     | Priority of blocked notifications (0–10).               |
| `GOTIFY_PRIORITY_DONE`     | `5`     | Priority of done notifications (0–10).                  |
| `NOTIFY_BLOCKED`           | `true`  | Set `false` to stop blocked notifications.              |
| `NOTIFY_DONE`              | `true`  | Set `false` to stop done notifications.                 |
| `GOTIFY_ALSO_HERDR_NOTIFY` | `false` | Also show a local Herdr desktop notification (`herdr notification show`). |

### Strategy note

Herdr reports five agent statuses: `idle`, `working`, `blocked`, `done`,
`unknown`. `idle` and `unknown` can occur often and are exactly what the
`done` state is meant to summarize, so this plugin only notifies on:

```text
blocked → priority 8
done    → priority 5
idle    → no notification
working → no notification
unknown → no notification
```

## Files

| Path                  | Purpose                                                    |
| --------------------- | ---------------------------------------------------------- |
| `herdr-plugin.toml`   | Manifest: registers the event hook for `pane.agent_status_changed`. |
| `notify.sh`           | The hook: reads the event, filters, posts to Gotify.       |
| `config.env.example`  | Documented configuration template.                         |

## Troubleshooting

**No notification arrives.** Confirm Gotify accepts a manual push first (see
Testing). Then check the plugin log. Each entry shows the command, exit code,
and captured stdout/stderr, so a curl failure surfaces as the hook's stderr
line:

```sh
herdr plugin log list --plugin 8liang.herdr-gotify
```

Missing `GOTIFY_URL`/`GOTIFY_TOKEN` is reported there as a skip (exit 0),
not an error.

**Secrets.** Never commit `config.env`. The local one is git-ignored; the real
config lives under the Herdr plugin config directory (see Install), which is
outside the repository.
