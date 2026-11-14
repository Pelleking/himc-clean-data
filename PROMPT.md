# Prompts for ChatGPT or Claude

Both prompts only set the assistant up to query the data. They do not tell it what to look for or
what to conclude: the questions and the judgement are yours.

## With the connector (nothing to download)

Add `https://himc-mcp.pellefredrikson.workers.dev/mcp` as a custom connector (steps in the
[README](README.md)), switch it on in a new chat, and paste:

```
Use the HIMC connector. It gives you read-only access to cleaned tables from a
fictional data room used in a university course. Nothing in it describes a real
hospital, patient or company.

First call `overview` and `list_tables`. Then give me a short, neutral description
of what is in the data: the tables, what one row in each means, and the period
covered. Do not analyse anything yet, do not suggest conclusions, and do not
propose what I should look into. Wait for my questions.

For the rest of this chat:
- Answer by querying the connector, not from memory or assumption.
- Show the numbers behind each answer and name the table and columns they came from.
- Answer the question I asked. Do not add recommendations, judgements or a "so
  what" unless I ask for one.
- If my question could be read in more than one way, say which reading you used,
  or ask. If I ask a leading question, answer it and also show what the data says
  against it.
- Columns starting `dq_` flag data problems (1 = problem present). Tell me when an
  answer rests on flagged rows, on few rows, or on a period with known gaps
  (the table `data_quality_log` lists them).
- If the data cannot answer something, say so instead of estimating.
```

## With the file attached

1. Download [himc.sqlite.gz](https://github.com/Pelleking/himc-clean-data/releases/latest/download/himc.sqlite.gz) (16 MB).
2. Start a new chat and attach the file. In Claude, first switch on *Code execution and file
   creation* under Settings › Capabilities.
3. Paste:

```
I've attached himc.sqlite.gz, a gzip-compressed SQLite database. It holds cleaned
tables from a fictional data room used in a university course. Nothing in it
describes a real hospital, patient or company.

Set it up with your code tool:
1. Unpack it with Python (gzip + shutil) to himc.sqlite and open it with sqlite3.
   If no file is attached, try downloading
   https://github.com/Pelleking/himc-clean-data/releases/latest/download/himc.sqlite.gz
   and if that is not possible, ask me to attach it.
2. Read both rows of the table `_about` in full. They describe the tables, how the
   data was cleaned and what to be careful with.
3. List the tables with their row counts.

Then give me a short, neutral description of what is in the database: the tables,
what one row in each means, and the period covered. Do not analyse anything yet,
do not suggest conclusions, and do not propose what I should look into. Wait for
my questions.

For the rest of this chat:
- Answer by running SQL or Python on the database, not from memory or assumption.
  Keep the database open and reuse it.
- Show the numbers behind each answer and name the table and columns they came from.
- Answer the question I asked. Do not add recommendations, judgements or a "so
  what" unless I ask for one.
- If my question could be read in more than one way, say which reading you used,
  or ask. If I ask a leading question, answer it and also show what the data says
  against it.
- Columns starting `dq_` flag data problems (1 = problem present). Tell me when an
  answer rests on flagged rows, on few rows, or on a period with known gaps
  (the table `data_quality_log` lists them).
- If the data cannot answer something, say so instead of estimating.
```
