#!/usr/bin/env bash
# distribute.sh — OKX 交易助手 打包脚本
# 生成 okx-trader.zip 分发包

set -e

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="${SKILL_DIR}/dist"
ZIP_NAME="okx-trader.zip"

# 清理旧文件
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR/okx-trader"

# 复制所有需要的文件（排除 dist 目录和无关文件）
cp -r "$SKILL_DIR/references" "$DIST_DIR/okx-trader/"
cp -r "$SKILL_DIR/scripts" "$DIST_DIR/okx-trader/"
cp -r "$SKILL_DIR/openclaw" "$DIST_DIR/okx-trader/"
cp -r "$SKILL_DIR/cursor" "$DIST_DIR/okx-trader/"
cp -r "$SKILL_DIR/windsurf" "$DIST_DIR/okx-trader/"
cp -r "$SKILL_DIR/claude" "$DIST_DIR/okx-trader/"
cp "$SKILL_DIR/SKILL.md" "$DIST_DIR/okx-trader/"
cp "$SKILL_DIR/install.sh" "$DIST_DIR/okx-trader/"

# 打包
cd "$DIST_DIR"
if command -v zip &>/dev/null; then
    zip -r "$ZIP_NAME" okx-trader/
    echo "✅ 打包完成: $DIST_DIR/$ZIP_NAME"
elif command -v powershell &>/dev/null; then
    powershell -Command "Compress-Archive -Path okx-trader -DestinationPath $ZIP_NAME -Force"
    echo "✅ 打包完成: $DIST_DIR/$ZIP_NAME"
else
    echo "⚠ 未找到 zip 或 powershell 命令，文件已复制到: $DIST_DIR/okx-trader/"
fi

# 也打包 CodeBuddy 专用 Skill zip（仅含 SKILL.md + references + scripts）
echo ""
echo "📦 生成 CodeBuddy Skill 专用包..."
mkdir -p "$DIST_DIR/codebuddy-skill"
cp "$SKILL_DIR/SKILL.md" "$DIST_DIR/codebuddy-skill/"
cp -r "$SKILL_DIR/references" "$DIST_DIR/codebuddy-skill/"
cp -r "$SKILL_DIR/scripts" "$DIST_DIR/codebuddy-skill/"
cd "$DIST_DIR"
if command -v zip &>/dev/null; then
    zip -r "codebuddy-skill.zip" codebuddy-skill/
    echo "✅ CodeBuddy Skill 包: $DIST_DIR/codebuddy-skill.zip"
elif command -v powershell &>/dev/null; then
    powershell -Command "Compress-Archive -Path codebuddy-skill -DestinationPath codebuddy-skill.zip -Force"
    echo "✅ CodeBuddy Skill 包: $DIST_DIR/codebuddy-skill.zip"
fi

echo ""
echo "================================"
echo "  分发文件已就绪！"
echo "================================"
echo "  完整包:  $DIST_DIR/$ZIP_NAME"
echo "  CB包:    $DIST_DIR/codebuddy-skill.zip"
