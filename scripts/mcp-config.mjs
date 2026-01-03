/**
 * Minimal MCP config helper (no deps).
 *
 * Goal: make it easy to "link more MCP servers" by generating/merging entries
 * into a JSON config file that looks like:
 *   { "mcpServers": { "<name>": { "command": "...", "args": [...], "env": {...} } } }
 *
 * Usage:
 *   node scripts/mcp-config.mjs help
 *
 *   # list servers
 *   node scripts/mcp-config.mjs list --config "/path/to/config.json"
 *
 *   # add a server from a built-in template
 *   node scripts/mcp-config.mjs add filesystem --name fs --root "/abs/path" --config "/path/to/config.json"
 *   node scripts/mcp-config.mjs add git --name git --root "/abs/path" --config "/path/to/config.json"
 *
 *   # add a custom server
 *   node scripts/mcp-config.mjs add-custom --name my --command npx --args "-y,@scope/pkg,--flag,value" --config "/path/to/config.json"
 *
 * Notes:
 * - This script does NOT start any MCP servers; it only writes config.
 * - Works with configs used by clients like Claude Desktop / Cursor (format is similar).
 */

import fs from "node:fs";
import path from "node:path";
import os from "node:os";

function die(msg, code = 2) {
  console.error(msg);
  process.exit(code);
}

function expandHome(p) {
  if (!p) return p;
  if (p === "~") return os.homedir();
  if (p.startsWith("~/")) return path.join(os.homedir(), p.slice(2));
  return p;
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) {
      out._.push(a);
      continue;
    }
    const key = a.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      out[key] = true;
    } else {
      out[key] = next;
      i++;
    }
  }
  return out;
}

function readJsonMaybe(filePath) {
  if (!fs.existsSync(filePath)) return null;
  const raw = fs.readFileSync(filePath, "utf8");
  if (!raw.trim()) return null;
  try {
    return JSON.parse(raw);
  } catch (e) {
    die(`Invalid JSON in config: ${filePath}\n${String(e?.message || e)}`);
  }
}

function ensureDirForFile(filePath) {
  const dir = path.dirname(filePath);
  fs.mkdirSync(dir, { recursive: true });
}

function writeJson(filePath, obj) {
  ensureDirForFile(filePath);
  fs.writeFileSync(filePath, `${JSON.stringify(obj, null, 2)}\n`, "utf8");
}

function normalizeConfigObject(obj) {
  if (obj == null) return { mcpServers: {} };
  if (typeof obj !== "object" || Array.isArray(obj)) die("Config JSON must be an object.");
  if (!obj.mcpServers) obj.mcpServers = {};
  if (typeof obj.mcpServers !== "object" || Array.isArray(obj.mcpServers)) die("Config.mcpServers must be an object.");
  return obj;
}

function printHelp() {
  const claudeDesktopDefault =
    process.platform === "darwin"
      ? "~/Library/Application Support/Claude/claude_desktop_config.json"
      : "(varies by OS)";
  const cursorProjectDefault = "./.cursor/mcp.json";
  const cursorUserDefault = "~/.cursor/mcp.json";

  console.log(`
MCP config helper

Commands:
  help
  list --config <path>
  add <template> --name <serverName> [--root <absPath>] [--db <dbPath>] [--npm-userconfig <path>] --config <path>
  add-custom --name <serverName> --command <cmd> --args <csv> [--env <k=v,k2=v2>] [--npm-userconfig <path>] --config <path>
  remove --name <serverName> --config <path>

Templates:
  filesystem   npx -y @modelcontextprotocol/server-filesystem <root>
  git          npx -y @cyanheads/git-mcp-server@latest  (uses env GIT_BASE_DIR=<root>)
  playwright   npx -y @playwright/mcp@latest --browser chromium --image-responses omit
  fetch        npx -y -p @thlee/fetch-mcp@0.0.5 mcp-server-fetch
  sqlite       node scripts/sqlite-mcp.mjs --db <dbPath>  (local, no deps)
  code-runner  npx -y mcp-server-code-runner@latest

Examples (Claude Desktop config default on macOS):
  node scripts/mcp-config.mjs add filesystem --name fs --root "${process.cwd()}" --config "${claudeDesktopDefault}"
  node scripts/mcp-config.mjs add git --name git --root "${process.cwd()}" --config "${claudeDesktopDefault}"
  node scripts/mcp-config.mjs list --config "${claudeDesktopDefault}"

Examples (Cursor config):
  # project-level (recommended for per-repo automation)
  node scripts/mcp-config.mjs add filesystem --name fs --root "${process.cwd()}" --config "${cursorProjectDefault}"
  node scripts/mcp-config.mjs add git --name git --root "${process.cwd()}" --config "${cursorProjectDefault}"
  node scripts/mcp-config.mjs add playwright --name pw --config "${cursorProjectDefault}"
  node scripts/mcp-config.mjs add fetch --name fetch --config "${cursorProjectDefault}"
  node scripts/mcp-config.mjs add sqlite --name db --db "${process.cwd()}/data/biubiu.db" --config "${cursorProjectDefault}"
  node scripts/mcp-config.mjs add code-runner --name code --config "${cursorProjectDefault}"

  # if you have npm auth/captcha issues, force MCP servers to use a clean npm config:
  # node scripts/mcp-config.mjs add git --name git --root "${process.cwd()}" --npm-userconfig "./npmrc.mcp" --config "${cursorProjectDefault}"

  # user-level (applies to all repos)
  node scripts/mcp-config.mjs add filesystem --name fs --root "${process.cwd()}" --config "${cursorUserDefault}"
`.trim());
}

