"""
ComfyUI-BulkPrompt
Loads prompts from CSV / Google Sheets and AUTO-LOOPS until all rows are done.

How auto-loop works:
  - After each generation, the node uses ComfyUI's PromptServer API to
    re-queue itself automatically — no manual queue count needed.
  - It stops automatically when the last row is reached (or wraps if loop=yes).
  - A front-end JS snippet adds a visual progress bar to the node.

Place CSV files in:  ComfyUI/custom_nodes/ComfyUI-BulkPrompt/csv_files/
"""

import os
import csv
import json
import urllib.request
import threading
import time
import folder_paths

NODE_DIR   = os.path.dirname(os.path.abspath(__file__))
CSV_DIR    = os.path.join(NODE_DIR, "csv_files")
STATE_FILE = os.path.join(NODE_DIR, "state.json")
os.makedirs(CSV_DIR, exist_ok=True)


# ── state helpers ─────────────────────────────────────────────────────────────

def _read_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}

def _write_state(state: dict):
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, indent=2)

def _get_row(key: str) -> int:
    return _read_state().get(key, 0)

def _set_row(key: str, row: int):
    state = _read_state()
    state[key] = row
    _write_state(state)

def _reset_row(key: str):
    _set_row(key, 0)

def _list_csv_files():
    files = [f for f in os.listdir(CSV_DIR) if f.lower().endswith(".csv")]
    return sorted(files) if files else ["(no csv files found)"]


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ComfyUI-BulkPrompt/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")

def _parse_csv(text: str) -> list:
    rows = []
    all_rows = [r for r in csv.reader(text.splitlines()) if any(c.strip() for c in r)]
    if not all_rows:
        return rows
    first = [c.strip().lower() for c in all_rows[0]]
    has_header = (
        "positive" in first or "prompt" in first or "negative" in first
        or all(not c.replace("_","").replace("-","").isdigit() for c in first)
    )
    if has_header and len(all_rows) > 1:
        headers = [c.strip() for c in all_rows[0]]
        for r in all_rows[1:]:
            rows.append({headers[i]: (r[i].strip() if i < len(r) else "") for i in range(len(headers))})
    else:
        for r in all_rows:
            rows.append({"positive": r[0].strip(), "negative": "", "filename_tag": ""})
    return rows


# ── auto-queue via PromptServer ───────────────────────────────────────────────

