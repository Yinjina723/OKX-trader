#!/usr/bin/env bash
# install.sh — OKX 交易助手 一键安装脚本
# 用法: bash install.sh codebuddy|openclaw|cursor|windsurf|claude
#
# Windows 用户可用 Git Bash 或 WSL 运行，
# 也可直接在 PowerShell 中手动复制

set -e

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-help}"

case "$MODE" in
  codebuddy)
    # ── CodeBuddy — 安装到用户 Skill 目录 ──
    SKILL_HOME="$HOME/.codebuddy/skills/okx-trader"
    mkdir -p "$SKILL_HOME"
    cp -r "$SKILL_DIR/references" "$SKILL_HOME/"
    cp -r "$SKILL_DIR/scripts" "$SKILL_HOME/"
    cp "$SKILL_DIR/SKILL.md" "$SKILL_HOME/"
    echo "✅ CodeBuddy Skill 已安装到: $SKILL_HOME"
    echo "   下次对话中提到 OKX/交易信号/庄家分析 时自动触发"
    ;;

  openclaw)
    # ── OpenClaw — 创建 Agent + 复制配置 ──
    AGENT_DIR="$HOME/.openclaw/agents/okx-trader/agent"
    mkdir -p "$AGENT_DIR"
    mkdir -p "$HOME/.openclaw/agents/okx-trader/workspace"
    cp "$SKILL_DIR/openclaw/SOUL.md" "$AGENT_DIR/"
    echo "✅ OpenClaw Agent 已安装"
    echo "👉 接下来需要手动操作:"
    echo "   1. 编辑 ~/.openclaw/openclaw.json"
    echo "   2. 参考 openclaw/openclaw_config.json5 添加 agent 和 binding"
    echo "   3. 执行 openclaw gateway restart"
    ;;

  cursor)
    # ── Cursor — 复制 .cursorrules 到当前项目根目录 ──
    PROJECT_ROOT="$(dirname "$SKILL_DIR")"
    cp "$SKILL_DIR/cursor/.cursorrules" "$PROJECT_ROOT/"
    echo "✅ .cursorrules 已复制到: $PROJECT_ROOT"
    echo "   重新打开项目即可生效"
    ;;

  windsurf)
    # ── Windsurf — 复制 .windsurfrules 到当前项目根目录 ──
    PROJECT_ROOT="$(dirname "$SKILL_DIR")"
    cp "$SKILL_DIR/windsurf/.windsurfrules" "$PROJECT_ROOT/"
    echo "✅ .windsurfrules 已复制到: $PROJECT_ROOT"
    echo "   重新打开项目即可生效"
    ;;

  claude)
    # ── Claude Code — 复制 CLAUDE.md 到当前项目根目录 ──
    PROJECT_ROOT="$(dirname "$SKILL_DIR")"
    cp "$SKILL_DIR/claude/CLAUDE.md" "$PROJECT_ROOT/"
    echo "✅ CLAUDE.md 已复制到: $PROJECT_ROOT"
    ;;

  *)
    echo "OKX 交易助手 — 一键安装"
    echo ""
    echo "用法: bash install.sh <平台>"
    echo ""
    echo "支持的平台:"
    echo "  codebuddy   安装为 CodeBuddy Skill"
    echo "  openclaw    安装为 OpenClaw Agent"
    echo "  cursor      安装 .cursorrules 规则文件"
    echo "  windsurf    安装 .windsurfrules 规则文件"
    echo "  claude      安装 CLAUDE.md 规则文件"
    echo ""
    echo "示例: bash install.sh codebuddy"
    ;;
esac
