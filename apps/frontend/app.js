// Incident Copilot GUI - dependency-free. All server text is HTML-escaped before the
// minimal markdown transform, so document or ticket content can never inject markup.
"use strict";

const CFG = window.COPILOT_CONFIG || {};
const API = (CFG.apiBaseUrl || "").replace(/\/$/, "");
const SAMPLES = [
  "Prepare an incident for the highest-priority active alarm in EastRefinery.",
  "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely contributing factors, retrieve the relevant operating procedure, and provide recommended actions with source evidence.",
  "Find similar historical tickets for this compressor alarm.",
  "Show open tickets linked to correlated assets.",
  "What is the escalation procedure for a P1 incident?",
];

const state = { conversationId: null, last: null, draft: null, busy: false };
const $ = (sel) => document.querySelector(sel);

// ------------------------------------------------------------------ utilities
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v; // only ever called with already-escaped HTML
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) n.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}
function pill(text, cls = "") { return el("span", { class: `pill ${cls}` }, text); }
function fmtMs(ms) { return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`; }

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    ...opts,
    headers: { "Content-Type": "application/json", "x-user": CFG.user || "operator", ...(opts.headers || {}) },
  });
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const msg = body?.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : body?.error?.message || `HTTP ${res.status}`;
    const err = new Error(msg); err.status = res.status; err.body = body; throw err;
  }
  return body;
}

// Minimal, safe markdown: headings, bold, italics, code, lists, blockquotes, citations.
function renderMarkdown(src) {
  const lines = esc(src).split("\n");
  const out = []; let list = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const inline = (t) => t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
    .replace(/\[(D\d+|T\d+|INC-\d{4,8})\]/g, (_, ref) => {
      const k = ref[0] === "D" ? "d" : ref[0] === "T" ? "t" : "i";
      return `<button type="button" class="cite ${k}" data-ref="${ref}">${ref}</button>`;
    });
  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^#{1,4}\s+(.*)$/))) { closeList(); out.push(`<h3>${inline(m[1])}</h3>`); }
    else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) { if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); }
    else if ((m = line.match(/^&gt;\s?(.*)$/))) { closeList(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); }
    else if (!line.trim()) { closeList(); }
    else { closeList(); out.push(`<p>${inline(line)}</p>`); }
  }
  closeList();
  return out.join("");
}

// ------------------------------------------------------------------ tabs
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "audit") loadAudit();
  if (name === "tools") loadTools();
}
function flash(selector, tab) {
  showTab(tab);
  const node = document.querySelector(selector);
  if (!node) return;
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  node.classList.add("flash");
  setTimeout(() => node.classList.remove("flash"), 1600);
}
document.addEventListener("click", (e) => {
  const c = e.target.closest(".cite");
  if (!c) return;
  const ref = c.dataset.ref;
  if (ref.startsWith("D")) flash(`[data-cite="${ref}"]`, "citations");
  else if (ref.startsWith("T")) flash(`[data-step-ref="[${ref}]"]`, "trace");
  else flash(`[data-ticket="${ref}"]`, "similar");
});

// ------------------------------------------------------------------ chat
function addMessage(role, content, opts = {}) {
  $("#chat-empty")?.remove();
  const box = el("div", { class: `msg ${role}${opts.error ? " error" : ""}` });
  if (opts.meta) box.append(opts.meta);
  if (role === "user") box.append(el("div", {}, content));
  else box.append(el("div", { class: "md", html: renderMarkdown(content) }));
  if (opts.warnings?.length) {
    box.append(el("div", { class: "warnings" }, el("strong", {}, "Warnings"), el("ul", {}, opts.warnings.map((w) => el("li", {}, w)))));
  }
  $("#messages").append(box);
  box.scrollIntoView({ behavior: "smooth", block: "end" });
  return box;
}

async function send(text) {
  if (state.busy || !text.trim()) return;
  state.busy = true; $("#send").disabled = true; $("#followups").replaceChildren();
  addMessage("user", text);
  const pending = el("div", { class: "msg bot loading" }, el("span", { class: "spinner" }), "Planning, calling MCP tools and retrieving documents…");
  $("#messages").append(pending);
  try {
    const r = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: text, conversation_id: state.conversationId }) });
    pending.remove();
    state.conversationId = r.conversation_id; state.last = r;
    const statusCls = { ok: "ok", partial: "warn", needs_clarification: "warn", error: "err" }[r.status] || "";
    const meta = el("div", { class: "meta" },
      pill(r.intent.replace(/_/g, " "), "mcp"), pill(r.status.replace(/_/g, " "), statusCls),
      pill(`${r.trace.steps.filter((s) => s.kind === "mcp").length} MCP calls`), pill(`${r.citations.length} docs`, "rag"),
      pill(fmtMs(r.trace.total_ms)), r.trace.llm ? pill(`LLM ${r.trace.llm.accepted ? "used" : "rejected"}`, r.trace.llm.accepted ? "ok" : "warn") : null);
    addMessage("bot", r.answer_markdown, { meta, warnings: r.warnings, error: r.status === "error" });
    renderCitations(r.citations);
    renderTrace(r.trace);
    renderTickets(r.similar_tickets, r.linked_tickets);
    if (r.ticket_draft) { renderDraft(r.ticket_draft); }
    if (r.requires_confirmation && state.draft) { showTab("draft"); openConfirm(); }
    else if (r.ticket_draft) showTab("draft");
    $("#followups").replaceChildren(...(r.suggestions || []).map((s) => el("button", { class: "chip", type: "button", onclick: () => send(s) }, s)));
  } catch (err) {
    pending.remove();
    addMessage("bot", `**Request failed:** ${err.message}`, { error: true });
  } finally {
    state.busy = false; $("#send").disabled = false; $("#input").focus();
  }
}

// ------------------------------------------------------------------ panels
function renderCitations(cites) {
  $("#cnt-citations").textContent = cites.length ? `(${cites.length})` : "";
  const panel = $("#tab-citations");
  if (!cites.length) { panel.replaceChildren(el("div", { class: "empty small" }, "No documents were used for this answer.")); return; }
  panel.replaceChildren(...cites.map((c) => el("div", { class: "card", "data-cite": c.ref.slice(1, -1) },
    el("h4", {}, el("span", { class: "cite d" }, c.ref), c.title),
    el("div", { class: "small" }, `${c.doc_id} v${c.version ?? "-"} · ${c.doc_type.replace(/_/g, " ")} · section “${c.section}” · score ${c.score}`),
    el("div", { class: "small mono" }, `${c.source_path} · ${c.chunk_id}`),
    el("div", { class: "snippet" }, c.snippet))));
}

function renderTickets(similar, linked) {
  const n = (similar?.length || 0) + (linked?.length || 0);
  $("#cnt-similar").textContent = n ? `(${n})` : "";
  const panel = $("#tab-similar");
  if (!n) { panel.replaceChildren(el("div", { class: "empty small" }, "No related tickets for this answer.")); return; }
  const card = (t, extra) => el("div", { class: "card", "data-ticket": t.ticket_id },
    el("h4", {}, el("span", { class: "cite i" }, t.ticket_id), t.title),
    el("div", { class: "small" }, `${t.asset_name ?? ""} · ${t.status} · ${t.priority ?? ""}${extra}`),
    t.resolution_notes ? el("div", { class: "snippet" }, `Resolution: ${t.resolution_notes}`) : null);
  const nodes = [];
  if (similar?.length) {
    nodes.push(el("div", { class: "section-title" }, "Similar historical tickets"));
    similar.forEach((t) => nodes.push(card(t, ` · similarity ${t.similarity} · ${(t.match_reasons || []).join(", ")}`)));
  }
  if (linked?.length) {
    nodes.push(el("div", { class: "section-title" }, "Open tickets on asset and correlated assets"));
    linked.forEach((t) => nodes.push(card(t, "")));
  }
  panel.replaceChildren(...nodes);
}

function renderTrace(trace) {
  const steps = trace.steps || [];
  $("#cnt-trace").textContent = steps.length ? `(${steps.length})` : "";
  const nodes = [el("div", { class: "small" }, `Total ${fmtMs(trace.total_ms)} · trace_id `, el("span", { class: "mono" }, state.last?.trace_id || ""))];
  for (const s of steps) {
    const kind = s.kind;
    const head = el("div", { class: "head" }, pill(kind.toUpperCase(), kind === "rag" ? "rag" : kind === "mcp" ? "mcp" : ""));
    if (kind === "mcp") {
      head.append(...[s.ref ? el("span", { class: "cite t" }, s.ref) : null, el("span", { class: "name" }, s.tool), pill(s.server),
        pill(s.status, s.status === "ok" ? "ok" : "err"), pill(fmtMs(s.duration_ms))].filter(Boolean));
      if (s.retry_count) head.append(pill(`${s.retry_count} retries`, "warn"));
      if (s.error?.attempts > 1) head.append(pill(`${s.error.attempts} attempts`, "warn"));
      if (s.api_status_codes?.length) head.append(pill(`API ${s.api_status_codes.join(",")}`));
    } else if (kind === "rag") {
      head.append(el("span", { class: "name" }, "retrieve"), pill(`confidence ${s.confidence ?? s.status}`, s.confidence === "high" ? "ok" : s.confidence === "medium" ? "" : "warn"), pill(fmtMs(s.duration_ms)));
    } else if (kind === "plan") {
      head.append(el("span", { class: "name" }, `intent: ${s.intent}`), pill(`confidence ${s.confidence}`));
    } else if (kind === "llm") {
      head.append(el("span", { class: "name" }, s.model), pill(s.status, s.status === "ok" ? "ok" : "warn"), pill(fmtMs(s.duration_ms)));
    }
    const body = [];
    if (s.purpose) body.push(el("div", { class: "purpose" }, s.purpose));
    if (s.error) body.push(el("div", { class: "err-text" }, `${s.error.code}: ${s.error.message}`));
    if (kind === "plan") body.push(el("div", { class: "purpose" }, (s.planned_steps || []).join("  →  ")), el("details", {}, el("summary", {}, "entities"), el("pre", {}, JSON.stringify(s.entities, null, 2))));
    if (kind === "mcp") {
      if (s.depends_on?.length) body.push(el("div", { class: "purpose" }, `uses output of: ${s.depends_on.map((id) => refFor(id)).join(", ")}`));
      body.push(el("details", {}, el("summary", {}, "arguments"), el("pre", {}, JSON.stringify(s.arguments, null, 2))));
      if (s.output_preview) body.push(el("details", {}, el("summary", {}, "output (preview)"), el("pre", {}, JSON.stringify(s.output_preview, null, 2))));
      if (s.source_calls?.length) body.push(el("details", {}, el("summary", {}, `source API calls (${s.source_calls.length})`), el("pre", {}, JSON.stringify(s.source_calls, null, 2))));
    }
    if (kind === "rag") {
      body.push(el("div", { class: "purpose" }, `query: ${s.query}`));
      body.push(el("details", {}, el("summary", {}, `hits (${(s.hits || []).length})`), el("pre", {}, JSON.stringify({ filters: s.filters, hits: s.hits, excluded: s.excluded }, null, 2))));
    }
    nodes.push(el("div", { class: `step ${s.status}`, "data-step-ref": s.ref || "" }, head, ...body));
  }
  $("#tab-trace").replaceChildren(...nodes);
}
function refFor(id) {
  const step = (state.last?.trace?.steps || []).find((s) => s.id === id);
  return step ? `${step.ref || ""} ${step.tool}` : id;
}

// ------------------------------------------------------------------ draft
function renderDraft(d) {
  state.draft = d;
  $("#draft-empty").classList.add("hidden");
  const f = $("#draft-form"); f.classList.remove("hidden");
  $("#draft-id").textContent = d.draft_id;
  const st = $("#draft-status"); st.textContent = d.status; st.className = `pill ${d.status === "submitted" ? "ok" : d.status === "failed" ? "err" : ""}`;
  for (const k of ["title", "priority", "assignee_group", "asset_name", "alarm_name", "description"]) f.elements[k].value = d.fields[k] ?? "";
  f.elements.tags.value = (d.fields.tags || []).join(", ");
  $("#draft-docs").replaceChildren(...(d.fields.linked_documents || []).map((x) => el("li", {}, `${x.doc_id} – ${x.section || x.title}`)));
  $("#draft-similar").replaceChildren(...(d.fields.similar_ticket_ids || []).map((x) => el("li", {}, x)));
  const conflicts = d.rationale?.priority_conflicts || [];
  $("#draft-conflicts").replaceChildren(...conflicts.map((c) => el("div", { class: "banner warn" }, c)));
  const locked = d.status === "submitted";
  f.querySelectorAll("input:not([readonly]), select, textarea").forEach((i) => (i.disabled = locked));
  ["#draft-save", "#draft-reject", "#draft-approve"].forEach((s) => ($(s).disabled = locked));
  $("#draft-result").replaceChildren(locked ? el("div", { class: "banner ok" }, `Created ticket ${d.ticket_id}`) : "");
}
function draftFields() {
  const f = $("#draft-form").elements;
  return {
    ...state.draft.fields,
    title: f.title.value.trim(), priority: f.priority.value, assignee_group: f.assignee_group.value.trim() || null,
    description: f.description.value, tags: f.tags.value.split(",").map((t) => t.trim()).filter(Boolean).slice(0, 20),
  };
}
async function saveDraft() {
  try {
    const d = await api(`/api/drafts/${state.draft.draft_id}`, { method: "PUT", body: JSON.stringify({ fields: draftFields() }) });
    renderDraft(d);
    $("#draft-result").replaceChildren(el("div", { class: "banner ok" }, "Edits saved (not submitted)."));
  } catch (err) { $("#draft-result").replaceChildren(el("div", { class: "banner err" }, `Save failed: ${err.message}`)); }
}
function openConfirm() {
  if (!state.draft || state.draft.status === "submitted") return;
  const f = draftFields();
  $("#confirm-summary").replaceChildren(el("div", {}, el("strong", {}, f.title)), el("div", {}, `Priority ${f.priority} · ${f.asset_name} · ${f.assignee_group || "unassigned"}`),
    el("div", { class: "small" }, `${f.linked_documents.length} linked documents · ${f.similar_ticket_ids.length} similar tickets`));
  $("#confirm-check").checked = false;
  $("#confirm-name").value = localStorage.getItem("copilot-user") || CFG.user || "";
  $("#confirm-dialog").showModal();
}
async function approve() {
  const name = $("#confirm-name").value.trim();
  if (!name || !$("#confirm-check").checked) return;
  try { localStorage.setItem("copilot-user", name); } catch { /* storage unavailable */ }
  $("#draft-result").replaceChildren(el("div", { class: "loading" }, el("span", { class: "spinner" }), "Creating ticket through the ticketing MCP server…"));
  try {
    const r = await api(`/api/drafts/${state.draft.draft_id}/approve`, { method: "POST", body: JSON.stringify({ approved_by: name, confirm: true, fields: draftFields() }) });
    renderDraft(r.draft);
    $("#draft-result").replaceChildren(el("div", { class: "banner ok" }, `Ticket ${r.ticket_id} created${r.idempotent_replay ? " (already existed)" : ""}. Approved by ${name}.`));
    addMessage("bot", `Ticket **${r.ticket_id}** was created after approval by ${esc(name)}.`);
  } catch (err) {
    const e = err.body?.error;
    if (err.body?.draft) renderDraft(err.body.draft);
    $("#draft-result").replaceChildren(el("div", { class: "banner err" }, `Ticket creation failed: ${e ? `${e.code}: ${e.message}` : err.message}. The draft is kept; you can retry.`));
  }
}
async function rejectDraft() {
  try { renderDraft(await api(`/api/drafts/${state.draft.draft_id}/reject`, { method: "POST" })); }
  catch (err) { $("#draft-result").replaceChildren(el("div", { class: "banner err" }, err.message)); }
}

// ------------------------------------------------------------------ tools / audit / status
async function loadTools() {
  const panel = $("#tab-tools");
  try {
    const r = await api("/api/tools");
    const nodes = [el("div", { class: "small" }, `Discovered ${r.tools.length} tools at ${r.discovered_at || "-"} `, el("button", { class: "chip", type: "button", onclick: async () => { await api("/api/tools?refresh=true"); loadTools(); refreshStatus(); } }, "Re-discover"))];
    for (const [server, st] of Object.entries(r.servers)) {
      nodes.push(el("div", { class: "section-title" }, `${server} `, pill(st.status, st.status === "ok" ? "ok" : "err")));
      if (st.error) nodes.push(el("div", { class: "err-text" }, st.error));
      r.tools.filter((t) => t.server === server).forEach((t) => nodes.push(el("div", { class: "card" },
        el("h4", {}, el("span", { class: "mono" }, t.name), pill(t.read_only ? "read-only" : "WRITE · approval", t.read_only ? "" : "warn")),
        el("div", { class: "small" }, t.description),
        el("details", {}, el("summary", {}, "input schema"), el("pre", {}, JSON.stringify(t.input_schema, null, 2))),
        t.output_schema ? el("details", {}, el("summary", {}, "output schema"), el("pre", {}, JSON.stringify(t.output_schema, null, 2))) : null)));
    }
    panel.replaceChildren(...nodes);
  } catch (err) { panel.replaceChildren(el("div", { class: "banner err" }, `Tool discovery failed: ${err.message}`)); }
}
async function loadAudit() {
  const panel = $("#tab-audit");
  if (!state.conversationId) { panel.replaceChildren(el("div", { class: "empty small" }, "No conversation yet.")); return; }
  try {
    const r = await api(`/api/audit?conversation_id=${encodeURIComponent(state.conversationId)}&limit=200`);
    if (!r.events.length) { panel.replaceChildren(el("div", { class: "empty small" }, "No audit events.")); return; }
    panel.replaceChildren(...r.events.map((e) => el("div", { class: "card" },
      el("h4", {}, pill(e.action, /created|granted/.test(e.action) ? "ok" : /fail|reject/.test(e.action) ? "err" : ""), el("span", { class: "small" }, `${new Date(e.ts * 1000).toLocaleTimeString()} · ${e.actor}`)),
      el("details", {}, el("summary", {}, "detail"), el("pre", {}, JSON.stringify(e.detail, null, 2))))));
  } catch (err) { panel.replaceChildren(el("div", { class: "banner err" }, err.message)); }
}
async function refreshStatus() {
  const box = $("#server-status");
  try {
    const res = await fetch(API + "/ready");
    const r = await res.json();
    box.replaceChildren(...Object.entries(r.mcp_servers || {}).map(([n, s]) => pill(`${n}: ${s.status}`, s.status === "ok" ? "ok" : "err")),
      pill(`RAG index: ${r.rag_index ? "loaded" : "missing"}`, r.rag_index ? "rag" : "err"), pill(`LLM: ${r.llm_provider}`));
  } catch { box.replaceChildren(pill("backend unreachable", "err")); }
}

// ------------------------------------------------------------------ boot
function boot() {
  $("#sample-prompts").append(...SAMPLES.map((s) => el("button", { class: "chip", type: "button", onclick: () => send(s) }, s)));
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); const t = $("#input").value; $("#input").value = ""; send(t); });
  $("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } });
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("#draft-save").addEventListener("click", saveDraft);
  $("#draft-reject").addEventListener("click", rejectDraft);
  $("#draft-approve").addEventListener("click", openConfirm);
  $("#confirm-dialog").addEventListener("close", () => { if ($("#confirm-dialog").returnValue === "confirm") approve(); });
  refreshStatus(); setInterval(refreshStatus, 30000);
}
boot();
