/**
 * Minimal Claude (Anthropic) Messages API CLI using Node's built-in fetch.
 *
 * Usage:
 *   ANTHROPIC_API_KEY=... node scripts/claude.mjs "你好，Claude"
 *
 * Optional env:
 *   ANTHROPIC_MODEL=claude-3-5-sonnet-latest
 *   ANTHROPIC_MAX_TOKENS=512
 *   ANTHROPIC_BASE_URL=https://api.anthropic.com
 */

const prompt = process.argv.slice(2).join(" ").trim();
if (!prompt) {
  console.error('Missing prompt. Example: npm run claude -- "你好，Claude"');
  process.exit(2);
}

const apiKey = process.env.ANTHROPIC_API_KEY?.trim();
if (!apiKey) {
  console.error("Missing ANTHROPIC_API_KEY env var.");
  process.exit(2);
}

const baseUrl = (process.env.ANTHROPIC_BASE_URL || "https://api.anthropic.com").replace(/\/+$/, "");
const model = process.env.ANTHROPIC_MODEL || "claude-3-5-sonnet-latest";
const maxTokens = Number.parseInt(process.env.ANTHROPIC_MAX_TOKENS || "512", 10);

const url = `${baseUrl}/v1/messages`;

const res = await fetch(url, {
  method: "POST",
  headers: {
    "content-type": "application/json",
    "x-api-key": apiKey,
    // Anthropic API version header is required.
    "anthropic-version": "2023-06-01"
  },
  body: JSON.stringify({
    model,
    max_tokens: Number.isFinite(maxTokens) ? maxTokens : 512,
    messages: [{ role: "user", content: prompt }]
  })
});

const text = await res.text();
let json;
try {
  json = JSON.parse(text);
} catch {
  // Keep raw text if not JSON.
}

if (!res.ok) {
  console.error(`Request failed: ${res.status} ${res.statusText}`);
  if (json) console.error(JSON.stringify(json, null, 2));
  else console.error(text);
  process.exit(1);
}

const content = json?.content;
const first = Array.isArray(content) ? content[0] : null;
const out = first?.type === "text" ? first.text : null;
if (!out) {
  console.log(JSON.stringify(json, null, 2));
  process.exit(0);
}

process.stdout.write(out.endsWith("\n") ? out : `${out}\n`);