def _trigger_next_queue():
    """
    Asks ComfyUI to queue one more prompt after a short delay.
    Uses the internal PromptServer which is always available.
    """
    def _do():
        time.sleep(0.3)   # let current generation finish writing files
        try:
            from server import PromptServer
            # Post to the /prompt endpoint — same as clicking Queue Prompt once
            PromptServer.instance.send_sync("crystools.monitor", {})  # wake monitor
        except Exception:
            pass
        try:
            import urllib.request as ur
            data = json.dumps({"client_id": ""}).encode()
            req  = ur.Request(
                "http://127.0.0.1:8188/queue",
                data=json.dumps({"delete": []}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
        except Exception:
            pass
        # Most reliable: use the /prompt POST with the last prompt id
        try:
            import urllib.request as ur
            # Get last prompt from history
            with ur.urlopen("http://127.0.0.1:8188/history?max_items=1") as r:
                history = json.loads(r.read())
            if history:
                last_id  = list(history.keys())[0]
                last_prompt = history[last_id]["prompt"]
                # last_prompt = [number, id, prompt_dict, extra, output_ids]
                prompt_payload = {
                    "prompt":  last_prompt[2],
                    "extra_data": last_prompt[3] if len(last_prompt) > 3 else {},
                    "client_id": "",
                }
                data = json.dumps(prompt_payload).encode()
                req  = ur.Request(
                    "http://127.0.0.1:8188/prompt",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with ur.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read())
                    print(f"[BulkPrompt] Auto-queued next run → prompt_id {result.get('prompt_id','?')}")
        except Exception as e:
            print(f"[BulkPrompt] Auto-queue failed: {e}")
    threading.Thread(target=_do, daemon=True).start()


# ── main node ─────────────────────────────────────────────────────────────────

class BulkPromptLoader:
    """
    Loads one prompt per run from CSV / Google Sheets.
    With auto_loop=ON it re-queues itself after every generation
    and stops automatically when all rows are done.
    """

    CATEGORY     = "BulkPrompt"
    FUNCTION     = "load_prompt"
    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT", "INT", "BOOLEAN")
    RETURN_NAMES = ("positive", "negative", "filename_tag",
                    "current_row", "total_rows", "is_last_row")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source":          (["CSV File", "Google Sheets URL"],),
                "csv_file":        (_list_csv_files(),),
                "sheets_url":      ("STRING", {
                    "default": "https://docs.google.com/spreadsheets/d/.../pub?output=csv",
                    "multiline": False,
                }),
                "positive_column": ("STRING", {"default": "positive"}),
                "negative_column": ("STRING", {"default": "negative"}),
                "tag_column":      ("STRING", {"default": "filename_tag"}),
                # ── loop controls ──────────────────────────────────────────
                "auto_loop": (["enabled", "disabled"], {
                    "default": "enabled",
                    "tooltip": "Auto-queue the next prompt until all rows are done"
                }),
                "loop_forever": (["no", "yes"], {
                    "default": "no",
                    "tooltip": "After the last row, start over from row 0"
                }),
                "reset_on_start": (["yes", "no"], {
                    "default": "yes",
                    "tooltip": "Reset to row 0 when you first queue (recommended)"
                }),
                # ── manual override ────────────────────────────────────────
                "manual_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999,
                    "tooltip": "Only used when auto_loop=disabled"
                }),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")   # always re-execute

    def load_prompt(
        self,
        source, csv_file, sheets_url,
        positive_column, negative_column, tag_column,
        auto_loop, loop_forever, reset_on_start, manual_index,
    ):
        # ── load CSV rows ─────────────────────────────────────────────────
        if source == "Google Sheets URL":
            try:
                text = _fetch_url(sheets_url.strip())
                state_key = "url:" + sheets_url.strip()[:80]
            except Exception as e:
                raise RuntimeError(f"[BulkPrompt] Sheets fetch failed: {e}")
        else:
            path = os.path.join(CSV_DIR, csv_file)
            if not os.path.exists(path):
                raise FileNotFoundError(f"[BulkPrompt] File not found: {path}")
            with open(path, "r", encoding="utf-8-sig") as fh:
                text = fh.read()
            state_key = "file:" + csv_file

        rows = _parse_csv(text)
        if not rows:
            raise ValueError("[BulkPrompt] CSV has no data rows.")
        total = len(rows)

        # ── first-run reset ───────────────────────────────────────────────
        # We detect "first run" by checking if the state key is missing
        state = _read_state()
        is_first_run = state_key not in state
        if is_first_run and reset_on_start == "yes":
            _reset_row(state_key)

        # ── determine current row ─────────────────────────────────────────
        if auto_loop == "disabled":
            idx = min(manual_index, total - 1)
        else:
            idx = _get_row(state_key)
            # Clamp in case CSV shrank
            idx = min(idx, total - 1)

        row       = rows[idx]
        is_last   = (idx == total - 1)

        # ── extract columns ───────────────────────────────────────────────
        def get_col(name):
            if name in row:
                return row[name]
            for k, v in row.items():
                if k.lower() == name.lower():
                    return v
            return list(row.values())[0] if len(row) == 1 else ""

        positive     = get_col(positive_column)
        negative     = get_col(negative_column)
        filename_tag = get_col(tag_column)

        print(f"[BulkPrompt] ▶ Row {idx + 1}/{total}  {'(last)' if is_last else ''}")
        print(f"[BulkPrompt]   prompt: {positive[:80]}...")

        # ── advance counter & decide whether to re-queue ──────────────────
        if auto_loop == "enabled":
            if is_last:
                if loop_forever == "yes":
                    _set_row(state_key, 0)
                    print("[BulkPrompt] 🔁 All rows done — looping back to row 0")
                    _trigger_next_queue()
                else:
                    # Reset so next manual queue starts fresh
                    _set_row(state_key, 0)
                    print("[BulkPrompt] ✅ All rows complete — stopping.")
            else:
                _set_row(state_key, idx + 1)
                _trigger_next_queue()

        return (positive, negative, filename_tag, idx, total, is_last)


# ── reset node ────────────────────────────────────────────────────────────────

class BulkPromptReset:
    CATEGORY     = "BulkPrompt"
    FUNCTION     = "reset"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"reset_all": (["yes", "no"],)}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def reset(self, reset_all):
        if reset_all == "yes":
            _write_state({})
            print("[BulkPrompt] 🔄 All counters reset.")
            return ("All row counters reset to 0.",)
        return ("No action.",)


# ── Google Sheets fetcher node ────────────────────────────────────────────────

class GoogleSheetsFetcher:
    CATEGORY     = "BulkPrompt"
    FUNCTION     = "fetch"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("csv_text",)

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "url": ("STRING", {
                "default": "https://docs.google.com/spreadsheets/d/.../pub?output=csv",
                "multiline": False,
            })
        }}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def fetch(self, url):
        try:
            return (_fetch_url(url.strip()),)
        except Exception as e:
            raise RuntimeError(f"[BulkPrompt] Fetch failed: {e}")


# ── registration ──────────────────────────────────────────────────────────────

NODE_CLASS_MAPPINGS = {
    "BulkPromptLoader":    BulkPromptLoader,
    "BulkPromptReset":     BulkPromptReset,
    "GoogleSheetsFetcher": GoogleSheetsFetcher,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BulkPromptLoader":    "📋 Bulk Prompt Loader (CSV / Sheets)",
    "BulkPromptReset":     "🔄 Bulk Prompt Reset Counter",
    "GoogleSheetsFetcher": "🌐 Google Sheets Fetcher",
}
