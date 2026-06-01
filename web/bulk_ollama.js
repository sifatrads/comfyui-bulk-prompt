/*
  ComfyUI-BulkPrompt — Ollama improver frontend.
  - Dynamic model dropdown + 🔄 Reconnect button (fetches the model list from
    the Ollama server via the /bulkprompt/ollama/get_models route).
  - Read-only result display ("Show Text") updated on execution.
*/
import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "BulkPrompt.OllamaImprover",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "BulkPromptOllama") return;

        const origCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = async function () {
            if (origCreated) origCreated.apply(this, arguments);

            const urlWidget   = this.widgets.find((w) => w.name === "url");
            const modelWidget = this.widgets.find((w) => w.name === "model");
            const refreshBtn  = this.addWidget("button", "🔄 Reconnect", null, () => {});

            const fetchModels = async (url) => {
                const r = await fetch("/bulkprompt/ollama/get_models", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url }),
                });
                if (!r.ok) throw new Error("HTTP " + r.status);
                const data = await r.json();
                if (data && data.error) throw new Error(data.error);
                return data;
            };

            const updateModels = async () => {
                if (!urlWidget || !modelWidget) return;
                refreshBtn.name = "⏳ Fetching...";
                this.setDirtyCanvas(true);
                let models = [];
                try {
                    models = await fetchModels(urlWidget.value);
                } catch (err) {
                    console.error("[BulkPrompt] Ollama error:", err);
                    app.extensionManager?.toast?.add?.({
                        severity: "error",
                        summary: "Ollama connection error",
                        detail: "Make sure the Ollama server is running on " + urlWidget.value,
                        life: 5000,
                    });
                    refreshBtn.name = "🔄 Reconnect";
                    this.setDirtyCanvas(true);
                    return;
                }
                const prev = modelWidget.value;
                modelWidget.options.values = models;
                if (models.includes(prev)) {
                    modelWidget.value = prev;
                } else if (models.length) {
                    modelWidget.value = models[0];
                }
                refreshBtn.name = "🔄 Reconnect";
                this.setDirtyCanvas(true);
            };

            urlWidget.callback  = updateModels;
            refreshBtn.callback = updateModels;

            // Read-only result display — a dedicated DOM widget, so it never
            // touches the url/model/reconnect widgets.
            const box = document.createElement("div");
            Object.assign(box.style, {
                width: "100%",
                boxSizing: "border-box",
                fontFamily: "monospace",
                fontSize: "11px",
                color: "#cccccc",
                background: "#1a1a1a",
                borderRadius: "4px",
                padding: "4px 6px",
                borderLeft: "2px solid #a78bfa",
                whiteSpace: "pre-wrap",
                maxHeight: "140px",
                overflow: "auto",
            });
            box.textContent = "🦙 (no output yet)";
            // addDOMWidget is a ComfyUI litegraph extension — absent (or able to
            // throw) on very old / non-DOM frontends. Guard it so a missing or
            // failing DOM widget can never break node creation; onExecuted
            // already null-checks __ollamaResult, so the node still works.
            try {
                if (typeof this.addDOMWidget === "function") {
                    this.addDOMWidget("ollama_result", "ollama_result_ui", box, {
                        serialize: false,
                        hideOnZoom: false,
                    });
                    this.__ollamaResult = box;
                }
            } catch (e) {
                console.warn("[BulkPrompt] result display unavailable on this frontend:", e);
            }

            // Initial population (waits for the widget to read its real value).
            await updateModels();
        };

        const origExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            if (origExecuted) origExecuted.apply(this, arguments);
            const txt = message?.text;
            if (this.__ollamaResult && txt) {
                this.__ollamaResult.textContent =
                    "🦙 " + (Array.isArray(txt) ? txt.join("\n") : txt);
            }
        };
    },
});
