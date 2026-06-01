"""
ComfyUI-BulkPrompt — Ollama prompt improver.

Sends each prompt to a local Ollama model with a user instruction (e.g.
"improve this prompt") and returns the improved text, plus the model context
and response metadata.

This uses the official `ollama` Python library
(https://github.com/ollama/ollama-python). Install it with `pip install ollama`
(or via this pack's requirements.txt). The server URL/port is fully configurable
on the node (default http://127.0.0.1:11434).

Requires:  a running Ollama server (https://ollama.com) and the `ollama` package.
"""

import json
import re

import ollama

DEFAULT_URL = "http://127.0.0.1:11434"

# Cross-version-safe handle for the library's error type — older builds may not
# export it at module top level; fall back to the broad Exception in that case.
_ResponseError = getattr(ollama, "ResponseError", Exception)


def _is_timeout(exc) -> bool:
    """True if exc is (or reads as) an HTTP connect/read timeout."""
    try:
        import httpx
        if isinstance(exc, httpx.TimeoutException):
            return True
    except Exception:
        pass
    text = f"{type(exc).__name__} {exc}".lower()
    return "timeout" in text or "timed out" in text


# ── helpers ─────────────────────────────────────────────────────────────────────

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


def _model_name(m) -> str:
    """Read a model name from an ollama .list() entry across library versions."""
    name = getattr(m, "model", None) or getattr(m, "name", None)
    if not name and isinstance(m, dict):
        name = m.get("model") or m.get("name")
    return name or ""


# ── prompt extraction: strip the conversational wrapper an LLM may add ───────────

def _extract_prompt(text: str) -> str:
    """Extract the actual image prompt from an LLM reply that may be wrapped in
    conversational scaffolding (preambles, postambles, markdown dividers, code
    fences, header labels, or surrounding quotes).

    Strategy (ordered, line-based so multi-paragraph prompts survive):
      1. If a code fence (``` / ~~~, optional language tag) is present, take the
         content strictly between the first and last fence line -- the fence is
         the strongest structural signal.
      2. Otherwise, peel known wrapper *lines* off both ends:
           - markdown horizontal-rule dividers (---, ***, ___ as whole lines)
           - bare header-label lines ("Prompt", "**Prompt:**", "Improved Prompt:")
           - conversational preamble lines (top) / postamble + Note: blocks (bottom)
         A "header anchor" rescues plain-prose preambles that sit above a
         ---/Prompt/--- scaffold (the real prompt begins after the LAST bare
         header line).
      3. Peel an inline label prefix on the first content line ("Improved
         prompt: X" -> "X") and a single surrounding quote pair.

    Invariants:
      - Never raises (everything risky is wrapped in try/except).
      - Returns text.strip() as the fallback; never empty for non-empty input.
      - A clean prompt with no wrapper passes through unchanged (aside .strip()).
    """
    # ---- top-level safety: compute the fallback first -------------------
    try:
        fallback = text.strip()
    except Exception:
        # text is None or not str-like; salvage what we can without raising.
        try:
            return str(text)
        except Exception:
            return ""

    if not fallback:
        return ""  # empty / whitespace-only input -> empty output

    try:
        result = _extract_core(text)
    except Exception:
        return fallback

    if not isinstance(result, str):
        return fallback
    result = result.strip()
    # Never return empty for non-empty input.
    return result if result else fallback


# ----------------------------------------------------------------------------
# Compiled patterns (built once at import).
# ----------------------------------------------------------------------------

# Adjectives that can precede the word "prompt" in a label.
_LABEL_ADJ = (r'improved|enhanced|refined|optimized|revised|final|updated|new|'
              r'polished|rewritten|reworked|edited|better|tighter')

# A horizontal-rule line: ONLY ---, ***, or ___ (3+ of the SAME char, optional
# interior/edge spaces). Crucially this does NOT match em-dashes (U+2014) or a
# spaced hyphen " - " used as punctuation inside a real prompt.
_RULE_RE = re.compile(r'^\s*([-*_])(?:\s*\1){2,}\s*$')

# Code-fence line: ``` or ~~~ (3+), optionally followed by a language tag.
_FENCE_RE = re.compile(r'^\s*(?:`{3,}|~{3,})\s*[A-Za-z0-9_+\-]*\s*$')

# A whole-line header label: the ENTIRE line is just a "prompt" label, e.g.
# "Prompt", "Prompt:", "**Prompt:**", "## Prompt", "**Improved Prompt:**".
# Because the whole line must match, real prompt text such as
# "Write a prompt for ..." never matches.
_HEADER_LINE_RE = re.compile(
    r'^\s*#*\s*\*{0,2}\s*'
    r'(?:(?:' + _LABEL_ADJ + r')\s+)?'
    r'prompt'
    r'\s*\*{0,2}\s*:?\s*\*{0,2}\s*$',
    re.IGNORECASE,
)

