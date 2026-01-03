/**
 * Minimal web dashboard for "Trader Agent" MVP (no deps).
 *
 * - Research-only: triggers report generation (sample/akshare) and shows outputs.
 * - Includes an in-process task queue + state machine: multi-task, progress, retry.
 *
 * Start:
 *   npm run web
 *
 * Endpoints:
 * - GET  /                   Dashboard HTML
 * - GET  /api/state          Returns { latestReportPath, latestReport, auditTail }
 * - GET  /api/tasks          List tasks (newest first)
 * - POST /api/tasks          Enqueue a report task. Body JSON: { provider, watchlist, lookback, top, policy }
 * - POST /api/tasks/:id/retry  Retry a failed task
 * - POST /api/tasks/:id/cancel Cancel a queued task
 */

import http from "node:http";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..");

const PORT = Number(process.env.PORT || "3141");
const MAX_TASKS = Number(process.env.MAX_TASKS || "100");
const TASKS_PATH = path.join(repoRoot, "data", "tasks.jsonl");

const DEFAULTS = {
  provider: "sample",
  watchlist: "data/watchlist.example.txt",
  lookback: 3,
  top: 20,
  policy: "policies/default_strict.json"
};

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj, null, 2);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store"
  });
  res.end(body);
}

function sendText(res, status, text, contentType = "text/plain; charset=utf-8") {
  res.writeHead(status, { "content-type": contentType, "cache-control": "no-store" });
  res.end(text);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => (data += chunk));
    req.on("end", () => resolve(data));
    req.on("error", reject);
  });
}

function safeRel(p) {
  try {
    const abs = path.resolve(repoRoot, p);
    if (!abs.startsWith(repoRoot)) return null;
    return abs;
  } catch {
    return null;
  }
}

function findLatestReport() {
  const dir = path.join(repoRoot, "reports");
  if (!fs.existsSync(dir)) return null;
  const files = fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".md"))
    .map((f) => ({ f, mtime: fs.statSync(path.join(dir, f)).mtimeMs }))
    .sort((a, b) => b.mtime - a.mtime);
  if (!files.length) return null;
  return path.join(dir, files[0].f);
}

function tailFile(filePath, maxLines = 80) {
  if (!filePath || !fs.existsSync(filePath)) return "";
  const raw = fs.readFileSync(filePath, "utf8");
  const lines = raw.split(/\r?\n/);
  return lines.slice(Math.max(0, lines.length - maxLines)).join("\n").trim();
}

async function runReport(params) {
  const provider = params.provider || DEFAULTS.provider;
  const watchlist = params.watchlist || DEFAULTS.watchlist;
  const lookback = Number.isFinite(Number(params.lookback)) ? Number(params.lookback) : DEFAULTS.lookback;
  const top = Number.isFinite(Number(params.top)) ? Number(params.top) : DEFAULTS.top;
  const policy = params.policy || DEFAULTS.policy;

  // Constrain paths to repo root
  const wlAbs = safeRel(watchlist);
  const policyAbs = safeRel(policy);
  if (!wlAbs) throw new Error("Invalid watchlist path.");
  if (!policyAbs) throw new Error("Invalid policy path.");

  const py = process.env.PYTHON || "python3";

  const args = [
    path.join(repoRoot, "scripts", "daily_report.py"),
    "--provider",
    String(provider),
    "--watchlist",
    wlAbs,
    "--lookback",
    String(lookback),
    "--top",
    String(top),
    "--policy",
    policyAbs
  ];

  const env = {
    ...process.env,
    PYTHONPATH: path.join(repoRoot, "py")
  };

  return await new Promise((resolve) => {
    const child = spawn(py, args, { cwd: repoRoot, env });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString("utf8")));
    child.stderr.on("data", (d) => (stderr += d.toString("utf8")));
    child.on("close", (code) => {
      resolve({ code, stdout, stderr });
    });
  });
}

// ---- Task queue / state machine ----
const TaskStatus = {
  queued: "queued",
  running: "running",
  succeeded: "succeeded",
  failed: "failed",
  cancelled: "cancelled"
};

function nowIso() {
  return new Date().toISOString();
}

function appendJsonl(filePath, obj) {
  try {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.appendFileSync(filePath, `${JSON.stringify(obj)}\n`, "utf8");
  } catch {
    // best-effort; don't crash server
  }
}

