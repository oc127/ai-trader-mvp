## Claude（Anthropic）最小连接示例

这个仓库提供一个**不依赖第三方包**的 Claude 调用示例：用 Node.js 自带 `fetch` 调用 Anthropic Messages API。

### 前置条件

- **Node.js**: 18 或更高
- **Claude API Key**: 在 Anthropic 控制台创建（环境变量名：`ANTHROPIC_API_KEY`）

### 快速开始

1) 设置环境变量（任选其一）

- 临时方式（推荐先验证）：

```bash
export ANTHROPIC_API_KEY="你的key"
```

- 或参考 `env.example` 把变量加到你的 shell 配置里（如 `~/.zshrc`）。

2) 发送一条消息验证连通

```bash
npm run claude -- "你好，Claude。请用一句话自我介绍。"
```

### 可选配置

- **ANTHROPIC_MODEL**: 默认 `claude-3-5-sonnet-latest`
- **ANTHROPIC_MAX_TOKENS**: 默认 `512`
- **ANTHROPIC_BASE_URL**: 默认 `https://api.anthropic.com`（有代理/网关时可改）

### 链接更多 MCP（把多个 MCP Server 写进配置文件）

这个仓库额外提供了一个**无三方依赖**的小工具：`scripts/mcp-config.mjs`，用来把多个 MCP server 合并写入某个 JSON 配置文件（不会把其它字段覆盖掉）。

#### 1) 找到你要写入的配置文件

- **Claude Desktop（macOS 常见路径）**：`~/Library/Application Support/Claude/claude_desktop_config.json`
- **Cursor（推荐）**：
  - **项目级**：`./.cursor/mcp.json`（只对当前仓库生效，适合自动化流程）
  - **用户级**：`~/.cursor/mcp.json`（对所有仓库生效）

> 你也可以先写到仓库里某个文件（例如 `./mcp.local.json`），确认无误后再复制到目标配置文件。

#### 2) 添加多个 MCP server（示例：filesystem + git）

把 `--config` 改成你的实际配置文件路径：

```bash
# 添加 filesystem（把本仓库目录作为 root）
npm run mcp -- add filesystem --name fs --root "$PWD" --config "~/Library/Application Support/Claude/claude_desktop_config.json"

# 添加 git（同样以本仓库目录作为 root）
npm run mcp -- add git --name git --root "$PWD" --config "~/Library/Application Support/Claude/claude_desktop_config.json"

# 列出当前已配置的 servers
npm run mcp -- list --config "~/Library/Application Support/Claude/claude_desktop_config.json"
```

#### 2.1) 自动化流程推荐：Playwright（浏览器自动化 / E2E）

Playwright MCP 很适合做“自动化流程”：跑 E2E、复现网页 bug、做页面回归检查等。

```bash
# Cursor 项目级配置（推荐）
npm run mcp -- add playwright --name pw --config "./.cursor/mcp.json"

# Claude Desktop 配置
npm run mcp -- add playwright --name pw --config "~/Library/Application Support/Claude/claude_desktop_config.json"
```

> 说明：默认使用 `chromium`；另外 `@playwright/mcp` 的 `--image-responses` 目前只支持 `allow` / `omit`，默认这里用 `omit`（更稳定也更省 token）。

#### 2.2) 验证是否写入成功

```bash
cat ./.cursor/mcp.json
```

#### 2.3) 常见报错：`Access token expired or revoked` / 登录验证码失败（推荐免登录修法）

如果 Cursor 的 MCP 输出里出现类似：

- `npm notice Access token expired or revoked`
- 或你 `npm login` 被验证码（captcha）卡住

通常是你本机 `~/.npmrc` 里有**过期 token**导致 `npx` 拉包失败。

推荐做法：给 MCP 单独指定一个“干净的 npm 配置文件”，让 MCP 启动时不去读你的 `~/.npmrc`。

```bash
# 在项目根目录创建一个干净配置（复制自仓库示例）
cp ./npmrc.mcp.example ./npmrc.mcp

# 重新写入/更新 MCP server，并强制它们使用这个 npm 配置文件
npm run mcp -- add filesystem --name fs --root "$PWD" --npm-userconfig "./npmrc.mcp" --config "./.cursor/mcp.json"
npm run mcp -- add git --name git --root "$PWD" --npm-userconfig "./npmrc.mcp" --config "./.cursor/mcp.json"
npm run mcp -- add playwright --name pw --npm-userconfig "./npmrc.mcp" --config "./.cursor/mcp.json"
```

#### 3) 添加自定义 MCP server

当你拿到某个 MCP server 的启动方式（`command` / `args` / `env`）时，可以用 `add-custom`：