# An inline label prefix on the SAME line as the prompt, e.g.
# "Improved prompt: <body>", "Revised: <body>", "Prompt: <body>".
# The trailing "(?=\S)" requires real content after the colon, so we never
# strip a label off a line that is only the label (that is the header case),
# and the required ":" right after the label word means a genuine prompt that
# merely starts with "Write a prompt for ..." is left untouched.
_INLINE_LABEL_RE = re.compile(
    r'^\s*\*{0,2}\s*'
    r'(?:here(?:\'s| is)\s+(?:the|your|a|an)\s+)?'
    r'(?:'
    r'(?:(?:' + _LABEL_ADJ + r')\s+)?prompt'   # "improved prompt", "prompt"
    r'|(?:' + _LABEL_ADJ + r')'                # bare adjective: "Revised"
    r')'
    r'\s*\*{0,2}\s*:\s+(?=\S)',
    re.IGNORECASE,
)

# A trailing "Note:" rationale line (starts a block we drop from the bottom).
_NOTE_RE = re.compile(r'^\s*\*{0,2}\s*note\s*\*{0,2}\s*:', re.IGNORECASE)

# Strong conversational openers that mark a *leading* line as preamble even
# when it does not end in a colon.
_PREAMBLE_START_RE = re.compile(
    r'^\s*(?:'
    r'sure(?:\s+thing)?|certainly|absolutely|of\s+course|great|gladly|'
    r'happy\s+to|glad\s+to|no\s+problem|okay|ok|got\s+it|definitely|'
    r'here(?:\'s| is| you go)|i\'?m\s+here\s+to\s+help|i\'?ve|i\s+have|'
    r'i\s+reworked|i\s+polished|i\s+leaned|i\s+emphasized|let\'?s|let\s+us'
    r')\b',
    re.IGNORECASE,
)

# Trailing conversational offers / sign-offs (postamble).
_POSTAMBLE_RE = re.compile(
    r'^\s*(?:'
    r'let\s+me\s+know\b|feel\s+free\b|i\s+hope\s+(?:this|that|it)\b|'
    r'hope\s+(?:this|that|it)\b|happy\s+to\b|tell\s+me\s+if\b|'
    r'do\s+you\s+want\b|would\s+you\s+like\b|if\s+you\'?d\s+like\b|'
    r'if\s+you\s+want\b|what\s+do\s+you\s+think\b'
    r')',
    re.IGNORECASE,
)

# Quote pairs we will peel when they wrap the WHOLE remaining content.
# Straight double, curly double, curly single. (Straight single is deliberately
# omitted: apostrophes like "child's" are common legitimate inner punctuation.)
_QUOTE_PAIRS = (('"', '"'), ('“', '”'), ('‘', '’'))


# ----------------------------------------------------------------------------
# Line classifiers.
# ----------------------------------------------------------------------------

def _demark(s):
    """Strip surrounding markdown emphasis/heading/quote markers for
    CLASSIFICATION only, so a bold lead-in like "**Here's your prompt:**" is
    recognized as wrapper. Never used to alter kept content."""
    return s.strip().strip("*_#> 	`").strip()


def _is_rule(line):
    return bool(_RULE_RE.match(line))


def _is_fence(line):
    return bool(_FENCE_RE.match(line))


def _is_header_line(line):
    return bool(_HEADER_LINE_RE.match(line))


def _looks_like_prompt_body(line):
    # Image prompts are comma-laden descriptor lists; prose lead-ins rarely are.
    # Used as a guard so a comma-heavy descriptor line is never mistaken for a
    # colon-terminated preamble (e.g. "a, b, c, lens:" style edge cases).
    return line.count(',') >= 2


def _is_preamble_line(line):
    """A leading conversational lead-in line that should be dropped."""
    s = _demark(line.strip())
    if not s:
        return False
    if _looks_like_prompt_body(s):
        return False
    # Strong chatty opener (with or without trailing colon).
    if _PREAMBLE_START_RE.match(s):
        return True
    # A short colon-terminated lead-in ("Here is the enhanced prompt:",
    # "... more vivid take on your idea:"). Keep the length guard so a long
    # descriptive sentence that merely happens to end in a colon is preserved.
    if s.endswith(':') and len(s) <= 120:
        return True
    return False


def _is_postamble_line(line):
    s = _demark(line.strip())
    if not s:
        return False
    if _looks_like_prompt_body(s):
        return False
    return bool(_POSTAMBLE_RE.match(s))


