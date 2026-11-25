#!/bin/bash
# 每日 19:05 拉取云端产出的数据（原由 launchd 直接跑 git pull --rebase --autostash）
# 2026-11-25 新增守卫：autostash 撞冲突时 git 会留下 UU 状态并静默结束，
# 之后每天的 pull 都卡在同一处且没人知道 → 现在冲突必告警，data/ 生成文件自动取远端版本。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/dailypull_$(TZ=Asia/Shanghai date +%F).log"

mkdir -p "$LOG_DIR"
. "$SCRIPT_DIR/notify.sh"

log() { echo "$*" >> "$LOG_FILE"; }
conflicts() { git -C "$REPO_DIR" diff --name-only --diff-filter=U; }
# 冲突路径是否全在 data/ 下（只有这些是脚本自动生成、可安全取远端版本）
only_data_conflicts() {
  [ -n "$(conflicts)" ] || return 1
  ! conflicts | grep -qv '^data/'
}
rebase_running() { [ -d "$REPO_DIR/.git/rebase-merge" ] || [ -d "$REPO_DIR/.git/rebase-apply" ]; }

auto_resolve_data_conflicts() {
  local p
  conflicts | while IFS= read -r p; do
    git -C "$REPO_DIR" checkout origin/main -- "$p" && git -C "$REPO_DIR" add "$p"
  done
  [ -z "$(conflicts)" ] || return 1
  # rebase 进行中时不能 commit，交给 rebase --continue
  if ! rebase_running; then
    if ! git -C "$REPO_DIR" diff --cached --quiet; then
      git -C "$REPO_DIR" commit -q -m "chore(data): dailypull 自动清理冲突，以远端为准 $(TZ=Asia/Shanghai date +%F)"
    fi
  fi
  return 0
}

cd "$REPO_DIR" || exit 1
log "===== $(date '+%F %H:%M:%S') 开始拉取云端数据 ====="

# 1) 早就在冲突状态（上次遗留，正是 11-12~11-25 卡住 13 天的情形）
if [ -n "$(conflicts)" ]; then
  log "⚠️ 拉取前已存在未解决冲突："
  conflicts | sed 's/^/    /' >> "$LOG_FILE"
  if only_data_conflicts && auto_resolve_data_conflicts; then
    log "✅ 冲突文件均在 data/ 下，已按远端版本自动清理并提交"
  else
    log "❌ 冲突涉及 data/ 以外的文件，需人工处理"
    push_alert "ERP每日拉取: 仓库有冲突" "冲突涉及非 data/ 文件，需人工处理，本地数据可能停更" || true
    exit 2
  fi
fi

# 2) 拉取
if ! git pull --rebase --autostash origin main >> "$LOG_FILE" 2>&1; then
  # rebase 回放时再撞冲突：data/ 内的自动取远端并继续，其余一律中止并告警
  if rebase_running && only_data_conflicts; then
    log "⚠️ rebase 途中冲突（全在 data/），按远端版本处理："
    conflicts | sed 's/^/    /' >> "$LOG_FILE"
    if auto_resolve_data_conflicts && GIT_EDITOR=true git rebase --continue >> "$LOG_FILE" 2>&1; then
      log "✅ rebase 冲突已自动清理并继续"
      push_alert "ERP每日拉取: 冲突已自动清理" "rebase 途中 data/ 冲突已按远端覆盖后继续；非预期请查看日志" || true
    else
      git rebase --abort >/dev/null 2>&1 || true
      log "❌ 自动清理后 rebase 仍未通过"
      push_alert "ERP每日拉取失败" "rebase 冲突自动清理失败（$(date '+%F %H:%M')），本地数据可能没跟上云端" || true
      exit 2
    fi
  else
    git rebase --abort >/dev/null 2>&1 || true
    log "❌ git pull --rebase 失败"
    push_alert "ERP每日拉取失败" "git pull 失败（$(date '+%F %H:%M')），本地数据可能没跟上云端，看 logs/dailypull_$(TZ=Asia/Shanghai date +%F).log" || true
    exit 2
  fi
fi

# 3) autostash 回弹时可能产生新冲突
if [ -n "$(conflicts)" ]; then
  log "⚠️ 拉取后出现冲突（autostash 回弹）："
  conflicts | sed 's/^/    /' >> "$LOG_FILE"
  if only_data_conflicts && auto_resolve_data_conflicts; then
    log "✅ 冲突文件均在 data/ 下，已按远端版本自动清理"
    push_alert "ERP每日拉取: 冲突已自动清理" "autostash 撞冲突，data/ 文件已按远端覆盖；本地未提交改动丢弃，如非预期请查日志" || true
    exit 0
  fi
  log "❌ 冲突涉及 data/ 以外的文件，需人工处理"
  push_alert "ERP每日拉取: 拉取后冲突" "autostash 回弹出冲突且涉及非 data/ 文件，需人工处理" || true
  exit 2
fi

log "✅ $(date '+%F %H:%M:%S') 已同步到 $(git rev-parse --short HEAD)"
exit 0
