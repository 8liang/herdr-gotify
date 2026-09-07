#!/usr/bin/env python3
"""Summarize a snippet of pane output through an LLM for herdr-gotify.

Borrowed from ai-cli-complete-notify/src/summary.js (same prompts, request
shapes, and response parsing), reduced to the two protocols this plugin needs:

  - openai   : OpenAI-compatible POST {base}/v1/chat/completions
               (covers OpenAI, DeepSeek, Qwen/DashScope, Ollama, ...)
  - anthropic: POST {base}/v1/messages

Config comes from the environment (notify.sh sources config.env with `set -a`,
so SUMMARY_* are exported here).  Only the final summary is printed on stdout;
diagnostics go to stderr.  Exit code is 0 on success, non-zero otherwise so
notify.sh can fall back to the plain message.

Dependencies: Python 3 stdlib only (urllib/json/sys/argparse).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_TIMEOUT_MS = 30000
DEFAULT_MAX_TOKENS = 200

PROMPT_ZH = (
    "你是一个技术助手。请根据任务与执行结果生成一句简短中文摘要"
    "（100字以内），只输出摘要本身。"
)
PROMPT_EN = (
    "You are a technical assistant. Summarize the task and outcome in one "
    "short sentence (<=120 chars). Output only the summary."
)

# Paths the codebase appends to a base URL. A URL that already ends in one of
# these (or a bare "/v1" prefix-less exact endpoint) is used as-is.
OPENAI_ENDPOINT = "/v1/chat/completions"
ANTHROPIC_ENDPOINT = "/v1/messages"

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def env_str(name, default=""):
    value = os.environ.get(name, "")
    return value.strip() or default


def env_bool(name, default=False):
    value = os.environ.get(name, "").strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


def env_int(name, default):
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


def trim(value):
    return (value or "").strip()


def truncate(text, max_length):
    value = str(text or "").strip()
    if not value:
        return ""
    if len(value) <= max_length:
        return value
    return value[: max(0, max_length - 1)] + "…"


def redact(text, secret):
    value = str(text or "")
    token = str(secret or "")
    if not value or not token:
        return value
    return value.replace(token, "[redacted]")


# ---------------------------------------------------------------------------
# request building
# ---------------------------------------------------------------------------


def endpoint_for(protocol, base):
    """Append the protocol's endpoint suffix unless base already is one."""
    raw = trim(base)
    if not raw:
        return ""
    # Trailing "#" forces the URL to be used exactly as given.
    if raw.endswith("#"):
        return raw[:-1]
    if raw.endswith("/"):
        raw = raw[:-1]
    if not raw:
        return ""
    lower = raw.lower()
    if protocol == "anthropic":
        if lower.endswith("/v1/messages") or lower.endswith("/messages"):
            return raw
        return raw + ANTHROPIC_ENDPOINT
    # openai-compatible
    if lower.endswith("/chat/completions"):
        return raw
    return raw + OPENAI_ENDPOINT


def build_user_content(task, output, max_chars):
    lines = []
    if trim(task):
        lines.append("Task: %s" % truncate(task, 200))
    if trim(output):
        lines.append("Recent output:\n%s" % truncate(output, max_chars))
    return "\n\n".join(lines)


def default_prompt(lang):
    return PROMPT_EN if str(lang or "").lower().startswith("en") else PROMPT_ZH


def build_request(protocol, url, api_key, model, system_prompt, user_content):
    """Return (headers, body_dict) for the given protocol.

    anthropic sends the key as x-api-key (standard Anthropic); openai-compatible
    sends it as Authorization: Bearer.
    """
    if protocol == "anthropic":
        payload = {
            "model": model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "temperature": 0.4,
            "messages": [{"role": "user", "content": user_content}],
        }
        if system_prompt:
            payload["system"] = system_prompt
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        return headers, payload

    # openai-compatible (default)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_content})
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % api_key,
    }
    return headers, payload


# ---------------------------------------------------------------------------
# response parsing (mirrors summary.js extractSummary/extractAnthropicSummary)
# ---------------------------------------------------------------------------