```bash
npm run mcp -- add-custom \
  --name myServer \
  --command npx \
  --args "-y,@scope/some-mcp-server,--flag,value" \
  --env "API_KEY=xxx,BASE_URL=https://example.com" \
  --config "~/Library/Application Support/Claude/claude_desktop_config.json"
```

#### 4) 参考示例配置

仓库提供 `mcp.example.json`，你也可以手动复制其中的 `mcpServers` 到你的目标配置文件。

---

## A股趋势动量日报（开发基础 / MVP）

目标：先把 **数据 → 信号 → Markdown 报告** 跑通（先日线、先交付报告）。

### 目录约定

- `data/`: 数据与示例
  - `data/watchlist.example.txt`: 自选股列表（你可以复制成自己的 watchlist）
  - `data/sample_daily.csv`: 示例日线数据（无需联网即可跑通）
- `py/biubiu_invest/`: Python 代码（数据源/缓存/信号/报告）
- `scripts/daily_report.py`: 生成日报脚本
- `reports/`: 生成的报告输出目录（已加入 `.gitignore`）

### 先用示例数据跑通（无需联网）

```bash
cd /Users/theoc/theOC/biubiu
npm run report
```

运行后会生成：`reports/<日期>.md`

### 切换为免费真实数据（可选：AkShare）

1) 创建 Python 虚拟环境并安装依赖：

```bash
cd /Users/theoc/theOC/biubiu
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install akshare
```

2) 用 AkShare 生成报告（先从 watchlist 做起，避免一次拉全市场）：

```bash
PYTHONPATH=./py python3 scripts/daily_report.py --provider akshare --watchlist data/watchlist.example.txt --lookback 20 --top 20
```

### 政策层（Jurisdiction Policy）——为“全球交易员 Agent”做合规可配置

我们把不同国家/地区的合规与安全边界抽象成**政策层**（JSON 配置，不引入第三方依赖）。

- 默认使用：`policies/default_strict.json`（研究/模拟盘，禁止自动下单）
- 预留占位：`policies/us_placeholder.json` / `policies/cn_placeholder.json` / `policies/hk_placeholder.json`

在生成报告时可以指定政策文件（会写入报告头部元信息）：

```bash
PYTHONPATH=./py python3 scripts/daily_report.py --policy policies/us_placeholder.json
```

---

## 投资/股票分析开发：推荐 MCP 组合（Cursor）

你现在已经有的（必备）：

- **`fs`**：读写项目文件（写代码/生成报告）
- **`git`**：版本控制操作（diff/提交/回滚/分支）
- **`pw`**：浏览器自动化（补充抓取、自动化验证）

建议再加三个（会让研发体验明显更顺）：

- **`fetch`**：抓网页并清洗为可读 Markdown（研报/公告/新闻页面很实用）
- **`db`（sqlite）**：直接查/写 SQLite（存行情/信号/回测/复盘）
- **`code`（code-runner）**：让 AI 跑小段代码（快速验证公式/小计算；前提是你本机有对应语言运行时）

项目根目录执行（写入到 `./.cursor/mcp.json`；如果你有 npm 登录/token 问题，先按前面 `npmrc.mcp` 的方式处理）：

```bash
cd /Users/theoc/theOC/biubiu
cp ./npmrc.mcp.example ./npmrc.mcp

npm run mcp -- add fetch --name fetch --npm-userconfig "./npmrc.mcp" --config "./.cursor/mcp.json"
npm run mcp -- add sqlite --name db --db "$PWD/data/biubiu.db" --npm-userconfig "./npmrc.mcp" --config "./.cursor/mcp.json"
npm run mcp -- add code-runner --name code --npm-userconfig "./npmrc.mcp" --config "./.cursor/mcp.json"
```

---

## Web Dashboard（先从网页开始）

我们提供一个**零依赖**的本地 Web 工作台，用来模拟 Manus 风格的“任务驱动 Agent”：

- 输入任务参数 → 执行（调用脚本）→ 写审计 → 展示报告产物
- 当前只做 **研究模式**：生成日报，不涉及自动交易

启动：

```bash
cd /Users/theoc/theOC/biubiu
npm run web
```

浏览器打开：`http://127.0.0.1:3141`

### 任务队列 / 状态机

Web 工作台内置一个最小的**任务队列/状态机**（单 worker 串行执行）：

- 状态：`queued` / `running` / `succeeded` / `failed` / `cancelled`
- 能力：多任务排队、进度（step 字段）、失败后重试（`POST /api/tasks/:id/retry`）、取消排队任务（`POST /api/tasks/:id/cancel`）

