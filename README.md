# ComfyUI-BulkPrompt

Batch-run your workflow once per prompt from a **CSV file** or **Google Sheets URL**.  
No extra dependencies — uses Python's built-in `csv` and `urllib` only.

---

## Installation

1. Clone (or copy) this repo into your ComfyUI `custom_nodes` folder:
   ```bash
   cd ComfyUI/custom_nodes
   git clone https://github.com/sifatrads/comfyui-bulk-prompt.git
   ```
2. Restart ComfyUI.
3. The nodes appear under the category **BulkPrompt** in the node menu.

No extra dependencies are required.

---

## Nodes included

| Node | What it does |
|------|-------------|
| 📋 Bulk Prompt Loader (CSV / Sheets) | Main node — loads one row per queue run |
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
   - Set **mode** → `auto-increment`
   - Connect `positive` → CLIP Text Encode
   - Connect `negative` → CLIP Text Encode (negative)
   - Set **Queue** count to the number of rows in your CSV
   - Click **Queue Prompt** → each run uses the next row!

### Option B — Google Sheets

1. Open your Google Sheet
2. **File → Share → Publish to web**
3. Choose **Comma-separated values (.csv)** → click **Publish**
4. Copy the URL (looks like: `https://docs.google.com/spreadsheets/.../pub?output=csv`)
5. In ComfyUI:
   - Set **source** → `Google Sheets URL`
   - Paste the URL into **sheets_url**
   - Works the same as CSV from here

---

## Outputs

| Output | Description |
|--------|-------------|
| `positive` | The positive prompt text for this row |
| `negative` | The negative prompt text for this row |
| `filename_tag` | Short tag you can append to saved filenames |
| `current_row` | Row index (0-based) being processed |
| `total_rows` | Total number of rows in the CSV |

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