function parseCsvArgs(csv) {
  // Simple CSV split (no quotes). Good enough for typical MCP args.
  return String(csv || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseEnvKVs(s) {
  if (!s) return undefined;
  const env = {};
  for (const part of String(s).split(",")) {
    const p = part.trim();
    if (!p) continue;
    const idx = p.indexOf("=");
    if (idx === -1) die(`Invalid --env entry "${p}". Expected k=v.`);
    const k = p.slice(0, idx).trim();
    const v = p.slice(idx + 1).trim();
    if (!k) die(`Invalid --env entry "${p}". Empty key.`);
    env[k] = v;
  }
  return env;
}

function templateServer(templateName, { root, db }) {
  const n = String(templateName || "").toLowerCase();
  if (n === "filesystem") {
    if (!root) die("Missing --root for filesystem template.");
    return {
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-filesystem", root]
    };
  }
  if (n === "git") {
    if (!root) die("Missing --root for git template.");
    return {
      command: "npx",
      args: ["-y", "@cyanheads/git-mcp-server@latest"],
      env: {
        MCP_TRANSPORT_TYPE: "stdio",
        MCP_LOG_LEVEL: "info",
        // Restrict git operations to this directory tree (acts as a safety sandbox).
        GIT_BASE_DIR: root
      }
    };
  }
  if (n === "playwright") {
    return {
      command: "npx",
      // Use chromium by default to avoid relying on a locally-installed Chrome app.
      // NOTE: @playwright/mcp currently supports: --image-responses allow|omit
      args: ["-y", "@playwright/mcp@latest", "--browser", "chromium", "--image-responses", "omit"],
      env: {
        // Optional, but useful when debugging Playwright behavior.
        DEBUG: "pw:*"
      }
    };
  }
  if (n === "fetch") {
    return {
      command: "npx",
      // Use an actively runnable fetch MCP server. (Some other packages publish broken bins.)
      args: ["-y", "-p", "@thlee/fetch-mcp@0.0.5", "mcp-server-fetch"]
    };
  }
  if (n === "sqlite") {
    if (!db) die('sqlite template requires --db "<db_path>" (e.g. --db "./data/biubiu.db")');
    return {
      command: "node",
      args: ["scripts/sqlite-mcp.mjs", "--db", db]
    };
  }
  if (n === "code-runner") {
    return {
      command: "npx",
      args: ["-y", "mcp-server-code-runner@latest"]
    };
  }
  die(`Unknown template "${templateName}". Supported: filesystem, git, playwright, fetch, sqlite, code-runner`);
}

function applyNpmUserconfig(server, npmUserconfigPath) {
  if (!npmUserconfigPath) return server;
  const p = path.resolve(expandHome(String(npmUserconfigPath)));
  const env = { ...(server.env || {}) };
  // Make npm/npx use a specific user config file (avoids global ~/.npmrc token issues).
  env.NPM_CONFIG_USERCONFIG = p;
  return { ...server, env };
}

const argv = process.argv.slice(2);
const cmd = argv[0] || "help";
const args = parseArgs(argv.slice(1));

if (cmd === "help" || cmd === "--help" || cmd === "-h") {
  printHelp();
  process.exit(0);
}

const configPath = expandHome(args.config);
if (!configPath) die('Missing required flag: --config "<path>"');

const configObj = normalizeConfigObject(readJsonMaybe(configPath));

if (cmd === "list") {
  const servers = configObj.mcpServers || {};
  const names = Object.keys(servers).sort();
  if (names.length === 0) {
    console.log("(no mcpServers)");
    process.exit(0);
  }
  for (const name of names) {
    const s = servers[name] || {};
    console.log(`${name}: ${s.command || "?"} ${(Array.isArray(s.args) ? s.args.join(" ") : "")}`.trim());
  }
  process.exit(0);
}

if (cmd === "remove") {
  const name = args.name;
  if (!name) die("Missing --name <serverName>");
  if (!configObj.mcpServers[name]) die(`Server not found: ${name}`, 1);
  delete configObj.mcpServers[name];
  writeJson(configPath, configObj);
  console.log(`Removed: ${name}`);
  process.exit(0);
}

if (cmd === "add") {
  const templateName = args._[0];
  if (!templateName) die("Missing template name. Example: add filesystem ...");
  const name = args.name;
  if (!name) die("Missing --name <serverName>");
  const root = args.root ? path.resolve(expandHome(args.root)) : undefined;
  const db = args.db ? path.resolve(expandHome(args.db)) : undefined;
  const server = applyNpmUserconfig(templateServer(templateName, { root, db }), args["npm-userconfig"]);
  configObj.mcpServers[name] = server;
  writeJson(configPath, configObj);
  console.log(`Added/updated: ${name}`);
  process.exit(0);
}

if (cmd === "add-custom") {
  const name = args.name;
  if (!name) die("Missing --name <serverName>");
  const command = args.command;
  if (!command) die("Missing --command <cmd>");
  const csv = args.args;
  if (!csv) die('Missing --args "<csv>" (comma-separated)');
  const env = parseEnvKVs(args.env);

  const server = { command, args: parseCsvArgs(csv) };
  if (env && Object.keys(env).length) server.env = env;

  configObj.mcpServers[name] = applyNpmUserconfig(server, args["npm-userconfig"]);
  writeJson(configPath, configObj);
  console.log(`Added/updated: ${name}`);
  process.exit(0);
}

die(`Unknown command "${cmd}". Try: node scripts/mcp-config.mjs help`);