def _is_note_line(line):
    return bool(_NOTE_RE.match(line))


# ----------------------------------------------------------------------------
# Inner-edge peelers.
# ----------------------------------------------------------------------------

def _strip_inline_label(body):
    """Remove an inline label prefix from the first line only, keep the rest."""
    if not body:
        return body
    nl = body.find('\n')
    first = body if nl == -1 else body[:nl]
    rest = '' if nl == -1 else body[nl:]
    m = _INLINE_LABEL_RE.match(first)
    if m:
        candidate = first[m.end():]
        if candidate.strip():
            return (candidate + rest)
    return body


def _strip_wrapping_quotes(body):
    """Peel a single surrounding quote pair that brackets the whole content."""
    s = body.strip()
    if len(s) < 2:
        return body
    for opn, cls in _QUOTE_PAIRS:
        if s.startswith(opn) and s.endswith(cls):
            inner = s[len(opn):len(s) - len(cls)]
            if opn == cls:
                # Straight double quote: only peel if the inner text has an
                # even number of that quote, so a stray internal quote does not
                # cause a mis-strip.
                if inner.count(opn) % 2 != 0:
                    return body
            if inner.strip():
                return inner
    return body


def _post_clean(body):
    if not body:
        return body
    body = _strip_inline_label(body)
    body = _strip_wrapping_quotes(body)
    return body.strip()


# ----------------------------------------------------------------------------
# Code-fence extraction.
# ----------------------------------------------------------------------------

def _extract_fence(text):
    """If text has >=2 fence lines, return the content between the first and
    last fence; else None."""
    lines = text.split('\n')
    idx = [i for i, ln in enumerate(lines) if _is_fence(ln)]
    if len(idx) < 2:
        return None
    first, last = idx[0], idx[-1]
    if last <= first:
        return None
    return '\n'.join(lines[first + 1:last])


# ----------------------------------------------------------------------------
# Wrapper-line stripping (the workhorse).
# ----------------------------------------------------------------------------

def _strip_wrapper_lines(text):
    """Peel scaffold lines off both ends. Returns the inner body, or None if
    nothing survives. Interior blank lines are preserved (multi-paragraph
    prompts survive)."""
    lines = text.split('\n')
    n = len(lines)

    # --- header anchor: the real prompt begins after the LAST bare header
    #     line. This rescues plain-prose preambles (no trailing colon) that sit
    #     above a ---/Prompt/--- scaffold, e.g. the real-world case.
    anchor = -1
    for i in range(n):
        if _is_header_line(lines[i]):
            anchor = i

    start, end = 0, n  # [start, end)

    # --- trim leading scaffold (blanks, rules, headers, preamble) ---
    changed = True
    while changed and start < end:
        changed = False
        while start < end and lines[start].strip() == '':
            start += 1
            changed = True
        if start >= end:
            break
        ln = lines[start]
        if _is_rule(ln) or _is_header_line(ln) or _is_preamble_line(ln):
            start += 1
            changed = True

    # Apply the header anchor even when the preamble above it was plain prose
    # that the line-by-line peel above could not classify.
    if anchor >= 0 and anchor + 1 > start:
        start = anchor + 1

    # --- trim trailing scaffold (blanks, rules, postamble, Note: blocks) ---
    changed = True
    while changed and end > start:
        changed = False
        while end > start and lines[end - 1].strip() == '':
            end -= 1
            changed = True
        if end <= start:
            break
        ln = lines[end - 1]
        if _is_rule(ln) or _is_postamble_line(ln) or _is_note_line(ln):
            end -= 1
            changed = True

    if start >= end:
        return None

    body = '\n'.join(lines[start:end]).strip()
    return body if body else None


# ----------------------------------------------------------------------------
# Orchestration.
# ----------------------------------------------------------------------------

def _extract_core(text):
    # 1) Code fence is the strongest structural signal.
    fenced = _extract_fence(text)
    if fenced is not None:
        inner = _strip_wrapper_lines(fenced)
        if inner is None or not inner.strip():
            inner = fenced.strip()
        cleaned = _post_clean(inner)
        if cleaned:
            return cleaned
        # Fall through if the fence somehow emptied out.

    # 2) Wrapper-line stripping on the whole text.
    body = _strip_wrapper_lines(text)
    if body is None or not body.strip():
        return None  # caller falls back to text.strip()

    # 3) Inner-edge peels: inline label, then a surrounding quote pair.
    cleaned = _post_clean(body)
    return cleaned if cleaned else None


