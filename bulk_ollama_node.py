"""
ComfyUI-BulkPrompt — self-contained Ollama prompt improver.

Sends each prompt to a local Ollama model with a user instruction (e.g.
"improve this prompt") and returns the improved text, plus the model context
and response metadata.

This talks to Ollama over its HTTP REST API directly using Python's standard
library (`urllib`) — it does NOT require the `ollama` python package or any
other custom node. The server URL/port is fully configurable on the node
(default http://127.0.0.1:11434).

Credit / attribution
---------------------
The model dropdown + 🔄 Reconnect UX (web/bulk_ollama.js) and the model-list
endpoint approach are inspired by comfyui-ollama by Stav Sapir, licensed under
Apache-2.0:
    https://github.com/stavsap/comfyui-ollama
See LICENSE-APACHE-2.0-comfyui-ollama.txt. This project's own code is MIT.

Requires:  a running Ollama server (https://ollama.com). No pip install needed.
"""

import json
import urllib.request
import urllib.error

DEFAULT_URL = "http://127.0.0.1:11434"


# ── HTTP helpers (Ollama REST API, stdlib only) ─────────────────────────────────

def _normalize_url(url: str) -> str:
    """Tolerant URL normalize so custom host/port input 'just works'.

    Accepts e.g. "localhost:11434", "192.168.1.50:11434", "http://host:port/".
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        url = DEFAULT_URL
    if "://" not in url:
        url = "http://" + url
    return url


def _api_get(url: str, path: str, timeout: int = 10):
    req = urllib.request.Request(
        _normalize_url(url) + path,
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _api_post(url: str, path: str, payload: dict, timeout: int = 120):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _normalize_url(url) + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ── model-list route (proxies to Ollama server-side, avoiding browser CORS) ─────
# Registered under /bulkprompt/... so it never collides with comfyui-ollama's
# own /ollama/get_models route when both packages are installed.
try:
    from server import PromptServer
    from aiohttp import web

    @PromptServer.instance.routes.post("/bulkprompt/ollama/get_models")
    async def bulkprompt_ollama_get_models(request):
        try:
            data = await request.json()
            url = data.get("url") or DEFAULT_URL
            tags = _api_get(url, "/api/tags")
            models = [m.get("model") or m.get("name") for m in tags.get("models", [])]
            return web.json_response([m for m in models if m])
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
except Exception as e:  # pragma: no cover - defensive: never block node import
    print(f"[BulkPrompt] Ollama model-list route not registered: {e}")


# ── node ────────────────────────────────────────────────────────────────────────

class BulkPromptOllama:
    """
    Improve a prompt with a local Ollama model.

    Wire the loader's `positive` output into this node's `positive` input; the
    improved text comes out of `result` (feed it to your CLIP Text Encode).
    """

    CATEGORY     = "BulkPrompt"
    FUNCTION     = "improve"
    OUTPUT_NODE  = True
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("result", "context", "meta")
    DESCRIPTION  = ("Improve each prompt with a local Ollama model over its REST API. "
                    "Use the 🔄 Reconnect button to load the model list.")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive": ("STRING", {"forceInput": True}),
                "instruction": ("STRING", {
                    "multiline": True,
                    "default": ("You are an expert prompt engineer for image "
                                "generation. Rewrite the following prompt to be "
                                "more vivid and detailed. Output ONLY the improved "
                                "prompt, with no preamble or explanation."),
                    "tooltip": "System prompt — tell the model what to do with the prompt.",
                }),
                "url": ("STRING", {
                    "default": DEFAULT_URL,
                    "tooltip": "Ollama server URL. Change this for a custom host/port "
                               "(e.g. http://192.168.1.50:11434), then click 🔄 Reconnect.",
                }),
                "model": ((), {
                    "tooltip": "Pick a model. Click 🔄 Reconnect to (re)load the list "
                               "from the Ollama server.",
                }),
                "keep_alive": ("INT", {
                    "default": 5, "min": -1, "max": 120, "step": 1,
                    "tooltip": "How long Ollama keeps the model loaded after inference "
                               "(-1 = forever, 0 = unload immediately).",
                }),
                "keep_alive_unit": (["minutes", "hours"],),
                "timeout": ("INT", {
                    "default": 120, "min": 5, "max": 3600, "step": 5,
                    "tooltip": "Max seconds to wait for the Ollama response.",
                }),
                "enabled": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "If off, the prompt passes through unchanged (no Ollama call).",
                }),
            },
            "optional": {
                "context": ("STRING", {
                    "forceInput": True,
                    "tooltip": "Optional Ollama context (comma-separated ints) for "
                               "multi-turn continuity.",
                }),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")   # re-run on every loop iteration

    def improve(self, positive, instruction, url, model,
                keep_alive, keep_alive_unit, timeout, enabled,
                context=None, unique_id=None):

        # Bypass: pass the prompt through unchanged.
        if not enabled:
            return {"ui": {"text": [positive]},
                    "result": (positive, "", "{}")}

        if not model:
            raise ValueError("[BulkPrompt] No Ollama model selected. Click "
                             "'🔄 Reconnect' on the node to load the model list.")

        ctx = None
        if context:
            try:
                ctx = [int(x.strip()) for x in str(context).split(",") if x.strip()]
            except Exception as e:
                raise ValueError(f"[BulkPrompt] Invalid context value: {e}")

        unit = "m" if keep_alive_unit == "minutes" else "h"
        request_keep_alive = f"{int(keep_alive)}{unit}"

        # text mode = omit "format" entirely (None fields are dropped from the body)
        payload = {
            "model": model,
            "system": instruction,
            "prompt": positive,
            "stream": False,
            "keep_alive": request_keep_alive,
        }
        if ctx is not None:
            payload["context"] = ctx

        try:
            response = _api_post(url, "/api/generate", payload, timeout=int(timeout))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            raise RuntimeError(f"[BulkPrompt] Ollama returned HTTP {e.code}: {body or e}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"[BulkPrompt] Could not reach Ollama at "
                               f"{_normalize_url(url)}: {e.reason}")

        result = response.get("response", "")
        out_ctx = response.get("context")
        context_str = ",".join(str(x) for x in (out_ctx or []))

        meta = {"model": model, "url": _normalize_url(url), "keep_alive": request_keep_alive}
        for k in ("total_duration", "eval_count", "prompt_eval_count", "created_at"):
            v = response.get(k)
            if v is not None:
                meta[k] = v
        meta_str = json.dumps(meta, default=str)

        preview = result[:80].replace("\n", " ")
        print(f"[BulkPrompt] 🦙 improved via {model}: {preview}...")

        return {"ui": {"text": [result]},
                "result": (result, context_str, meta_str)}


# ── registration ──────────────────────────────────────────────────────────────

NODE_CLASS_MAPPINGS = {
    "BulkPromptOllama": BulkPromptOllama,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BulkPromptOllama": "🦙 Bulk Prompt Ollama Improver",
}
