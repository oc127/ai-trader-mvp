#!/bin/bash
# 东方财富妙想Skills 一键安装脚本
set -e

echo "=== 东方财富妙想Skills 安装 ==="

# 1. 检查 Node.js
if ! command -v node &> /dev/null; then
  echo "❌ 未安装 Node.js，请先安装 v22+"
  exit 1
fi
echo "✅ Node.js $(node --version)"

# 2. 清理旧版本
rm -rf ~/.openclaw/skills/mx-skills*
rm -rf ~/.openclaw/workspace/skills/mx-skills*
rm -rf ~/.openclaw/workspace/agent/skills/mx-skills*
rm -rf ~/mx-skills* ~/mx-data* ~/mx-search* ~/mx-xuangu* ~/mx-zixuan* ~/mx-moni*
echo "✅ 旧版本已清理"

# 3. 下载并解压5个skill包
INSTALL_DIR="$HOME"
TMP_DIR=$(mktemp -d)

echo "⏳ 下载 mx-data (金融数据)..."
curl -L -s -o "$TMP_DIR/mx-data.zip" "https://marketing.dfcfw.com/res/download/A620260331IHX67H.zip"
unzip -q -o "$TMP_DIR/mx-data.zip" -d "$INSTALL_DIR"

echo "⏳ 下载 mx-search (资讯搜索)..."
curl -L -s -o "$TMP_DIR/mx-search.zip" "https://marketing.dfcfw.com/res/download/A620260331K5WDTK.zip"
unzip -q -o "$TMP_DIR/mx-search.zip" -d "$INSTALL_DIR"

echo "⏳ 下载 mx-xuangu (智能选股)..."
curl -L -s -o "$TMP_DIR/mx-xuangu.zip" "https://marketing.dfcfw.com/res/download/A620260331NXBVEY.zip"
unzip -q -o "$TMP_DIR/mx-xuangu.zip" -d "$INSTALL_DIR"

echo "⏳ 下载 mx-zixuan (自选股管理)..."
curl -L -s -o "$TMP_DIR/mx-zixuan.zip" "https://marketing.dfcfw.com/res/download/A6202603314TMGR1.zip"
unzip -q -o "$TMP_DIR/mx-zixuan.zip" -d "$INSTALL_DIR"

echo "⏳ 下载 mx-moni (模拟组合管理)..."
curl -L -s -o "$TMP_DIR/mx-moni.zip" "https://marketing.dfcfw.com/res/download/A6202605083WYMDX.zip"
unzip -q -o "$TMP_DIR/mx-moni.zip" -d "$INSTALL_DIR"

rm -rf "$TMP_DIR"
echo "✅ 全部下载解压完成"

# 4. 设置永久环境变量
MX_KEY="mkt_h-nTWUdB7MrEdlxUH2kRo24VXBjIL9TZf7xgkHST_pw"

# 检测当前 shell 配置文件
if [ -f "$HOME/.zshrc" ]; then
  SHELL_RC="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
  SHELL_RC="$HOME/.bashrc"
else
  SHELL_RC="$HOME/.bashrc"
fi

# 删除旧的 MX_APIKEY 行（如果有），再追加新的
sed -i '/^export MX_APIKEY=/d' "$SHELL_RC"
echo "export MX_APIKEY=\"$MX_KEY\"" >> "$SHELL_RC"
export MX_APIKEY="$MX_KEY"
echo "✅ MX_APIKEY 已写入 $SHELL_RC（永久生效）"

# 5. 验证
echo ""
echo "=== 验证安装 ==="
test -f ~/mx-data/SKILL.md && echo "✅ mx-data OK" || echo "❌ mx-data MISSING"
test -f ~/mx-search/SKILL.md && echo "✅ mx-search OK" || echo "❌ mx-search MISSING"
test -f ~/mx-xuangu/SKILL.md && echo "✅ mx-xuangu OK" || echo "❌ mx-xuangu MISSING"
test -f ~/mx-zixuan/SKILL.md && echo "✅ mx-zixuan OK" || echo "❌ mx-zixuan MISSING"
test -f ~/mx-moni/SKILL.md && echo "✅ mx-moni OK" || echo "❌ mx-moni MISSING"
echo "MX_APIKEY=${MX_APIKEY:+已设置}"
echo ""
echo "=== 安装完成！==="
echo "运行 source $SHELL_RC 或重新登录使环境变量生效"
