// Remote MCP server for the cleaned HIMC data room, on Cloudflare Workers + D1.
//
// It speaks MCP over Streamable HTTP at /mcp and is stateless: every request is one JSON-RPC
// message answered with plain JSON. No sessions, no sign-in, nothing stored.
//
// The data is FICTIONAL teaching material. The server is read-only: the only way to reach the
// database is `query`, which wraps the caller's SQL inside `SELECT * FROM (...) LIMIT n`, so
// anything that is not a SELECT is a syntax error before it can run.

const VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"];
const MAX_ROWS = 1000;

const FICTIONAL =
  "All of this data is fictional teaching material: the company, the three hospitals and every person in it were invented. " +
  "Never present a result as true of a real hospital.";

const INSTRUCTIONS =
  "Cleaned tables from the HIMC data room: a fictional UK hospital (Kingsmere), a fictional Kenyan county hospital (Kisima), " +
  "a fictional Indonesian regional hospital (Sukamaju) and the back-test of a fictional bed-forecasting prototype. " + FICTIONAL +
  " Start with `overview`, then `list_tables` and `describe_table`, then `query` with SQLite SQL. " +
  "Answer the question asked from the data, show the numbers and the table they came from, and do not volunteer conclusions " +
  "or recommendations the user did not ask for. Columns starting `dq_` flag data-quality problems (1 = problem present); " +
  "`*_raw` columns hold the value before correction. Check `data_quality_log` before trusting a trend.";

const TOOLS = [
  {
    name: "overview",
    description: "What the tables are, how the data was cleaned and the caveats to respect before analysing. Read this first.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "list_tables",
    description: "Every table with its row and column counts. Table names are `<hospital>__<table>`, e.g. `uk_kingsmere__daily_site_panel`.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "describe_table",
    description: "Columns (name and type), row count, three sample rows, and how many rows each data-quality flag marks, for one table.",
    inputSchema: { type: "object", properties: { table: { type: "string", description: "Table name from list_tables" } }, required: ["table"], additionalProperties: false },
  },
  {
    name: "query",
    description:
      "Run one read-only SQLite SELECT (or WITH ... SELECT) and get the rows back. Aggregate in SQL instead of pulling raw rows: " +
      "the stays table has 151,000 rows. At most 1,000 rows are returned; `truncated` says whether more existed. Dates are text in " +
      "YYYY-MM-DD form, so strftime('%Y-%m', date) and date BETWEEN '2025-11-01' AND '2026-10-31' work.",
    inputSchema: {
      type: "object",
      properties: {
        sql: { type: "string", description: "One SELECT statement, SQLite dialect" },
        limit: { type: "integer", description: "Rows to return, 1 to 1000 (default 200)", minimum: 1, maximum: MAX_ROWS },
      },
      required: ["sql"],
      additionalProperties: false,
    },
  },
  {
    name: "data_quality_log",
    description:
      "The 53 problems found while cleaning: what was wrong, how many rows, what was done, how it was found. " +
      'Pass `dataset` (e.g. "uk.spells", "ke.bed_state", or just "uk") to narrow it.',
    inputSchema: { type: "object", properties: { dataset: { type: "string" } }, additionalProperties: false },
  },
].map((t) => ({ ...t, annotations: { readOnlyHint: true, openWorldHint: false } }));

class ToolError extends Error {}

async function tableNames(db) {
  const r = await db.prepare('SELECT "table" FROM _tables ORDER BY 1').all();
  return r.results.map((x) => x.table);
}

