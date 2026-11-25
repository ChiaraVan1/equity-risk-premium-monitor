#!/bin/bash
# 统一告警出口（2026-11-25）
# 教训：原 alert() 只判断 curl 是否连上，不校验 PushPlus 返回体 code。
# 令牌失效/被限流时服务端仍回 HTTP 200，但报警实际没送达 → 报警自己挂了也不吭声。
# 现在：校验 code == 200，否则落一条投递失败台账 + 桌面通知兜底，绝不静默。

REPO_DIR="${REPO_DIR:-$HOME/equity-risk-premium-monitor}"
ALERT_FAIL_LOG="$REPO_DIR/logs/alert_delivery_fail.log"
PUSHPLUS_TOKEN="${PUSHPLUS_TOKEN:-5bffdca861424c56ab8a28b10f29a238}"

push_alert() {
  local title="$1" content="$2" resp code
  resp=$(curl -s -m 15 -X POST "https://www.pushplus.plus/send" \
    -H "Content-Type: application/json" \
    -d "{\"token\":\"$PUSHPLUS_TOKEN\",\"title\":\"$title\",\"content\":\"$content\",\"template\":\"txt\"}" 2>/dev/null)

  code=$(printf '%s' "$resp" | grep -o '"code":[0-9]*' | head -1 | cut -d: -f2)

  if [ "$code" = "200" ]; then
    return 0
  fi

  # 报警投递失败：必须留痕，不能吞掉
  mkdir -p "$(dirname "$ALERT_FAIL_LOG")"
  printf '%s | code=%s | resp=%s | 标题=%s | 内容=%s\n' \
    "$(date '+%F %T')" "${code:-无}" "${resp:-空响应}" "$title" "$content" >> "$ALERT_FAIL_LOG"
  osascript -e "display notification \"PushPlus 投递失败(code=${code:-无})：$title\" with title \"ERP 告警投递失败\" sound name \"Basso\"" 2>/dev/null || true
  return 1
}

# 被直接执行时（bash notify.sh "标题" "内容"）当作一次告警发送
if [ "${BASH_SOURCE[0]}" = "$0" ] && [ $# -ge 2 ]; then
  push_alert "$1" "$2"
  exit $?
fi