function normalizeParams(p) {
  return {
    provider: p?.provider || DEFAULTS.provider,
    watchlist: p?.watchlist || DEFAULTS.watchlist,
    lookback: Number.isFinite(Number(p?.lookback)) ? Number(p.lookback) : DEFAULTS.lookback,
    top: Number.isFinite(Number(p?.top)) ? Number(p.top) : DEFAULTS.top,
    policy: p?.policy || DEFAULTS.policy
  };
}

let taskSeq = 0;
const tasksById = new Map(); // id -> task
const queue = []; // ids
let runningId = null;

function taskSnapshot(t) {
  return {
    id: t.id,
    type: t.type,
    status: t.status,
    step: t.step,
    attempt: t.attempt,
    createdAt: t.createdAt,
    startedAt: t.startedAt,
    finishedAt: t.finishedAt,
    params: t.params,
    result: t.result,
    error: t.error
  };
}

function listTasks() {
  // newest first
  return Array.from(tasksById.values())
    .sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""))
    .slice(0, MAX_TASKS)
    .map(taskSnapshot);
}

function enqueueReportTask(params) {
  const id = `t_${Date.now()}_${++taskSeq}`;
  const t = {
    id,
    type: "report",
    status: TaskStatus.queued,
    step: "queued",
    attempt: 1,
    createdAt: nowIso(),
    startedAt: null,
    finishedAt: null,
    params: normalizeParams(params),
    result: null,
    error: null
  };
  tasksById.set(id, t);
  queue.push(id);
  appendJsonl(TASKS_PATH, { event: "task_enqueued", at: nowIso(), task: taskSnapshot(t) });
  pumpQueue();
  return t;
}

function cancelTask(id) {
  const t = tasksById.get(id);
  if (!t) return { ok: false, error: "Task not found." };
  if (t.status !== TaskStatus.queued) return { ok: false, error: "Only queued tasks can be cancelled." };
  t.status = TaskStatus.cancelled;
  t.step = "cancelled";
  t.finishedAt = nowIso();
  // remove from queue
  const idx = queue.indexOf(id);
  if (idx >= 0) queue.splice(idx, 1);
  appendJsonl(TASKS_PATH, { event: "task_cancelled", at: nowIso(), task: taskSnapshot(t) });
  return { ok: true, task: taskSnapshot(t) };
}

function retryTask(id) {
  const t = tasksById.get(id);
  if (!t) return { ok: false, error: "Task not found." };
  if (t.status !== TaskStatus.failed) return { ok: false, error: "Only failed tasks can be retried." };
  const id2 = `t_${Date.now()}_${++taskSeq}`;
  const t2 = {
    ...t,
    id: id2,
    status: TaskStatus.queued,
    step: "queued",
    attempt: (t.attempt || 1) + 1,
    createdAt: nowIso(),
    startedAt: null,
    finishedAt: null,
    result: null,
    error: null
  };
  tasksById.set(id2, t2);
  queue.push(id2);
  appendJsonl(TASKS_PATH, { event: "task_retried", at: nowIso(), from: id, to: id2, task: taskSnapshot(t2) });
  pumpQueue();
  return { ok: true, task: taskSnapshot(t2) };
}

async function runTask(t) {
  t.status = TaskStatus.running;
  t.step = "executor_running";
  t.startedAt = nowIso();
  appendJsonl(TASKS_PATH, { event: "task_started", at: nowIso(), task: taskSnapshot(t) });

  try {
    // step: planner/validation
    t.step = "planner_validate";
    appendJsonl(TASKS_PATH, { event: "task_step", at: nowIso(), id: t.id, step: t.step });

    // step: executor
    t.step = "executor_run_report";
    appendJsonl(TASKS_PATH, { event: "task_step", at: nowIso(), id: t.id, step: t.step });

    const res = await runReport(t.params);
    t.result = { code: res.code, stdout: res.stdout.trim(), stderr: res.stderr.trim() };

    // step: audit/artifact are done in python; we just refresh state
    t.step = "artifact_refresh";
    appendJsonl(TASKS_PATH, { event: "task_step", at: nowIso(), id: t.id, step: t.step });

    if (res.code === 0) {
      t.status = TaskStatus.succeeded;
      t.step = "succeeded";
      t.finishedAt = nowIso();
      appendJsonl(TASKS_PATH, { event: "task_succeeded", at: nowIso(), task: taskSnapshot(t) });
    } else {
      t.status = TaskStatus.failed;
      t.step = "failed";
      t.finishedAt = nowIso();
      t.error = `Report command failed (exit=${res.code})`;
      appendJsonl(TASKS_PATH, { event: "task_failed", at: nowIso(), task: taskSnapshot(t) });
    }
  } catch (e) {
    t.status = TaskStatus.failed;
    t.step = "failed";
    t.finishedAt = nowIso();
    t.error = String(e?.message || e);
    appendJsonl(TASKS_PATH, { event: "task_failed", at: nowIso(), task: taskSnapshot(t) });
  }
}