def extract_openai_summary(data):
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        message = first.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        if isinstance(first.get("text"), str):
            return first["text"]
    if isinstance(data.get("output"), str):
        return data["output"]
    return ""


def extract_anthropic_summary(data):
    content = data.get("content")
    if isinstance(content, list):
        parts = []
        for block in content:
            if block is None:
                continue
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        if parts:
            return "".join(parts)
    if isinstance(data.get("completion"), str):
        return data["completion"]
    return ""


def sanitize_summary(text):
    value = str(text or "")
    if not value:
        return ""
    cleaned = " ".join(value.split())
    cleaned = cleaned.strip("\"'`")
    cleaned = trim(cleaned)
    return truncate(cleaned, 160)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv):
    parser = argparse.ArgumentParser(description="Summarize text via an LLM.")
    parser.add_argument("--text", default="", help="text to summarize")
    parser.add_argument("--task", default="", help="optional task title")
    parser.add_argument("--lang", default="", help="zh | en")
    parser.add_argument("--prompt", default="", help="override system prompt")
    parser.add_argument(
        "--max-chars", type=int, default=0, help="truncate --text to N chars"
    )
    args = parser.parse_args(argv)

    protocol = env_str("SUMMARY_PROTOCOL", "openai").lower()
    if protocol not in ("openai", "anthropic"):
        print(
            "herdr-gotify: unsupported SUMMARY_PROTOCOL=%r (openai | anthropic)"
            % protocol,
            file=sys.stderr,
        )
        return 2

    timeout_ms = env_int("SUMMARY_TIMEOUT_MS", DEFAULT_TIMEOUT_MS)
    timeout_s = max(1, timeout_ms / 1000.0)

    api_url = env_str("SUMMARY_API_URL")
    api_key = env_str("SUMMARY_API_KEY")
    model = env_str("SUMMARY_MODEL")

    missing = []
    if not api_url:
        missing.append("SUMMARY_API_URL")
    if not model:
        missing.append("SUMMARY_MODEL")
    if not api_key:
        missing.append("SUMMARY_API_KEY")
    if missing:
        print(
            "herdr-gotify: missing config for summary (%s); skipping summary"
            % ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    request_url = endpoint_for(protocol, api_url)
    if not request_url:
        print("herdr-gotify: empty SUMMARY_API_URL", file=sys.stderr)
        return 2

    max_chars = args.max_chars if args.max_chars and args.max_chars > 0 else 6000
    user_content = build_user_content(args.task, args.text, max_chars)
    if not user_content:
        print("herdr-gotify: nothing to summarize", file=sys.stderr)
        return 2

    lang = env_str("SUMMARY_LANG") or args.lang
    system_prompt = args.prompt or env_str("SUMMARY_PROMPT") or default_prompt(lang)
    headers, payload = build_request(
        protocol, request_url, api_key, model, system_prompt, user_content
    )

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(request_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        print(
            "herdr-gotify: LLM HTTP %s: %s"
            % (err.code, truncate(redact(detail, api_key), 400)),
            file=sys.stderr,
        )
        return 1
    except Exception as err:  # timeout / connection refused / ...
        print(
            "herdr-gotify: LLM request failed: %s" % truncate(str(err), 300),
            file=sys.stderr,
        )
        return 1

    try:
        data = json.loads(raw or "{}")
    except ValueError:
        print("herdr-gotify: LLM returned invalid JSON", file=sys.stderr)
        return 1

    if status < 200 or status >= 300:
        print(
            "herdr-gotify: LLM HTTP %s: %s"
            % (status, truncate(redact(raw, api_key), 400)),
            file=sys.stderr,
        )
        return 1

    if protocol == "anthropic":
        summary = sanitize_summary(extract_anthropic_summary(data))
    else:
        summary = sanitize_summary(extract_openai_summary(data))

    if not summary:
        print("herdr-gotify: LLM returned an empty summary", file=sys.stderr)
        return 1

    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
