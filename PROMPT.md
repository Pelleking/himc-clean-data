# One prompt for ChatGPT or Claude

1. Download [himc.sqlite.gz](https://github.com/Pelleking/himc-clean-data/releases/latest/download/himc.sqlite.gz) (16 MB).
2. Start a new chat in ChatGPT or Claude and attach the file.
   In Claude, first switch on *Code execution and file creation* under Settings › Capabilities.
3. Paste everything in the box below and send it.

```
I've attached himc.sqlite.gz. It is a gzip-compressed SQLite database with cleaned
data from a FICTIONAL hospital data room used in a university course: a UK hospital
(Kingsmere), a Kenyan county hospital (Kisima), an Indonesian regional hospital
(Sukamaju), and the back-test of a bed-forecasting prototype called Horizon. No real
hospital, patient or company is in it.

Set it up with your code tool:
1. Unpack it with Python (gzip + shutil) to himc.sqlite and open it with sqlite3.
   If no file is attached, try downloading
   https://github.com/Pelleking/himc-clean-data/releases/latest/download/himc.sqlite.gz
   and if that is not possible, ask me to attach it.
2. Read both rows of the table `_about` in full. They explain every table, how the
   data was cleaned, and the caveats to respect.
3. List the tables with their row counts, and read the table `data_quality_log`.

Then tell me, in plain language and briefly: what data there is, its three biggest
weaknesses, and five questions it could answer for my role. My role is:
[business and strategy / data and AI / responsible innovation / customer experience].
My team has to recommend whether the company should proceed, pilot, redesign, defer
or reject Horizon.

Rules for the rest of this chat:
- Answer my questions by running SQL or Python on the database, never from memory
  or guesswork. Keep the database open and reuse it.
- Show the numbers behind each answer and name the table they came from. Offer a
  chart when it helps.
- Columns starting `dq_` flag data problems (1 = problem present): say when an
  answer depends on flagged rows, and check `data_quality_log` before trusting a trend.
- If the data cannot answer something, say so instead of estimating.
- The data is fictional. Never present a result as true of a real hospital.
```

## Questions to try next

- How often was Kingsmere at its highest alert level, and in which months?
- Does Horizon beat "same as yesterday", and how often does the real number land inside its 80% range?
- How many planned operations were cancelled because there was no bed?
- How far ahead are temporary nurses booked, and what does short notice cost?
- Who waits longest in the emergency department for a bed?
- How much of Kisima's hand-written bed data is missing or does not add up?
- What changed at Sukamaju after the beds were cut in July 2025?

The assistant can be wrong, and some figures in the data room are wrong on purpose. Treat an
answer as a lead to check.