# ── model-list route (queries Ollama server-side, avoiding browser CORS) ────────
# Namespaced under /bulkprompt/ so it never collides with another pack's route.
try:
    from server import PromptServer
    from aiohttp import web

    @PromptServer.instance.routes.post("/bulkprompt/ollama/get_models")
    async def bulkprompt_ollama_get_models(request):
        try:
            data = await request.json()
            url = _normalize_url(data.get("url") or DEFAULT_URL)
            resp = await ollama.AsyncClient(host=url).list()
            models = getattr(resp, "models", None)          # pydantic era (0.4+)
            if models is None and isinstance(resp, dict):
                models = resp.get("models")                 # pre-0.4 dict era
            names = [n for n in (_model_name(m) for m in (models or [])) if n]
            return web.json_response(names)
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
    DESCRIPTION  = ("Improve each prompt with a local Ollama model (official ollama "
                    "library), optionally trimming any chat wrapper the model adds. "
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
                "keep_alive_minutes": ("INT", {
                    "default": 5, "min": -1, "max": 1440, "step": 1,
                    "tooltip": "Minutes Ollama keeps the model loaded after inference "
                               "(-1 = keep forever, 0 = unload immediately).",
                }),
                "timeout": ("INT", {
                    "default": 120, "min": 5, "max": 3600, "step": 5,
                    "tooltip": "Max seconds to wait for the response. Large models "
                               "(e.g. 14B) need more — the first run also loads the "
                               "model into VRAM. Use 300+ if you hit timeouts.",
                }),
                "enabled": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "If off, the prompt passes through unchanged (no Ollama call).",
                }),
                "trim_output": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Keep only the prompt: strip any chat wrapper the model "
                               "adds (preambles, '---'/code-fence/'Prompt:' headers, "
                               "trailing offers). Turn off to use the raw reply.",
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
    def VALIDATE_INPUTS(cls, model=None, **kwargs):
        # The model list is fetched client-side from the live Ollama server, so
        # the valid values aren't known at INPUT_TYPES time and the `model`
        # combo is declared empty. Naming `model` here (plus **kwargs) tells
        # ComfyUI's backend to SKIP its combo-membership check for this input —
        # without it, validate_inputs rejects every model as "not in list" and
        # the node can never execute. Accept any non-empty selection.
        if not model:
            return "No Ollama model selected. Click 🔄 Reconnect to load the list."
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")   # re-run on every loop iteration

    def improve(self, positive, instruction, url, model,
                keep_alive_minutes, timeout, enabled, trim_output=True,
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

        request_keep_alive = f"{int(keep_alive_minutes)}m"

        url_n = _normalize_url(url)
        # text mode = no "format" (omitting it lets the model reply in plain text)
        kwargs = {
            "model": model,
            "system": instruction,
            "prompt": positive,
            "stream": False,
            "keep_alive": request_keep_alive,
        }
        if ctx is not None:
            kwargs["context"] = ctx

        # `timeout` is the generation budget (the read timeout). Use a short,
        # separate connect timeout so a truly-unreachable server fails fast
        # instead of waiting the whole budget. NOTE: the first call to a big model
        # also has to load it into VRAM, which counts against this budget.
        gen_timeout = float(timeout)
        try:
            import httpx
            client_timeout = httpx.Timeout(gen_timeout, connect=min(10.0, gen_timeout))
        except Exception:
            client_timeout = gen_timeout

        try:
            response = ollama.Client(host=url_n, timeout=client_timeout).generate(**kwargs)
        except _ResponseError as e:
            code = getattr(e, "status_code", None)
            code = code if isinstance(code, int) and code > 0 else "?"
            raise RuntimeError(f"[BulkPrompt] Ollama returned an error (HTTP {code}): {e}")
        except Exception as e:
            if _is_timeout(e):
                raise RuntimeError(
                    f"[BulkPrompt] Ollama timed out after {int(gen_timeout)}s on model "
                    f"'{model}'. Large models take longer to load and generate — raise "
                    f"this node's 'timeout' (e.g. 300) and retry. Server: {url_n}")
            raise RuntimeError(
                f"[BulkPrompt] Could not reach Ollama at {url_n}: {e}. "
                f"Check the server is running and reachable from ComfyUI.")

        def _field(obj, key, default=None):
            v = getattr(obj, key, None)
            if v is None and isinstance(obj, dict):
                v = obj.get(key)
            return default if v is None else v

        result = _field(response, "response", "") or ""
        # Trim any conversational wrapper the model added around the prompt.
        if trim_output:
            result = _extract_prompt(result)
        out_ctx = _field(response, "context")
        context_str = ",".join(str(x) for x in (out_ctx or []))

        meta = {"model": model, "url": url_n, "keep_alive": request_keep_alive,
                "trimmed": bool(trim_output)}
        for k in ("total_duration", "eval_count", "prompt_eval_count", "created_at"):
            v = _field(response, k)
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
