/*
  ComfyUI-BulkPrompt — frontend extension
  Shows: current prompt text, filename tag, row counter (blue), progress bar
*/
import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "BulkPrompt.ProgressBar",

    async nodeCreated(node) {
        if (node.comfyClass !== "BulkPromptLoader") return;

        // ── outer container ────────────────────────────────────────────
        const container = document.createElement("div");
        Object.assign(container.style, {
            width: "100%",
            padding: "6px 4px 4px 4px",
            boxSizing: "border-box",
            fontFamily: "monospace",
            display: "flex",
            flexDirection: "column",
            gap: "5px",
        });

        // ── row counter  (blue) ────────────────────────────────────────
        const rowCounter = document.createElement("div");
        Object.assign(rowCounter.style, {
            fontSize: "12px",
            fontWeight: "bold",
            color: "#4a9eff",
            letterSpacing: "0.03em",
        });
        rowCounter.textContent = "Row: — / —";

        // ── filename tag (blue, slightly smaller) ──────────────────────
        const tagLine = document.createElement("div");
        Object.assign(tagLine.style, {
            fontSize: "11px",
            color: "#60aaff",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
        });
        tagLine.textContent = "Tag: —";

        // ── prompt preview (white/grey, truncated) ─────────────────────
        const promptLine = document.createElement("div");
        Object.assign(promptLine.style, {
            fontSize: "11px",
            color: "#cccccc",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            background: "#1a1a1a",
            borderRadius: "4px",
            padding: "3px 6px",
            borderLeft: "2px solid #4a9eff",
        });
        promptLine.textContent = "Prompt: —";

        // ── progress track ─────────────────────────────────────────────
        const trackWrap = document.createElement("div");
        Object.assign(trackWrap.style, {
            display: "flex",
            alignItems: "center",
            gap: "6px",
        });

        const track = document.createElement("div");
        Object.assign(track.style, {
            flex: "1",
            height: "6px",
            background: "#333",
            borderRadius: "3px",
            overflow: "hidden",
        });

        const bar = document.createElement("div");
        Object.assign(bar.style, {
            height: "100%",
            width: "0%",
            background: "linear-gradient(90deg, #4a9eff, #a78bfa)",
            borderRadius: "3px",
            transition: "width 0.4s ease",
        });

        const pctLabel = document.createElement("div");
        Object.assign(pctLabel.style, {
            fontSize: "11px",
            color: "#4a9eff",
            minWidth: "34px",
            textAlign: "right",
            fontWeight: "bold",
        });
        pctLabel.textContent = "0%";

        track.appendChild(bar);
        trackWrap.appendChild(track);
        trackWrap.appendChild(pctLabel);

        // ── assemble ───────────────────────────────────────────────────
        container.appendChild(rowCounter);
        container.appendChild(tagLine);
        container.appendChild(promptLine);
        container.appendChild(trackWrap);

        node.addDOMWidget("bulk_progress", "bulk_progress_ui", container, {
            serialize: false,
            hideOnZoom: false,
        });

        // ── update on execution output ─────────────────────────────────
        const origOnExecuted = node.onExecuted?.bind(node);
        node.onExecuted = function(output) {
            if (origOnExecuted) origOnExecuted(output);

            const cur      = output?.current_row?.[0];
            const total    = output?.total_rows?.[0];
            const last     = output?.is_last_row?.[0];
            const positive = output?.positive?.[0]     ?? "";
            const tag      = output?.filename_tag?.[0] ?? "";

            if (cur === undefined || total === undefined) return;

            const pct     = Math.round(((cur + 1) / total) * 100);
            const running = !last;

            // progress bar
            bar.style.width      = pct + "%";
            bar.style.background = last
                ? "linear-gradient(90deg, #22c55e, #16a34a)"
                : "linear-gradient(90deg, #4a9eff, #a78bfa)";

            // row counter — always blue, green on done
            rowCounter.style.color  = last ? "#22c55e" : "#4a9eff";
            rowCounter.textContent  = last
                ? `✅  ${total} / ${total} — All done!`
                : `▶  Row  ${cur + 1}  /  ${total}`;

            // filename tag — blue
            tagLine.style.color    = last ? "#22c55e" : "#60aaff";
            tagLine.textContent    = tag
                ? `🏷  ${tag}`
                : "🏷  (no tag)";

            // prompt preview
            promptLine.style.borderLeftColor = last ? "#22c55e" : "#4a9eff";
            promptLine.textContent = positive
                ? `💬  ${positive.length > 90 ? positive.slice(0, 90) + "…" : positive}`
                : "💬  —";

            // pct label — blue
            pctLabel.style.color   = last ? "#22c55e" : "#4a9eff";
            pctLabel.textContent   = pct + "%";
        };
    },
});