const tools = {
  async overview(db) {
    const r = await db.prepare("SELECT topic, text FROM _about ORDER BY rowid").all();
    return r.results.map((x) => x.text).join("\n\n");
  },

  async list_tables(db) {
    return (await db.prepare('SELECT "table", rows, columns FROM _tables ORDER BY 1').all()).results;
  },

  async describe_table(db, { table }) {
    if (!(await tableNames(db)).includes(table)) throw new ToolError(`No table named "${table}". Call list_tables for the names.`);
    const cols = (await db.prepare(`PRAGMA table_info("${table}")`).all()).results;
    const flags = cols.filter((c) => c.name.startsWith("dq_")).map((c) => c.name);
    const counts = {};
    if (flags.length) {
      const sums = flags.map((f) => `SUM(CASE WHEN "${f}" IS NOT NULL AND "${f}" NOT IN (0, '', '0') THEN 1 ELSE 0 END) AS "${f}"`).join(", ");
      Object.assign(counts, (await db.prepare(`SELECT ${sums} FROM "${table}"`).all()).results[0]);
    }
    const rows = (await db.prepare('SELECT rows FROM _tables WHERE "table" = ?').bind(table).first()).rows;
    const sample = (await db.prepare(`SELECT * FROM "${table}" LIMIT 3`).all()).results;
    return { table, rows, columns: cols.map((c) => ({ name: c.name, type: c.type })), quality_flags_rows_affected: counts, sample_rows: sample, note: FICTIONAL };
  },

  async query(db, { sql, limit }) {
    const statement = String(sql || "").trim().replace(/;+\s*$/, "").trim();
    if (!statement) throw new ToolError("Give a SELECT statement in `sql`.");
    if (statement.includes(";")) throw new ToolError("One statement at a time.");
    if (!/^(select|with)\b/i.test(statement)) throw new ToolError("Only SELECT statements are allowed: this server is read-only.");
    const n = Math.max(1, Math.min(Number.isFinite(+limit) && +limit > 0 ? Math.floor(+limit) : 200, MAX_ROWS));
    let res;
    try {
      // Wrapping makes anything but a SELECT a syntax error, and stops a huge result before it is built.
      res = await db.prepare(`SELECT * FROM (${statement}\n) LIMIT ${n + 1}`).raw({ columnNames: true });
    } catch (e) {
      throw new ToolError("SQL error: " + String(e.message || e).replace(/^D1_ERROR:\s*/, ""));
    }
    const [columns, ...rows] = res;
    return { columns: columns || [], rows: rows.slice(0, n), row_count: Math.min(rows.length, n), truncated: rows.length > n, note: "Fictional teaching data." };
  },

  async data_quality_log(db, { dataset }) {
    const q = dataset
      ? db.prepare("SELECT * FROM data_quality_log WHERE dataset LIKE ? ORDER BY rowid").bind(`%${dataset}%`)
      : db.prepare("SELECT * FROM data_quality_log ORDER BY rowid");
    return (await q.all()).results;
  },
};

async function handle(msg, env) {
  const { id, method, params = {} } = msg;
  const ok = (result) => ({ jsonrpc: "2.0", id, result });
  const fail = (code, message) => ({ jsonrpc: "2.0", id, error: { code, message } });
  switch (method) {
    case "initialize":
      return ok({
        protocolVersion: VERSIONS.includes(params.protocolVersion) ? params.protocolVersion : VERSIONS[0],
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "himc", title: "HIMC clean data (fictional)", version: "0.1.2" },
        instructions: INSTRUCTIONS,
      });
    case "ping":
      return ok({});
    case "tools/list":
      return ok({ tools: TOOLS });
    case "tools/call": {
      const fn = tools[params.name];
      if (!fn) return fail(-32602, `Unknown tool: ${params.name}`);
      try {
        const out = await fn(env.DB, params.arguments || {});
        const text = typeof out === "string" ? out : JSON.stringify(out, null, 1);
        const result = { content: [{ type: "text", text }], isError: false };
        if (typeof out === "object" && !Array.isArray(out)) result.structuredContent = out;
        return ok(result);
      } catch (e) {
        const text = e instanceof ToolError ? e.message : "The server hit an unexpected error running this tool.";
        if (!(e instanceof ToolError)) console.error(params.name, e);
        return ok({ content: [{ type: "text", text }], isError: true });
      }
    }
    case "resources/list":
      return ok({ resources: [] });
    case "prompts/list":
      return ok({ prompts: [] });
    default:
      return fail(-32601, `Method not found: ${method}`);
  }
}

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Accept, Authorization, Mcp-Session-Id, MCP-Protocol-Version, Last-Event-ID",
  "Access-Control-Expose-Headers": "Mcp-Session-Id",
};
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...CORS } });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    if (url.pathname === "/mcp" || url.pathname === "/mcp/") {
      if (request.method === "POST") {
        let body;
        try {
          body = await request.json();
        } catch {
          return json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }, 400);
        }
        const batch = Array.isArray(body);
        const answers = [];
        for (const msg of batch ? body : [body]) {
          if (msg && msg.method && msg.id !== undefined && msg.id !== null) answers.push(await handle(msg, env));
          // notifications and responses from the client need no answer
        }
        if (!answers.length) return new Response(null, { status: 202, headers: CORS });
        return json(batch ? answers : answers[0]);
      }
      // No server-initiated stream and no sessions to end.
      return new Response("This MCP endpoint answers POST requests only.", { status: 405, headers: { Allow: "POST, OPTIONS", ...CORS } });
    }

    if (url.pathname === "/") {
      return new Response(
        "HIMC clean data: a read-only MCP server over fictional hospital teaching data.\n\n" +
          `Add this address as a custom connector in Claude or ChatGPT:\n\n  ${url.origin}/mcp\n\n` +
          "More: https://github.com/Pelleking/himc-clean-data\n",
        { headers: { "Content-Type": "text/plain; charset=utf-8" } },
      );
    }
    return new Response("Not found", { status: 404 });
  },
};
