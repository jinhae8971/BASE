#!/usr/bin/env bash
set -euo pipefail

#
# migrate.sh — Create 6 GitHub repos and push each project
#
# Prerequisites:
#   - gh CLI installed and authenticated (gh auth login)
#   - Run from the BASE repo root where all 6 subdirectories exist
#
# Usage:
#   chmod +x migrate.sh
#   ./migrate.sh
#

GITHUB_USER=$(gh api user --jq .login 2>/dev/null || true)
if [ -z "$GITHUB_USER" ]; then
  echo "❌ gh CLI not authenticated. Run: gh auth login"
  exit 1
fi
echo "✅ Authenticated as: $GITHUB_USER"

REPOS=(
  "crypto-research-agent|Daily top-500 crypto gainers research agent with Claude analysis, Telegram delivery, and GitHub Pages dashboard"
  "kospi-research-agent|Daily KOSPI top-gainer research agent with Claude analysis, Telegram delivery, and GitHub Pages dashboard"
  "sp500-research-agent|Daily S&P 500 top-gainer research agent with Claude analysis, Telegram delivery, and GitHub Pages dashboard"
  "nasdaq-research-agent|Daily NASDAQ-100 top-gainer research agent with Claude analysis, Telegram delivery, and GitHub Pages dashboard"
  "dow30-research-agent|Daily Dow Jones 30 top-gainer research agent with Claude analysis, Telegram delivery, and GitHub Pages dashboard"
  "global-market-orchestrator|Daily cross-market orchestrator aggregating Crypto, KOSPI, S&P 500, NASDAQ-100, and Dow 30 research agents"
)

for entry in "${REPOS[@]}"; do
  IFS='|' read -r REPO_NAME DESCRIPTION <<< "$entry"

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "📦 Processing: $REPO_NAME"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  if [ ! -d "$REPO_NAME" ]; then
    echo "⚠️  Directory $REPO_NAME not found, skipping"
    continue
  fi

  # 1. Create GitHub repo (skip if already exists)
  if gh repo view "$GITHUB_USER/$REPO_NAME" &>/dev/null; then
    echo "   Repo already exists: $GITHUB_USER/$REPO_NAME"
  else
    echo "   Creating repo: $GITHUB_USER/$REPO_NAME"
    gh repo create "$REPO_NAME" \
      --public \
      --description "$DESCRIPTION" \
      --disable-wiki \
      --disable-issues=false
    echo "   ✅ Created"
  fi

  # 2. Init git + push
  cd "$REPO_NAME"

  # Clean up any existing .git from parent tracking
  if [ -d ".git" ]; then
    rm -rf .git
  fi

  git init -b main
  git add .
  git commit -m "Initial import: $REPO_NAME

Automated migration from jinhae8971/BASE mono-scaffold."

  REMOTE_URL="https://github.com/$GITHUB_USER/$REPO_NAME.git"
  git remote add origin "$REMOTE_URL" 2>/dev/null || git remote set-url origin "$REMOTE_URL"

  # Push with retry
  for i in 1 2 3 4; do
    if git push -u origin main 2>/dev/null; then
      echo "   ✅ Pushed to $REMOTE_URL"
      break
    fi
    echo "   Retry push ($i)..."
    sleep $((2 ** i))
  done

  cd ..
  echo "   ✅ Done: $REPO_NAME"
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎉 All repos created and pushed!"
echo ""
echo "Next steps for EACH repo:"
echo "  1. Settings → Pages → Source: GitHub Actions"
echo "  2. Settings → Secrets → Add: ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID"
echo "  3. Settings → Variables → Add: DASHBOARD_URL = https://$GITHUB_USER.github.io/<repo-name>/"
echo ""
echo "For global-market-orchestrator, also add Variables:"
echo "  CRYPTO_DASHBOARD_URL  = https://$GITHUB_USER.github.io/crypto-research-agent"
echo "  KOSPI_DASHBOARD_URL   = https://$GITHUB_USER.github.io/kospi-research-agent"
echo "  SP500_DASHBOARD_URL   = https://$GITHUB_USER.github.io/sp500-research-agent"
echo "  NASDAQ_DASHBOARD_URL  = https://$GITHUB_USER.github.io/nasdaq-research-agent"
echo "  DOW30_DASHBOARD_URL   = https://$GITHUB_USER.github.io/dow30-research-agent"
echo "  DASHBOARD_URL         = https://$GITHUB_USER.github.io/global-market-orchestrator/"
echo ""
echo "Then trigger first run: Actions → Run workflow"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
