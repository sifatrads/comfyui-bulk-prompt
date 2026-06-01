# ComfyUI-BulkPrompt

Batch-run your workflow once per prompt from a **CSV file**, a **Google Sheets URL**, or **pasted text**, and optionally improve each prompt with a local **Ollama** model.

**No Python dependencies** — every node uses only the standard library (`csv`, `urllib`, `json`). The optional 🦙 Ollama Improver node talks to a running [Ollama](https://ollama.com) server over its HTTP REST API (nothing to `pip install`).

---

## Installation

1. Clone (or copy) this repo into your ComfyUI `custom_nodes` folder:
   ```bash
   cd ComfyUI/custom_nodes
   git clone https://github.com/sifatrads/comfyui-bulk-prompt.git
   ```
2. Restart ComfyUI.
3. The nodes appear under the category **BulkPrompt** in the node menu.

No `pip install` step is required. The optional 🦙 Ollama Improver node only needs a running [Ollama](https://ollama.com) server (see below).

---

## Nodes included

| Node | What it does |
|------|-------------|
| 📋 Bulk Prompt Loader (CSV / Sheets) | Main node — loads one row per queue run from a CSV file, Google Sheets URL, or pasted text |
| 🦙 Bulk Prompt Ollama Improver | Rewrites each row's prompt with a local Ollama model (optional, via Ollama's REST API) |
| 🌐 Google Sheets Fetcher | Fetches raw CSV text from a published Sheets URL |
| 🔄 Bulk Prompt Reset Counter | Resets the row counter back to 0 |

---

## How to use

### Option A — CSV File

1. Put your `.csv` file inside:
   ```
   ComfyUI-BulkPrompt/csv_files/your_prompts.csv
   ```

2. CSV format with headers (recommended):
   ```csv
   positive,negative,filename_tag
   a red apple on a wooden table,blurry bad quality,apple
   a blue car in the rain,ugly watermark,car
   ```

3. CSV format without headers (also works):
   ```csv
   a red apple on a wooden table
   a blue car in the rain
   ```

4. In ComfyUI:
   - Add **Bulk Prompt Loader** node
   - Set **source** → `CSV File`
   - Select your file from the dropdown
   - Connect `positive` → CLIP Text Encode
   - Connect `negative` → CLIP Text Encode (negative)
   - Leave **auto_loop** → `enabled`
   - Click **Queue Prompt** **once** → the node auto-queues the next run for
     each row and stops at the last one (the on-node progress bar tracks it).
     Set **loop_forever** → `yes` to start over after the last row.

> The auto-loop is driven from the browser, so keep the ComfyUI tab open while
> it runs. The row counter is saved in `state.json`, so it resumes if interrupted.

### Option B — Google Sheets

1. Open your Google Sheet
2. **File → Share → Publish to web**
3. Choose **Comma-separated values (.csv)** → click **Publish**
4. Copy the URL (looks like: `https://docs.google.com/spreadsheets/.../pub?output=csv`)
5. In ComfyUI:
   - Set **source** → `Google Sheets URL`
   - Paste the URL into **sheets_url**
   - Works the same as CSV from here

### Option C — Paste Text

1. Set **source** → `Paste Text`
2. Paste your prompts into the **pasted_data** box. The format is auto-detected:
   - **One prompt per line:**
     ```
     a red apple on a wooden table
     a blue car in the rain
     ```
   - **A comma-separated list on one line** (each item becomes one prompt):
     ```
     a red apple, a blue car, a green tree
     ```
   - **Full CSV with a header row** (mapped to the column outputs):
     ```
     positive,negative,filename_tag
     a red apple,blurry,apple
     a blue car,watermark,car
     ```
3. Queue once — it loops through every pasted row just like the CSV source.
   Editing the pasted text starts a fresh run from row 0.

---

## Improve prompts with Ollama (optional)

The **🦙 Bulk Prompt Ollama Improver** node rewrites each prompt with a local
[Ollama](https://ollama.com) model before it reaches your sampler.

It calls Ollama's HTTP REST API directly (`/api/tags`, `/api/generate`) — no Python
package to install. Just make sure your Ollama server is running (`ollama serve`).

1. Add the **🦙 Bulk Prompt Ollama Improver** node.
2. Wire the loader's **`positive`** output into the node's **`positive`** input.
3. Set **url** and click **🔄 Reconnect** to load the model list, then pick a **model**.
   - Default is `http://127.0.0.1:11434`.
   - **Custom host/port:** change **url** to point anywhere — e.g.
     `http://192.168.1.50:11434`, `http://my-server:11500`, or even `localhost:11434`
     (the scheme is added for you) — then click **🔄 Reconnect**.
4. Edit **instruction** to tell the model what to do (default: rewrite the prompt
   to be more vivid and detailed, output only the improved prompt).
5. Wire the node's **`result`** output into your **CLIP Text Encode**.
6. Tune **keep_alive** / **keep_alive_unit** (how long Ollama keeps the model loaded)
   and **timeout** (max seconds to wait for a response). Toggle **enabled** off to pass
   prompts through unchanged.

Outputs: `result` (improved prompt), `context` (Ollama context for chaining),
`meta` (JSON with model, timings, token counts). The improved text is also shown
on the node.

---

## Outputs

| Output | Description |
|--------|-------------|
| `positive` | The positive prompt text for this row |
| `negative` | The negative prompt text for this row |
| `filename_tag` | Short tag you can append to saved filenames |
| `current_row` | Row index (0-based) being processed |
| `total_rows` | Total number of rows in the CSV |
| `is_last_row` | `True` on the final row (handy for stopping downstream logic) |

---

## Tips

- **Column names** are flexible — you can name them anything. Set `positive_column`, `negative_column`, and `tag_column` to match your headers.
- **Reset**: Use the **Reset Counter** node, or set `reset_counter → yes` on the loader node.
- **Manual mode**: Set `mode → manual-index` and use `manual_index` to pick a specific row for testing.
- The counter **wraps around** — after the last row it goes back to row 0 automatically.
- The row state is saved in `state.json` inside the node folder, so it survives ComfyUI restarts.

---

## Workflow tip: Save with filename_tag

Connect `filename_tag` output to a text node, then concatenate it with your save path in the **Image Saver** node so each image is named after its prompt row.

---

## Credits

The Ollama integration UX — the idea of a model-list endpoint and the on-node model
dropdown / 🔄 Reconnect button — is inspired by
[**comfyui-ollama**](https://github.com/stavsap/comfyui-ollama) by **Stav Sapir**
(`stavsap`), licensed under Apache-2.0. Our node was written independently and calls
Ollama's public REST API with standard-library code (it does not copy comfyui-ollama's
source). A copy of their license is included as
[`LICENSE-APACHE-2.0-comfyui-ollama.txt`](LICENSE-APACHE-2.0-comfyui-ollama.txt).
ComfyUI-BulkPrompt's own code is licensed under the MIT License (see [`LICENSE`](LICENSE)).