async function pumpQueue() {
  if (runningId) return;
  // skip cancelled tasks
  while (queue.length) {
    const id = queue.shift();
    const t = tasksById.get(id);
    if (!t) continue;
    if (t.status !== TaskStatus.queued) continue;
    runningId = id;
    await runTask(t);
    runningId = null;
    // keep only latest MAX_TASKS tasks in memory
    if (tasksById.size > MAX_TASKS * 2) {
      const ids = Array.from(tasksById.values())
        .sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""))
        .slice(0, MAX_TASKS)
        .map((x) => x.id);
      const keep = new Set(ids);
      for (const k of tasksById.keys()) if (!keep.has(k)) tasksById.delete(k);
    }
    // continue to next queued
  }
}

function dashboardHtml() {
  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>biubiu Trader Agent (MVP)</title>
    <style>
      :root { color-scheme: dark; }
      * { box-sizing: border-box; }
      body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; background: #0b0f14; color: #e6edf3; }
      a { color: inherit; }
      .app { display: grid; grid-template-columns: 260px 1fr 420px; height: 100vh; }
      .sidebar { background: #0f1621; border-right: 1px solid #1f2a37; padding: 14px; overflow: auto; }
      .main { background: #0b0f14; padding: 18px 18px 100px; overflow: auto; position: relative; }
      .right { background: #0f1621; border-left: 1px solid #1f2a37; padding: 14px; overflow: auto; }

      .brand { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
      .brand h1 { font-size: 14px; margin: 0; }
      .pill { display: inline-block; padding: 4px 8px; border-radius: 999px; border: 1px solid #233042; background: #0b111a; font-size: 12px; color: #9fb3c8; }
      .muted { color: #9fb3c8; font-size: 12px; }

      .nav { display: grid; gap: 8px; margin-top: 10px; }
      .nav button { width: 100%; padding: 10px 10px; border-radius: 10px; border: 1px solid #233042; background: #0b111a; color: #e6edf3; cursor: pointer; text-align: left; }
      .nav button:hover { background: #0d1420; }

      .sectionTitle { font-size: 12px; color: #9fb3c8; margin: 14px 0 8px; }
      .taskList { display: grid; gap: 8px; }
      .taskItem { padding: 10px; border-radius: 10px; border: 1px solid #233042; background: #0b111a; cursor: pointer; }
      .taskItem:hover { background: #0d1420; }
      .taskItem.active { border-color: #3a546f; }
      .badge { font-size: 11px; padding: 2px 6px; border-radius: 999px; border: 1px solid #233042; color: #9fb3c8; }
      .badge.ok { border-color: #1f6f4a; color: #98f2c1; }
      .badge.run { border-color: #3a546f; color: #9ecbff; }
      .badge.fail { border-color: #7a2e2e; color: #ffb4b4; }

      .empty { text-align: center; margin-top: 18vh; }
      .empty h2 { font-size: 34px; margin: 0 0 14px; font-weight: 600; color: #c9d4df; }
      .chips { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 12px; }
      .chip { padding: 8px 10px; border-radius: 999px; border: 1px solid #233042; background: #0b111a; color: #e6edf3; cursor: pointer; font-size: 12px; }
      .chip:hover { background: #0d1420; }

      .chat { max-width: 820px; margin: 0 auto; display: grid; gap: 10px; }
      .msg { border: 1px solid #233042; background: #0f1621; border-radius: 12px; padding: 10px 12px; }
      .msg .who { font-size: 12px; color: #9fb3c8; margin-bottom: 6px; }
      .msg pre { margin: 0; white-space: pre-wrap; word-wrap: break-word; }

      .composerWrap { position: fixed; left: 260px; right: 420px; bottom: 0; padding: 12px 18px; background: linear-gradient(180deg, rgba(11,15,20,0) 0%, rgba(11,15,20,0.9) 40%, rgba(11,15,20,1) 100%); }
      .composer { max-width: 820px; margin: 0 auto; border: 1px solid #233042; background: #0b111a; border-radius: 16px; padding: 10px; display: flex; gap: 10px; align-items: center; }
      .composer input { flex: 1; padding: 10px 12px; border-radius: 12px; border: 1px solid #233042; background: #0b111a; color: #e6edf3; }
      .composer button { padding: 10px 12px; border-radius: 12px; border: 1px solid #2b3c52; background: #152235; color: #e6edf3; cursor: pointer; }
      .composer button:hover { background: #182943; }

      .panel { border: 1px solid #233042; background: #0b111a; border-radius: 12px; padding: 10px; margin-top: 10px; }
      .panel h3 { font-size: 12px; margin: 0 0 8px; color: #9fb3c8; }
      .panel pre { margin: 0; white-space: pre-wrap; word-wrap: break-word; max-height: 320px; overflow: auto; }
      .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
      .row button { width: 100%; }

      @media (max-width: 1100px) {
        .app { grid-template-columns: 240px 1fr; }
        .right { display: none; }
        .composerWrap { right: 0; left: 240px; }
      }
      @media (max-width: 760px) {
        .app { grid-template-columns: 1fr; }
        .sidebar { display: none; }
        .composerWrap { left: 0; right: 0; }
      }
    </style>
  </head>
  <body>
    <div class="app">
      <aside class="sidebar">
        <div class="brand">
          <h1>biubiu Trader Agent</h1>
          <span class="pill">Research-only</span>
        </div>
        <div class="muted">Manus 风格：任务驱动 → 工具编排 → Policy Gate → Audit → 交付物</div>

        <div class="nav">
          <button id="btnNewTask">+ 新建任务</button>
        </div>

        <div class="sectionTitle">任务（Tasks）</div>
        <div class="taskList" id="taskList"></div>

        <div class="sectionTitle">提示</div>
        <div class="muted">输入示例：</div>
        <div class="muted">- 生成趋势动量日报（sample）</div>
        <div class="muted">- 生成动量日报 lookback=20 top=20</div>
        <div class="muted">- 用 akshare 生成日报（需安装）</div>
      </aside>

      <main class="main">
        <div class="empty" id="empty">
          <h2>我能为你做什么？</h2>
          <div class="muted">把任务交给我，我会排队执行并产出报告（研究模式）。</div>
          <div class="chips">
            <button class="chip" data-prompt="生成趋势动量日报（sample）">生成趋势动量日报</button>
            <button class="chip" data-prompt="生成动量日报 lookback=20 top=20">动量日报（20日）</button>
            <button class="chip" data-prompt="查看最新报告">查看最新报告</button>
          </div>
        </div>

        <div class="chat" id="chat" style="display:none;"></div>

        <div class="composerWrap">
          <div class="composer">
            <input id="prompt" placeholder="分配一个任务或提问，例如：生成动量日报 lookback=20 top=20" />
            <button id="send">发送</button>
          </div>
          <div class="muted" style="max-width:820px;margin:8px auto 0;">注意：此 MVP 默认研究模式，不提供任何自动下单能力。</div>
        </div>
      </main>

      <aside class="right">
        <div class="brand">
          <h1 style="margin:0;font-size:13px;">产物 / 审计</h1>
        </div>

        <div class="panel">
          <h3>任务详情（Selected Task）</h3>
          <pre id="taskDetail">（未选择）</pre>
          <div class="row" style="margin-top:10px;">
            <button id="btnRetry" type="button">重试</button>
            <button id="btnCancel" type="button">取消</button>
          </div>
          <div class="muted" style="margin-top:8px;">仅支持 failed 重试、queued 取消。</div>
        </div>

        <div class="panel">
          <h3>最新报告（Latest Report）</h3>
          <div class="muted" id="reportPath"></div>
          <pre id="report">（暂无）</pre>
        </div>

        <div class="panel">
          <h3>审计日志（Audit Tail）</h3>
          <pre id="audit">（暂无）</pre>
        </div>
      </aside>
    </div>

    <script>
      const state = {
        selectedTaskId: null,
        tasks: [],
      };

      const emptyEl = document.getElementById("empty");
      const chatEl = document.getElementById("chat");
      const promptEl = document.getElementById("prompt");
      const sendBtn = document.getElementById("send");
      const taskListEl = document.getElementById("taskList");
      const taskDetailEl = document.getElementById("taskDetail");
      const btnRetry = document.getElementById("btnRetry");
      const btnCancel = document.getElementById("btnCancel");
      const btnNewTask = document.getElementById("btnNewTask");
      const reportEl = document.getElementById("report");
      const reportPathEl = document.getElementById("reportPath");
      const auditEl = document.getElementById("audit");

      function addMsg(who, text) {
        emptyEl.style.display = "none";
        chatEl.style.display = "grid";
        const div = document.createElement("div");
        div.className = "msg";
        div.innerHTML = '<div class="who"></div><pre></pre>';
        div.querySelector(".who").textContent = who;
        div.querySelector("pre").textContent = text;
        chatEl.appendChild(div);
        div.scrollIntoView({ block: "end" });
      }

      function renderTaskList() {
        taskListEl.innerHTML = "";
        for (const t of state.tasks) {
          const item = document.createElement("div");
          item.className = "taskItem" + (t.id === state.selectedTaskId ? " active" : "");
          const badgeClass = t.status === "succeeded" ? "ok" : (t.status === "running" ? "run" : (t.status === "failed" ? "fail" : ""));
          item.innerHTML = \`
            <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;">
              <div style="font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">\${t.id}</div>
              <span class="badge \${badgeClass}">\${t.status}</span>
            </div>
            <div class="muted" style="margin-top:6px;">step=\${t.step} • attempt=\${t.attempt}</div>
          \`;
          item.onclick = () => {
            state.selectedTaskId = t.id;
            taskDetailEl.textContent = JSON.stringify(t, null, 2);
            renderTaskList();
          };
          taskListEl.appendChild(item);
        }
      }

      async function refreshState() {
        const res = await fetch("/api/state", { cache: "no-store" });
        const j = await res.json();
        reportPathEl.textContent = j.latestReportPath ? ("路径: " + j.latestReportPath) : "";
        reportEl.textContent = j.latestReport || "（暂无）";
        auditEl.textContent = j.auditTail || "（暂无）";
      }

      async function refreshTasks() {
        const res = await fetch("/api/tasks", { cache: "no-store" });
        const j = await res.json();
        state.tasks = j.tasks || [];
        // auto-select most recent task
        if (!state.selectedTaskId && state.tasks.length) {
          state.selectedTaskId = state.tasks[0].id;
          taskDetailEl.textContent = JSON.stringify(state.tasks[0], null, 2);
        } else if (state.selectedTaskId) {
          const found = state.tasks.find(t => t.id === state.selectedTaskId);
          if (found) taskDetailEl.textContent = JSON.stringify(found, null, 2);
        }
        renderTaskList();
      }

      function parseCommand(s) {
        const text = (s || "").trim();
        if (!text) return null;

        // quick command
        if (text.includes("查看最新报告")) return { kind: "show_latest" };

        // report task (very lightweight parsing)
        const isReport = /日报|report|动量/.test(text);
        if (!isReport) return { kind: "unknown", text };

        const p = {
          provider: text.includes("akshare") ? "akshare" : "sample",
          watchlist: "${DEFAULTS.watchlist}",
          lookback: ${DEFAULTS.lookback},
          top: ${DEFAULTS.top},
          policy: "${DEFAULTS.policy}",
        };

        const mLook = text.match(/lookback\\s*=\\s*(\\d+)/i);
        if (mLook) p.lookback = Number(mLook[1]);
        const mTop = text.match(/top\\s*=\\s*(\\d+)/i);
        if (mTop) p.top = Number(mTop[1]);

        return { kind: "enqueue_report", params: p };
      }

      async function enqueueReport(params) {
        const res = await fetch("/api/tasks", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(params),
        });
        return await res.json();
      }

      async function onSend() {
        const text = promptEl.value;
        promptEl.value = "";
        addMsg("你", text);
        const cmd = parseCommand(text);
        if (!cmd) return;
        if (cmd.kind === "show_latest") {
          await refreshState();
          addMsg("Agent", "已刷新最新报告与审计。");
          return;
        }
        if (cmd.kind === "enqueue_report") {
          const j = await enqueueReport(cmd.params);
          addMsg("Agent", "已入队：\\n" + JSON.stringify(j, null, 2));
          await refreshTasks();
          return;
        }
        addMsg("Agent", "我目前只支持：生成动量日报（研究模式）。例如：\\n- 生成动量日报 lookback=20 top=20\\n- 用 akshare 生成日报");
      }

      sendBtn.addEventListener("click", () => onSend().catch(e => addMsg("Agent", String(e))));
      promptEl.addEventListener("keydown", (e) => { if (e.key === "Enter") onSend().catch(e => addMsg("Agent", String(e))); });
      btnNewTask.addEventListener("click", () => { promptEl.focus(); });

      // chips
      document.querySelectorAll(".chip").forEach(btn => {
        btn.addEventListener("click", () => {
          promptEl.value = btn.getAttribute("data-prompt") || "";
          onSend().catch(() => {});
        });
      });

      btnRetry.addEventListener("click", async () => {
        if (!state.selectedTaskId) return;
        const res = await fetch("/api/tasks/" + state.selectedTaskId + "/retry", { method: "POST" });
        const j = await res.json();
        addMsg("Agent", "重试结果：\\n" + JSON.stringify(j, null, 2));
        await refreshTasks();
      });

      btnCancel.addEventListener("click", async () => {
        if (!state.selectedTaskId) return;
        const res = await fetch("/api/tasks/" + state.selectedTaskId + "/cancel", { method: "POST" });
        const j = await res.json();
        addMsg("Agent", "取消结果：\\n" + JSON.stringify(j, null, 2));
        await refreshTasks();
      });

      // Poll tasks + state
      refreshState().catch(() => {});
      refreshTasks().catch(() => {});
      setInterval(() => { refreshState().catch(()=>{}); refreshTasks().catch(()=>{}); }, 1000);
    </script>
  </body>
</html>`;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);

  if (req.method === "GET" && url.pathname === "/") {
    return sendText(res, 200, dashboardHtml(), "text/html; charset=utf-8");
  }

  if (req.method === "GET" && url.pathname === "/api/state") {
    const latest = findLatestReport();
    const latestText = latest ? fs.readFileSync(latest, "utf8") : "";
    const auditPath = path.join(repoRoot, "data", "audit.jsonl");
    return sendJson(res, 200, {
      latestReportPath: latest ? path.relative(repoRoot, latest) : null,
      latestReport: latestText,
      auditTail: tailFile(auditPath, 80)
    });
  }

  if (req.method === "GET" && url.pathname === "/api/tasks") {
    return sendJson(res, 200, {
      runningId,
      queue: [...queue],
      tasks: listTasks()
    });
  }

  if (req.method === "POST" && url.pathname === "/api/tasks") {
    try {
      const body = await readBody(req);
      const params = JSON.parse(body || "{}");
      const t = enqueueReportTask(params);
      return sendJson(res, 200, { ok: true, task: taskSnapshot(t) });
    } catch (e) {
      return sendJson(res, 400, { ok: false, error: String(e?.message || e) });
    }
  }

  const mRetry = url.pathname.match(/^\/api\/tasks\/([^/]+)\/retry$/);
  if (req.method === "POST" && mRetry) {
    const id = mRetry[1];
    const r = retryTask(id);
    return sendJson(res, r.ok ? 200 : 400, r);
  }

  const mCancel = url.pathname.match(/^\/api\/tasks\/([^/]+)\/cancel$/);
  if (req.method === "POST" && mCancel) {
    const id = mCancel[1];
    const r = cancelTask(id);
    return sendJson(res, r.ok ? 200 : 400, r);
  }

  return sendText(res, 404, "Not Found");
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Web dashboard running: http://127.0.0.1:${PORT}`);
});


