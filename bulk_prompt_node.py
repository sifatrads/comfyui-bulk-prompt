"""
ComfyUI-BulkPrompt
Loads prompts from a CSV file, a Google Sheets URL, or pasted text, and
AUTO-LOOPS until all rows are done.

How auto-loop works:
  - After each run the node BROADCASTS a "bulkprompt.update" event (row, prompt,
    progress) to every connected browser via PromptServer.send_sync(sid=None).
  - The front-end extension (web/bulk_prompt.js) renders that update on the node
    and, while more rows remain, drives the next run with app.queuePrompt(0).
    Driving from the browser means each run carries the real client_id, so
    ComfyUI's native status (node borders, sampler progress, queue count) shows.
  - It stops automatically at the last row (or wraps if loop_forever=yes).

Place CSV files in:  ComfyUI/custom_nodes/ComfyUI-BulkPrompt/csv_files/
"""

import os
import csv
import json
import hashlib
import urllib.request
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


def _parse_pasted(text: str) -> list:
    """
    Auto-detect parser for the "Paste Text" source:
      - no non-empty lines              -> []
      - single line WITH comma          -> each comma item = one prompt
      - single line WITHOUT comma       -> one prompt
      - multi-line WITH a detected header (first line contains
        positive/prompt/negative/filename) -> delegate to _parse_csv (full CSV)
      - multi-line otherwise            -> one prompt per line

    Note: a single comma'd line is treated as a LIST of prompts, not one CSV
    row. For CSV semantics, paste a header line first or use multiple lines.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []

    if len(lines) == 1:
        line = lines[0]
        if "," in line:
            return [{"positive": item.strip(), "negative": "", "filename_tag": ""}
                    for item in line.split(",") if item.strip()]
        return [{"positive": line, "negative": "", "filename_tag": ""}]

    # multi-line: only treat as CSV when the first line looks like a header
    first_lower = lines[0].lower()
    looks_like_header = any(h in first_lower for h in
                            ("positive", "prompt", "negative", "filename"))
    if looks_like_header:
        return _parse_csv(text)

    # default: one prompt per line (commas inside a prompt are preserved)
    return [{"positive": ln, "negative": "", "filename_tag": ""} for ln in lines]


# ── live update broadcast ──────────────────────────────────────────────────────

def _broadcast_update(payload: dict):
    """
    Push a status update to EVERY connected browser (sid=None broadcasts).
    Broadcasting bypasses client_id routing, so the node display updates on the
    first run and on every front-end-driven loop iteration alike.
    """
    try:
        from server import PromptServer
        PromptServer.instance.send_sync("bulkprompt.update", payload)
    except Exception as e:
        print(f"[BulkPrompt] update broadcast failed: {e}")


def _current_client_id():
    try:
        from server import PromptServer
        return getattr(PromptServer.instance, "client_id", None)
    except Exception:
        return None


# ── auto-loop continuation flag ─────────────────────────────────────────────────
# The front-end marks the NEXT run of a node as an auto-loop CONTINUATION (right
# before it re-queues) so the node advances the loop instead of restarting at
# manual_index. A manual Queue press never sets this, so the backend can tell a
# user-initiated run from a browser-driven one — which is what makes reset_on_start
# behave predictably.
_CONTINUE = set()

try:
    from server import PromptServer
    from aiohttp import web

    @PromptServer.instance.routes.post("/bulkprompt/loader/continue")
    async def _bulkprompt_loader_continue(request):
        try:
            data = await request.json()
            nid = str(data.get("node_id") or "")
            if nid:
                _CONTINUE.add(nid)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
except Exception as e:  # pragma: no cover - never block import outside ComfyUI
    print(f"[BulkPrompt] loader continue-route not registered: {e}")


# ── main node ─────────────────────────────────────────────────────────────────

class BulkPromptLoader:
    """
    Loads one prompt per run from CSV / Google Sheets.
    With auto_loop=ON it broadcasts a progress update after every run; the
    front-end re-queues the next run and it stops when all rows are done.
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
                "source":          (["CSV File", "Google Sheets URL", "Paste Text"],),
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
                    "tooltip": "After the last row, loop back to the start row (manual_index)"
                }),
                "reset_on_start": (["yes", "no"], {
                    "default": "yes",
                    "tooltip": "yes = each time YOU press Queue, (re)start at "
                               "manual_index, then auto-advance. no = resume where the "
                               "loop left off (manual_index is used only on the first run)."
                }),
                # ── manual override ────────────────────────────────────────
                "manual_index": ("INT", {
                    "default": 1, "min": 1, "max": 99999,
                    "tooltip": "Start row, 1-based — matches the 'Row N / total' "
                               "display. With reset_on_start=yes, every Queue press "
                               "(re)starts the batch here; the auto-loop then advances. "
                               "auto_loop=disabled loads exactly this row."
                }),
                # IMPORTANT: append new widgets at the END of this dict. ComfyUI
                # restores saved workflows by widget POSITION, so inserting a widget
                # in the middle shifts every saved value below it — that is what made
                # the column fields load one slot off after pasted_data was added.
                "pasted_data": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "Used when source = Paste Text. One prompt per line, "
                               "a comma-separated list on one line, or full CSV "
                               "(header row + rows).",
                }),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")   # always re-execute

    def load_prompt(
        self,
        source, csv_file, sheets_url, pasted_data,
        positive_column, negative_column, tag_column,
        auto_loop, loop_forever, reset_on_start, manual_index,
        unique_id=None,
    ):
        # ── load prompt rows from the selected source ─────────────────────
        if source == "Google Sheets URL":
            try:
                text = _fetch_url(sheets_url.strip())
                state_key = "url:" + sheets_url.strip()[:80]
            except Exception as e:
                raise RuntimeError(f"[BulkPrompt] Sheets fetch failed: {e}")
            rows = _parse_csv(text)
        elif source == "Paste Text":
            text = pasted_data or ""
            digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
            state_key = "paste:" + digest
            rows = _parse_pasted(text)
        else:  # "CSV File"
            path = os.path.join(CSV_DIR, csv_file)
            if not os.path.exists(path):
                raise FileNotFoundError(f"[BulkPrompt] File not found: {path}")
            with open(path, "r", encoding="utf-8-sig") as fh:
                text = fh.read()
            state_key = "file:" + csv_file
            rows = _parse_csv(text)

        if not rows:
            raise ValueError("[BulkPrompt] No data rows found.")
        total = len(rows)

        # ── determine current row ─────────────────────────────────────────
        # manual_index is the 1-based START row (matches the "Row N" display).
        start   = min(max(int(manual_index) - 1, 0), total - 1)
        node_id = str(unique_id) if unique_id is not None else None

        # Was this run kicked off by the browser auto-loop (a continuation) or by a
        # manual Queue press? The front-end sets a per-node flag right before it
        # re-queues; consume it here. A manual Queue carries no flag.
        is_autodrive = bool(node_id) and node_id in _CONTINUE
        if node_id:
            _CONTINUE.discard(node_id)

        st         = _read_state()
        saved      = st.get(state_key)
        mi_key     = state_key + "::mi"
        mi_changed = st.get(mi_key) != int(manual_index)

        if auto_loop == "disabled":
            idx = start                                  # load exactly manual_index
        elif is_autodrive and saved is not None:
            idx = min(int(saved), total - 1)             # continuation → advance
        else:                                            # a fresh, manual Queue
            if reset_on_start == "yes" or saved is None or mi_changed:
                idx = start                              # (re)start at manual_index
            else:
                idx = min(int(saved), total - 1)         # resume where we left off
            _set_row(state_key, idx)
            _set_row(mi_key, int(manual_index))

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

        # ── advance counter & decide whether the loop continues ───────────
        will_continue = False
        if auto_loop == "enabled":
            if is_last:
                if loop_forever == "yes":
                    _set_row(state_key, start)   # wrap back to the start row
                    will_continue = True
                    print(f"[BulkPrompt] 🔁 All rows done — looping back to row {start + 1}")
                elif reset_on_start == "yes":
                    _set_row(state_key, start)   # next queue restarts at the start row
                    print("[BulkPrompt] ✅ All rows complete — stopping "
                          "(next queue restarts at the start row).")
                else:
                    print("[BulkPrompt] ✅ All rows complete — stopping.")
            else:
                _set_row(state_key, idx + 1)
                will_continue = True

        # ── broadcast the display update to every browser ─────────────────
        # sid=None broadcast reaches the canvas regardless of which client_id
        # queued this run. The front-end drives the next run when running=True.
        _broadcast_update({
            "node_id": str(unique_id) if unique_id is not None else None,
            "current_row": idx,
            "total_rows": total,
            "is_last_row": is_last,
            "positive": positive,
            "filename_tag": filename_tag,
            "running": will_continue,
            "origin_client_id": _current_client_id(),
        })

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
