"""MCP server that lets an AI assistant read the cleaned HIMC data room.

The data is FICTIONAL: Wardline Health, Kingsmere Royal Infirmary, Kisima County Referral Hospital
and RSUD Sukamaju were invented for the Hyper Island module Intelligent Machines.

The server is read-only. It opens the SQLite database in read-only mode, accepts a single
SELECT statement at a time, and caps the rows it returns.
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import time
from importlib import resources
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

MAX_ROWS = 1000
MAX_SECONDS = 20

FICTIONAL = ("All of this data is fictional teaching material: the company, the three hospitals and every person in it were invented. "
             "Say so when you report a result, and never present a finding as true of a real hospital.")

mcp = MCPServer("himc", instructions=(
    "Cleaned tables from the HIMC data room: a fictional UK hospital (Kingsmere), a fictional Kenyan county hospital (Kisima), "
    "a fictional Indonesian regional hospital (Sukamaju) and the back-test of a fictional bed-forecasting prototype. " + FICTIONAL +
    " Start with `overview`, then `list_tables` and `describe_table`, then `query` with SQLite SQL. "
    "Columns starting `dq_` flag data-quality problems (1 = problem present); `*_raw` columns hold the value before correction. "
    "Check `data_quality_log` before trusting a trend."))


def database() -> Path:
    """Path to the SQLite file. It ships compressed inside the package and is unpacked once."""
    if os.environ.get("HIMC_DB"):
        return Path(os.environ["HIMC_DB"])
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "himc-mcp"
    target = cache / "himc-0.1.1.sqlite"
    if not target.exists():
        cache.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(".part")
        with resources.files("himc_mcp").joinpath("data/himc.sqlite.gz").open("rb") as zipped, gzip.open(zipped) as src, open(part, "wb") as dst:
            shutil.copyfileobj(src, dst)
        part.replace(target)
    return target


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{database()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def tables(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY name")]


@mcp.tool()
def overview() -> str:
    """What the data is, how it was cleaned, the caveats to respect before analysing, and starter questions. Read this first."""
    text = resources.files("himc_mcp").joinpath("data/README_CLEAN_DATA.md").read_text(encoding="utf-8")
    return "NOTE: " + FICTIONAL + "\n\n" + text


@mcp.tool()
def list_tables() -> list[dict]:
    """Every table with its row and column counts. Table names are `<hospital>__<table>`, e.g. `uk_kingsmere__daily_site_panel`."""
    con = connect()
    out = []
    for t in tables(con):
        cols = con.execute(f'PRAGMA table_info("{t}")').fetchall()
        out.append({"table": t, "rows": con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0], "columns": len(cols)})
    con.close()
    return out


@mcp.tool()
def describe_table(table: str) -> dict:
    """Columns (name and type), row count, three sample rows, and which columns are data-quality flags, for one table."""
    con = connect()
    if table not in tables(con):
        con.close()
        raise ToolError(f"No table named {table!r}. Call list_tables for the names.")
    cols = con.execute(f'PRAGMA table_info("{table}")').fetchall()
    sample = [dict(r) for r in con.execute(f'SELECT * FROM "{table}" LIMIT 3')]
    flags = {}
    for c in cols:
        if c["name"].startswith("dq_"):
            flags[c["name"]] = con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{c["name"]}" NOT IN (0, \'\', \'0\') AND "{c["name"]}" IS NOT NULL').fetchone()[0]
    out = {"table": table, "rows": con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],
           "columns": [{"name": c["name"], "type": c["type"]} for c in cols], "quality_flags_rows_affected": flags, "sample_rows": sample,
           "note": FICTIONAL}
    con.close()
    return out


@mcp.tool()
def query(sql: str, limit: int = 200) -> dict:
    """Run one read-only SQLite SELECT (or WITH ... SELECT) and get the rows back.

    Aggregate in SQL instead of pulling raw rows: the stays table has 151,000 rows. At most 1,000 rows
    are returned; `truncated` says whether more existed. Dates are text in YYYY-MM-DD form, so
    `strftime('%Y-%m', date)` and `date BETWEEN '2025-11-01' AND '2026-10-31'` work.
    """
    statement = sql.strip().rstrip(";").strip()
    if ";" in statement:
        raise ToolError("One statement at a time.")
    if statement.split(None, 1)[0].lower() not in ("select", "with"):
        raise ToolError("Only SELECT statements are allowed: this server is read-only.")
    limit = max(1, min(int(limit), MAX_ROWS))
    con = connect()
    deadline = time.monotonic() + MAX_SECONDS
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        cur = con.execute(statement)
        rows = cur.fetchmany(limit + 1)
    except sqlite3.OperationalError as e:
        raise ToolError(f"The query took longer than {MAX_SECONDS} seconds and was stopped. Narrow it or aggregate."
                         if "interrupted" in str(e) else f"SQL error: {e}") from None
    finally:
        con.close()
    names = [d[0] for d in cur.description or []]
    return {"columns": names, "rows": [list(r) for r in rows[:limit]], "row_count": min(len(rows), limit), "truncated": len(rows) > limit,
            "note": "Fictional teaching data."}


@mcp.tool()
def data_quality_log(dataset: str | None = None) -> list[dict]:
    """The 53 problems found while cleaning: what was wrong, how many rows, what was done, how it was found.
    Pass `dataset` (e.g. "uk.spells", "ke.bed_state") to see one table's issues, or part of a name such as "uk"."""
    con = connect()
    if dataset:
        rows = con.execute("SELECT * FROM data_quality_log WHERE dataset LIKE ? ORDER BY rowid", (f"%{dataset}%",)).fetchall()
    else:
        rows = con.execute("SELECT * FROM data_quality_log ORDER BY rowid").fetchall()
    con.close()
    return [dict(r) for r in rows]


def main() -> None:
    database()   # unpack before the first request, so the first tool call is not slow
    mcp.run()


if __name__ == "__main__":
    main()
