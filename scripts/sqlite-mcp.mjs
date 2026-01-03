/**
 * Local SQLite MCP server (stdio) using system `sqlite3` CLI.
 * No third-party deps, avoids Python/pip issues.
 *
 * Tools:
 * - list_tables()
 * - describe_table({ table })
 * - read_query({ sql })   // SELECT recommended
 * - write_query({ sql })  // INSERT/UPDATE/DELETE/DDL
 *
 * Usage:
 *   node scripts/sqlite-mcp.mjs --db /abs/path/to.db
 */

import { spawn } from "node:child_process";

function die(msg, code = 2) {
  console.error(msg);
  process.exit(code);
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--db") {
      out.db = argv[++i];
      continue;
    }
    if (a === "--help" || a === "-h") out.help = true;
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
if (args.help) {
  console.log("Usage: node scripts/sqlite-mcp.mjs --db /abs/path/to.db");
  process.exit(0);
}
const dbPath = args.db;
if (!dbPath) die('Missing --db "/abs/path/to.db"');

function sqliteJson(sql) {
  return new Promise((resolve, reject) => {
    const child = spawn("sqlite3", ["-json", dbPath, sql], {
      stdio: ["ignore", "pipe", "pipe"]
    });
    let out = "";
    let err = "";
    child.stdout.on("data", (d) => (out += d.toString("utf8")));
    child.stderr.on("data", (d) => (err += d.toString("utf8")));
    child.on("close", (code) => {
      if (code === 0) {
        try {
          resolve(out.trim() ? JSON.parse(out) : []);
        } catch (e) {
          reject(new Error(`sqlite3 output not JSON: ${String(e?.message || e)}\n${out}`));
        }
      } else {
        reject(new Error(err.trim() || `sqlite3 exited ${code}`));
      }
    });
  });
}

function sqliteExec(sql) {
  return new Promise((resolve, reject) => {
    const child = spawn("sqlite3", [dbPath, sql], {
      stdio: ["ignore", "pipe", "pipe"]
    });
    let out = "";
    let err = "";
    child.stdout.on("data", (d) => (out += d.toString("utf8")));
    child.stderr.on("data", (d) => (err += d.toString("utf8")));
    child.on("close", (code) => {
      if (code === 0) resolve(out.trim());
      else reject(new Error(err.trim() || `sqlite3 exited ${code}`));
    });
  });
}

// ---- MCP (minimal) ----
// We implement enough of MCP to satisfy Cursor: initialize + tools/list + tools/call.

const tools = [
  {
    name: "list_tables",
    description: "List all tables in the SQLite database.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false }
  },
  {
    name: "describe_table",
    description: "Describe a table schema using PRAGMA table_info.",
    inputSchema: {
      type: "object",
      properties: { table: { type: "string" } },
      required: ["table"],
      additionalProperties: false
    }
  },
  {
    name: "read_query",
    description: "Run a SELECT query and return rows as JSON.",
    inputSchema: {
      type: "object",
      properties: { sql: { type: "string" } },
      required: ["sql"],
      additionalProperties: false
    }
  },
  {
    name: "write_query",
    description: "Run a write query (INSERT/UPDATE/DELETE/DDL). Returns OK or error.",
    inputSchema: {
      type: "object",
      properties: { sql: { type: "string" } },
      required: ["sql"],
      additionalProperties: false
    }
  }
];

function send(msg) {
  process.stdout.write(`${JSON.stringify(msg)}\n`);
}

function ok(id, result) {
  send({ jsonrpc: "2.0", id, result });
}

function err(id, message) {
  // Some MCP clients validate that responses must have id: string|number.
  // If we don't have a valid id (e.g. notifications or malformed input), don't respond.
  if (typeof id !== "string" && typeof id !== "number") return;
  send({ jsonrpc: "2.0", id, error: { code: -32000, message } });
}

async function handleCall(name, argsObj) {
  if (name === "list_tables") {
    const rows = await sqliteJson("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name;");
    return { content: [{ type: "text", text: JSON.stringify(rows, null, 2) }] };
  }
  if (name === "describe_table") {
    const t = argsObj?.table;
    const rows = await sqliteJson(`PRAGMA table_info(${JSON.stringify(t)});`);
    return { content: [{ type: "text", text: JSON.stringify(rows, null, 2) }] };
  }
  if (name === "read_query") {
    const sql = String(argsObj?.sql || "");
    const rows = await sqliteJson(sql);
    return { content: [{ type: "text", text: JSON.stringify(rows, null, 2) }] };
  }
  if (name === "write_query") {
    const sql = String(argsObj?.sql || "");
    await sqliteExec(sql);
    return { content: [{ type: "text", text: "OK" }] };
  }
  throw new Error(`Unknown tool: ${name}`);
}

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", async (chunk) => {
  buf += chunk;
  while (true) {
    const idx = buf.indexOf("\n");
    if (idx === -1) break;
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (!line) continue;

    let msg;
    try {
      msg = JSON.parse(line);
    } catch (e) {
      continue;
    }

    const { id, method, params } = msg || {};
    try {
      if (method === "initialize") {
        ok(id, {
          protocolVersion: "2024-11-05",
          capabilities: { tools: {} },
          serverInfo: { name: "biubiu-sqlite-mcp", version: "0.1.0" }
        });
        continue;
      }
      if (method === "tools/list") {
        ok(id, { tools });
        continue;
      }
      if (method === "tools/call") {
        const res = await handleCall(params?.name, params?.arguments);
        ok(id, res);
        continue;
      }
      // default: method not found
      err(id, `Method not supported: ${method}`);
    } catch (e) {
      err(id, String(e?.message || e));
    }
  }
});


