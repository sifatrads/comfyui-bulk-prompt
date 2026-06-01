from .bulk_prompt_node import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# Optional Ollama improver node — merged in only if it imports cleanly so a
# missing 'ollama' package (or any other issue) never disables the core nodes.
try:
    from . import bulk_ollama_node as _ollama
    NODE_CLASS_MAPPINGS.update(_ollama.NODE_CLASS_MAPPINGS)
    NODE_DISPLAY_NAME_MAPPINGS.update(_ollama.NODE_DISPLAY_NAME_MAPPINGS)
except Exception as e:
    print(f"[BulkPrompt] Ollama node disabled: {e}")

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
