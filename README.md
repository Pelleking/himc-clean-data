# HIMC clean data

Cleaned tables from the [HIMC data room](https://github.com/wendinstefan/HIMC), plus an MCP server
so an AI assistant (Claude, or any other MCP client) can query them directly.

> **Everything here is fictional.** The data room was written for the Hyper Island module
> *Intelligent Machines*. Wardline Health, Kingsmere Royal Infirmary, Kisima County Referral Hospital
> and RSUD Sukamaju do not exist, and no real patient, staff or company data is in it. A result
> from this data says nothing about a real hospital.

The raw files, the brief and the inject cards are **not** in this repository: get them from the
[original repository](https://github.com/wendinstefan/HIMC). This one holds only the cleaned tables
of the three hospitals and the prototype back-test.

## What is in it

| Path | What it is |
|---|---|
| `clean/README_CLEAN_DATA.md` | **Read this first.** What each table is, the cleaning rules, and the caveats |
| `clean/catalog.csv` | Every table with its row and column counts |
| `clean/data_quality_log.csv` | The 53 problems found: what was wrong, how many rows, what was done |
| `clean/uk_kingsmere/`, `clean/kenya_kisima/`, `clean/indonesia_sukamaju/`, `clean/prototype/` | The 25 cleaned tables as CSV |
| `cleaning/clean_data.py` | The script that produced them from the raw files |
| `src/himc_mcp/` | The MCP server, with the same tables as one SQLite database |

`clean/uk_kingsmere/spells.csv.gz` (151,217 hospital stays) is compressed because it is 64 MB as
plain CSV. Double-click to unpack it, or read it directly: `pd.read_csv("clean/uk_kingsmere/spells.csv.gz")`.

## Use it in ChatGPT or Claude with nothing to install

Download **[himc.sqlite.gz](https://github.com/Pelleking/himc-clean-data/releases/latest/download/himc.sqlite.gz)**
(16 MB: every table in one database), attach it to a new chat and paste the prompt in
[`PROMPT.md`](PROMPT.md). The assistant unpacks it with its own code tool and answers questions
by querying it. In Claude, switch on *Code execution and file creation* under Settings › Capabilities first.

## Use it from an AI assistant (MCP)

The server is read-only: it answers `SELECT` queries and cannot change anything. You need
[uv](https://docs.astral.sh/uv/getting-started/installation/) installed; nothing else, and no
need to clone this repository.

**Claude Code**

```sh
claude mcp add himc -- uvx --from git+https://github.com/Pelleking/himc-clean-data himc-mcp
```

**Claude Desktop** (Settings › Developer › Edit Config), and most other MCP clients:

```json
{
  "mcpServers": {
    "himc": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Pelleking/himc-clean-data", "himc-mcp"]
    }
  }
}
```

Restart the client. The first start takes a little longer: it downloads the package and unpacks
the database (about 100 MB) into `~/.cache/himc-mcp`.

Then ask in plain language, for example:

- "Using the himc tools, what is in the data and what should I be careful with?"
- "How often was Kingsmere at alert level 4, by month?"
- "Does the Horizon prototype beat 'same as yesterday', and how often does the real value fall inside its 80% range?"
- "What share of Kisima's ward-nights are missing or don't add up?"

### Tools

| Tool | What it does |
|---|---|
| `overview` | The data notes: tables, cleaning rules, caveats, starter questions |
| `list_tables` | All 26 tables with row and column counts |
| `describe_table` | Columns, types, sample rows and data-quality flag counts for one table |
| `query` | One read-only SQLite `SELECT`; at most 1,000 rows back, 20-second limit |
| `data_quality_log` | The cleaning log, optionally for one dataset |

Columns starting `dq_` flag a problem (1 = present). Columns ending `_raw` hold the value before
correction.

## Use it without an assistant

Open the CSV files in any spreadsheet or notebook. For SQL, unpack the database:

```sh
gunzip -k src/himc_mcp/data/himc.sqlite.gz
sqlite3 src/himc_mcp/data/himc.sqlite "SELECT horizon_days, mae_model, mae_naive_last FROM prototype__backtest_summary LIMIT 7"
```

## Run the server from a clone

```sh
git clone https://github.com/Pelleking/himc-clean-data && cd himc-clean-data
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/himc-mcp        # speaks MCP over stdin/stdout
```

## Credits and terms

The data room is by the authors of [wendinstefan/HIMC](https://github.com/wendinstefan/HIMC), made
for teaching. That repository states no licence, so this copy is shared for use within the course;
ask its authors before using the data for anything else. The MCP server code in `src/` is MIT-licensed.
