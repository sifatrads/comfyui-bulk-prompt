/*
  ComfyUI-BulkPrompt — frontend extension
  - Renders current prompt text, filename tag, row counter (blue) and a progress
    bar on the BulkPromptLoader node.
  - Listens for the "bulkprompt.update" event broadcast by the Python node and
    updates the matching node by id.
  - Drives the auto-loop from the browser: while rows remain it calls
    app.queuePrompt(0), so each run carries this tab's real client_id and ComfyUI's
    native status (node borders, sampler progress, queue count) keeps updating.
*/
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// One queue per loop cycle — guards against double-queueing when several
// "bulkprompt.update" events arrive close together (e.g. multiple loader nodes).
let bulkDriveLock = false;

function applyUpdate(d) {
    if (d.node_id == null) return;

    // The server sends unique_id as a string; getNodeById may expect a number.
    const node =
        app.graph.getNodeById(d.node_id) ||
        app.graph.getNodeById(Number(d.node_id)) ||
        app.graph._nodes_by_id?.[d.node_id] ||
        app.graph._nodes_by_id?.[String(d.node_id)];
    if (!node || !node.__bulk) return;

    const { rowCounter, tagLine, promptLine, bar, pctLabel } = node.__bulk;

    const cur   = d.current_row;
    const total = d.total_rows;
    const last  = d.is_last_row;
    if (cur === undefined || total === undefined) return;

    const pct = total ? Math.round(((cur + 1) / total) * 100) : 0;

    // progress bar
    bar.style.width      = pct + "%";
    bar.style.background = last
        ? "linear-gradient(90deg, #22c55e, #16a34a)"
        : "linear-gradient(90deg, #4a9eff, #a78bfa)";

    // row counter — blue while running, green when done
    rowCounter.style.color = last ? "#22c55e" : "#4a9eff";
    rowCounter.textContent = last
        ? `✅  ${total} / ${total} — All done!`
        : `▶  Row  ${cur + 1}  /  ${total}`;

    // filename tag
    tagLine.style.color   = last ? "#22c55e" : "#60aaff";
    tagLine.textContent   = d.filename_tag
        ? `🏷  ${d.filename_tag}`
        : "🏷  (no tag)";

    // prompt preview
    const positive = d.positive ?? "";
    promptLine.style.borderLeftColor = last ? "#22c55e" : "#4a9eff";
    promptLine.textContent = positive
        ? `💬  ${positive.length > 90 ? positive.slice(0, 90) + "…" : positive}`
        : "💬  —";

    // pct label
    pctLabel.style.color = last ? "#22c55e" : "#4a9eff";
    pctLabel.textContent = pct + "%";

    // ── front-end loop driver ──────────────────────────────────────────
    // Only the tab that originated the run drives the loop; other tabs just
    // mirror the display. Re-queue the whole graph once per cycle.
    const mine = !d.origin_client_id || api.clientId === d.origin_client_id;
    if (d.running && mine && !bulkDriveLock) {
        bulkDriveLock = true;
        setTimeout(async () => {
            try {
                // Mark the NEXT run of this node as an auto-loop continuation so the
                // backend ADVANCES the loop instead of restarting at manual_index.
                // A manual Queue press never hits this path, so it stays a fresh run.
                const r = await fetch("/bulkprompt/loader/continue", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ node_id: d.node_id }),
                });
                if (!r.ok) throw new Error("HTTP " + r.status);
                await app.queuePrompt(0);
            } catch (e) {
                // If the flag can't be set, STOP rather than risk restarting the
                // batch mid-loop. Press Queue to resume.
                console.error("[BulkPrompt] auto-loop stopped (continue flag failed):", e);
            } finally {
                bulkDriveLock = false;
            }
        }, 50);
    }
}

app.registerExtension({
    name: "BulkPrompt.ProgressBar",

    setup() {
        api.addEventListener("bulkprompt.update", (e) => applyUpdate(e.detail || {}));
    },

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

        // Stash element refs so the global "bulkprompt.update" listener can
        // find and update this node's widgets by id.
        node.__bulk = { rowCounter, tagLine, promptLine, bar, pctLabel };
    },
});
