#!/usr/bin/env bash
# sync-webui.sh — 手动同步 upstream WebUI 到本地
# 用法: bash sync-webui.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

UPSTREAM="upstream-webui"
UPSTREAM_URL="https://github.com/nesquena/hermes-webui.git"
BRANCH="master"

echo "⚡ Hermes Installer — WebUI 上游同步"
echo "──────────────────────────────────────"

# 确保 upstream remote 存在
if ! git remote get-url "$UPSTREAM" &>/dev/null; then
  echo "→ 添加上游 remote: $UPSTREAM_URL"
  git remote add "$UPSTREAM" "$UPSTREAM_URL"
fi

echo "→ 拉取上游最新代码..."
git fetch "$UPSTREAM" "$BRANCH"

LOCAL=$(git log --oneline --grep="Squashed.*webui.*content from commit" -1 | sed 's/.*commit //;s/ .*//' | tr -d "'")
REMOTE=$(git rev-parse "$UPSTREAM/$BRANCH")

echo "  本地 webui: $LOCAL"
echo "  上游 webui: $REMOTE"

if [ "$LOCAL" = "$REMOTE" ]; then
  echo ""
  echo "✅ WebUI 已是最新，无需同步。"
  exit 0
fi

echo ""
echo "→ 上游有更新！开始同步..."
git subtree pull --prefix=webui "$UPSTREAM" "$BRANCH" --squash \
  -m "sync: update webui from upstream ($REMOTE)"

echo ""
echo "✅ 同步完成！"
echo "  请检查改动后手动推送: git push origin main"
