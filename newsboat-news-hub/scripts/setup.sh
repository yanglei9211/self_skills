#!/usr/bin/env bash
# Newsboat 一键安装部署脚本
# 用法: bash setup.sh [china|full]
# 默认使用 china（中国大陆直连版）

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-china}"

echo "=== Newsboat 新闻中心 - 安装部署 ==="
echo "模式: $MODE"
echo ""

# 1. 安装 Newsboat
if command -v newsboat &>/dev/null; then
    echo "✓ Newsboat 已安装: $(newsboat -v 2>&1 | head -1)"
else
    echo "→ 安装 Newsboat..."
    if command -v brew &>/dev/null; then
        brew install newsboat
    elif command -v apt &>/dev/null; then
        sudo apt update && sudo apt install -y newsboat
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm newsboat
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y newsboat
    else
        echo "✗ 无法检测包管理器，请手动安装 newsboat"
        exit 1
    fi
    echo "✓ Newsboat 安装完成"
fi

# 2. 创建配置目录
mkdir -p ~/.newsboat
echo "✓ ~/.newsboat/ 目录就绪"

# 3. 部署配置文件
if [ -f ~/.newsboat/config ]; then
    cp ~/.newsboat/config ~/.newsboat/config.bak
    echo "→ 已备份旧配置到 config.bak"
fi
cp "$SKILL_DIR/config" ~/.newsboat/config
echo "✓ 配置文件已部署"

# 4. 部署 RSS 源
if [ -f ~/.newsboat/urls ]; then
    cp ~/.newsboat/urls ~/.newsboat/urls.bak
    echo "→ 已备份旧源到 urls.bak"
fi
if [ "$MODE" = "full" ]; then
    cp "$SKILL_DIR/urls-full" ~/.newsboat/urls
    echo "✓ 完整版源已部署（需要代理）"
    echo ""
    echo "⚠  记得在 ~/.newsboat/config 中取消注释代理配置并填入正确端口"
else
    cp "$SKILL_DIR/urls-china" ~/.newsboat/urls
    echo "✓ 中国直连版源已部署"
fi

# 5. 验证
echo ""
echo "=== 部署完成 ==="
echo "启动: newsboat"
echo "刷新: 按 R（大写）"
echo ""

# 6. 快速连通性测试（抽测 3 个源）
echo "=== 连通性抽测 ==="
test_url() {
    local url="$1" name="$2"
    code=$(curl -s -o /dev/null -w "%{http_code}" -L --max-time 5 --connect-timeout 3 "$url" 2>/dev/null)
    [ $? -ne 0 ] && echo "  ✗ $name → 不可达" || echo "  ✓ $name → HTTP $code"
}
test_url "https://feeds.npr.org/1001/rss.xml" "NPR"
test_url "https://apnews.com/hub/apf-topnews?output=rss" "AP"
test_url "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en" "Google News"
echo ""
echo "全部通过则可正常使用。如有不通，检查网络或代理设置。"
