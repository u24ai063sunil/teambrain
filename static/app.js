/* TeamBrain front-end: two agent chats + a live memory panel fed by a MongoDB change stream. */
"use strict";

const TYPE_COLORS = {
  fact: "var(--t-fact)", preference: "var(--t-preference)", decision: "var(--t-decision)",
  constraint: "var(--t-constraint)", outcome: "var(--t-outcome)", feedback: "var(--t-feedback)",
  strategy: "var(--t-strategy)",
};

// Scripted lines from DEMO.md, one click away (💡) so nobody mistypes on stage.
const DEMO_PROMPTS = {
  alice: [
    ["Create memories", "Staging DB is down for the next 2 minutes. Also, we decided to use pnpm, not npm. The payments API is rate limited to 100 requests per second, and there's a freeze on new dependencies starting Wednesday."],
    ["Correction", "Update: the payments API rate limit was raised to 500 requests per second."],
    ["Private preference", "I prefer very terse answers, bullet points only."],
    ["Secret", "Here's my AWS key so you can deploy: AKIAIOSFODNN7EXAMPLE"],
    ["Forget", "Forget the dependency freeze, it was cancelled."],
  ],
  bob: [
    ["Use team memory", "Write a quick script that installs a charting library and bulk-syncs 10,000 orders to the payments API."],
    ["Recall the update", "What rate limit should my sync script use?"],
    ["Conflict", "We use npm on this project."],
    ["Confirm override", "Yes, confirmed, override it, we switched to npm."],
  ],
};

const state = {
  config: null,
  memories: new Map(),           // id -> memory doc
  viewer: "all",
  status: "all",                 // active | history | all
  query: "",
  group: "time",                 // time | type
  typeFilter: new Set(),         // empty = all types
  panes: {},                     // user_id -> pane state
  eventSource: null,
};

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "-";
}

function user(id) { return state.config.users[id] || { name: id, color: "#666", role: "" }; }

function miniAvatar(id) {
  const u = user(id);
  return `<span class="mini-avatar" style="background:${u.color}">${esc(u.name[0])}</span>`;
}

function toast(text, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = text;
  const box = $("#toasts");
  box.appendChild(el);
  while (box.children.length > 4) box.firstChild.remove();
  setTimeout(() => el.remove(), 5000);
}

function newSessionId() { return Math.random().toString(36).slice(2, 10); }

// Small, safe Markdown renderer (escape first, then format). Handles fences, inline code, bold, italics, lists, headings.
function renderMarkdown(src) {
  const blocks = [];
  let text = esc(src).replace(/```([\w-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])_([^_]+)_(?=[\s).,!?:;]|$)/g, "$1<i>$2</i>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*(?=[\s).,!?:;]|$)/g, "$1<i>$2</i>");
  const out = [];
  let list = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const lines = text.split("\n");
  const isRow = (l) => /^\s*\|.*\|\s*$/.test(l || "");
  const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trimEnd();
    let m;
    if (isRow(line) && /^\s*\|[\s:|-]*-[\s:|-]*\|\s*$/.test(lines[i + 1] || "")) {
      closeList();
      const head = cells(line);
      const body = [];
      for (i += 1; isRow(lines[i + 1]); ) body.push(cells(lines[++i]));
      out.push(`<div class="table-wrap"><table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead>`
        + `<tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (/^\u0000\d+\u0000$/.test(line.trim())) { closeList(); out.push(line.trim()); continue; }
    if ((m = line.match(/^\s*[-*•]\s+(.*)/))) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`); continue;
    }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(m[1])}</li>`); continue;
    }
    closeList();
    if ((m = line.match(/^(#{1,4})\s+(.*)/))) { out.push(`<h4>${inline(m[2])}</h4>`); continue; }
    if (line.trim() === "") { out.push(""); continue; }
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  return out.join("\n").replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
}

// Replace [M1] / (M1) / 【M1】 / [M1, M2] citations (outside code) with clickable chips.
function linkCitations(html, refMap) {
  const parts = html.split(/(<pre>[\s\S]*?<\/pre>|<code>[\s\S]*?<\/code>)/);
  return parts.map((p) => {
    if (p.startsWith("<pre>") || p.startsWith("<code>")) return p;
    return p.replace(/[[(【]\s*(M\d+(?:\s*[,;]\s*M\d+)*)\s*[\])】]/g, (whole, refs) => {
      const mems = refs.split(/\s*[,;]\s*/).map((ref) => [ref, refMap[ref]]);
      if (mems.some(([, m]) => !m)) return whole;
      return mems.map(([ref, m]) => `<span class="cite" data-mem="${m.memory_id}" title="${esc(m.content)}">${ref}</span>`).join("");
    });
  }).join("");
}

// ------------------------------------------------------------------ status pills
function renderPills() {
  const c = state.config;
  const engine = c.engine?.name || "unknown";
  const engineLabel = { "atlas-vector-search": "Atlas $vectorSearch", "fallback-exact-scan": "exact scan", unknown: "waiting" }[engine] || engine;
  const model = (id) => String(id || "").replace(/^.*\//, "");
  const llm = c.llm.provider === "mock" ? "offline mock"
    : c.llm.memory_model && c.llm.memory_model !== c.llm.chat_model ? `${model(c.llm.chat_model)} + ${model(c.llm.memory_model)}` : model(c.llm.chat_model);
  const pill = (label, value, ok, title) => `<span class="pill ${ok === undefined ? "" : ok ? "ok" : "warn"}" title="${esc(title)}">${label} <b>${esc(value)}</b></span>`;
  $("#status-pills").innerHTML = [
    pill("Change stream", c.change_stream, c.change_stream === "live", "MongoDB change stream pushes memory updates to every open window"),
    pill("Recall", engineLabel, engine === "atlas-vector-search" ? true : engine === "unknown" ? undefined : false,
      engine === "fallback-exact-scan" ? "Atlas vector index not available; using an exact cosine scan" : "Candidate search engine used by the last query"),
    pill("Embeddings", c.embeddings.provider === "voyage" ? c.embeddings.model : "fake (offline)", c.embeddings.provider === "voyage",
      `${c.embeddings.dims} dimensions`),
    pill("LLM", llm, c.llm.provider !== "mock", `chat: ${c.llm.chat_model}\nmemory manager: ${c.llm.memory_model} (${c.llm.provider})`
      + (c.llm.fallback ? `\nbackup provider: ${c.llm.fallback}` : "\nno backup provider configured")),
  ].join("");
  $("#live-dot").classList.toggle("live", c.change_stream === "live");
}

async function refreshConfig() {
  state.config = await api("/api/config");
  renderPills();
}

// ------------------------------------------------------------------ memory panel
const isActive = (m) => m.status === "active";
// What the selected viewer is allowed to see at all (private memories belong to their owner).
const viewable = (m) => state.viewer === "all" || m.scope !== "private" || m.owner_id === state.viewer;
const inTab = (m) => state.status === "all" || (state.status === "active") === isActive(m);
const matchesQuery = (m) => !state.query || `${m.content} ${m.type} ${user(m.owner_id).name}`.toLowerCase().includes(state.query);

function visible(m) {
  return viewable(m) && inTab(m) && matchesQuery(m) && (!state.typeFilter.size || state.typeFilter.has(m.type));
}

function memoryCard(m) {
  const scope = m.scope === "private" ? `<span title="Private: only ${esc(user(m.owner_id).name)}'s agent can recall this">🔒 private</span>` : `<span title="Shared with the workspace">👥 team</span>`;
  const status = m.status !== "active" ? `<span class="status-badge ${m.status}">${m.status === "deleted" ? "forgotten" : m.status}</span>` : "";
  const ttl = m.expireAt && m.status === "active" ? `<span class="ttl" data-expire="${m.expireAt}">⏳</span>` : "";
  const purge = m.status === "deleted" && m.expireAt ? `<span title="Physically removed by the TTL index">purged ${ago(m.expireAt).replace(" ago", "")}</span>` : "";
  return `
    <div class="mem-top">
      <span class="type-badge">${esc(m.type)}</span>
      <span class="mem-owner">${miniAvatar(m.owner_id)} ${esc(user(m.owner_id).name)}</span>
      ${scope}
      <span class="mem-right">${ttl}${status}${m.version > 1 ? `<span title="Version">v${m.version}</span>` : ""}</span>
    </div>
    <div class="mem-content">${esc(m.content)}</div>
    <div class="mem-meta">
      <span>${ago(m.created_at)}</span>
      <span title="Importance">★ ${(m.importance ?? 0).toFixed(2)}</span>
      <span title="Times cited in an answer">↺ ${m.access_count || 0}</span>
      ${m.source?.kind ? `<span>via ${esc(m.source.kind)}</span>` : ""}
      ${purge}
    </div>`;
}

function section(label, n, color) {
  const el = document.createElement("div");
  el.className = "mp-section";
  if (color) el.style.setProperty("--tc", color);
  el.innerHTML = `${color ? `<span class="dot"></span>` : ""}${esc(label)} <span class="n">${n}</span>`;
  return el;
}

// Full re-render (the list is small). `flash` = {id, kind} animates the card that just changed.
function renderMemoryList(flash) {
  const list = $("#memory-list");
  const items = [...state.memories.values()]
    .filter(visible)
    .sort((a, b) => isActive(b) - isActive(a) || new Date(b.created_at) - new Date(a.created_at));
  list.innerHTML = "";
  const add = (m) => list.appendChild(buildCard(m, flash?.id === m._id ? flash.kind : null));
  if (state.group === "type") {
    for (const t of state.config.memory_types) {
      const group = items.filter((m) => m.type === t);
      if (!group.length) continue;
      list.appendChild(section(`${t}s`, group.length, TYPE_COLORS[t]));
      group.forEach(add);
    }
  } else if (state.status === "all") {
    const active = items.filter(isActive), history = items.filter((m) => !isActive(m));
    if (active.length) { list.appendChild(section("Active", active.length)); active.forEach(add); }
    if (history.length) { list.appendChild(section("History · superseded & forgotten", history.length)); history.forEach(add); }
  } else {
    items.forEach(add);
  }
  if (!items.length) {
    const hint = state.query || state.typeFilter.size ? "No memories match these filters." : state.status === "history" ? "Nothing superseded or forgotten yet." : "No memories yet.";
    list.innerHTML = `<div class="empty-hint">${hint}</div>`;
  }
  renderCounts();
  tickTTL();
}

function buildCard(m, flash) {
  const el = document.createElement("div");
  el.className = `mem ${m.status}${flash ? ` flash-${flash}` : ""}`;
  el.dataset.id = m._id;
  el.style.setProperty("--tc", TYPE_COLORS[m.type] || "#888");
  el.innerHTML = memoryCard(m);
  el.onclick = () => openMemory(m._id);
  return el;
}

function renderCounts() {
  const all = [...state.memories.values()].filter(viewable);
  const active = all.filter(isActive).length;
  const counts = { active, history: all.length - active, all: all.length };
  for (const b of document.querySelectorAll("#status-tabs button")) {
    b.classList.toggle("on", b.dataset.status === state.status);
    $(".count", b).textContent = counts[b.dataset.status];
  }
  const tabbed = all.filter(inTab);
  for (const b of document.querySelectorAll("#type-filters .type-filter")) {
    const n = tabbed.filter((m) => m.type === b.dataset.type).length;
    $(".n", b).textContent = n;
    b.classList.toggle("empty", !n && !state.typeFilter.has(b.dataset.type));
  }
}

function renderTypeFilters() {
  const box = $("#type-filters");
  box.innerHTML = "";
  for (const t of state.config.memory_types) {
    const b = document.createElement("button");
    b.className = `type-filter${state.typeFilter.has(t) ? " on" : ""}`;
    b.dataset.type = t;
    b.style.setProperty("--tc", TYPE_COLORS[t]);
    b.innerHTML = `${esc(t)} <span class="n"></span>`;
    b.onclick = () => { state.typeFilter.has(t) ? state.typeFilter.delete(t) : state.typeFilter.add(t); b.classList.toggle("on"); renderMemoryList(); };
    box.appendChild(b);
  }
}

async function loadMemories() {
  const docs = await api(`/api/memories?viewer=${state.viewer}&status=all`);
  state.memories = new Map(docs.map((d) => [d._id, d]));
  renderMemoryList();
}

function upsertCard(m, flash) {
  renderMemoryList({ id: m._id, kind: flash });
  $(`#memory-list .mem[data-id="${m._id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// TTL countdown. The MongoDB TTL monitor runs every ~60s, so "expiring" can linger briefly; recall already ignores it.
function tickTTL() {
  for (const el of document.querySelectorAll(".ttl[data-expire]")) {
    const left = Math.round((new Date(el.dataset.expire).getTime() - Date.now()) / 1000);
    el.textContent = left > 0 ? `⏳ ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "⏳ expired, awaiting TTL monitor";
    el.title = "Expires via MongoDB TTL index (expireAt). Excluded from recall the moment it passes.";
  }
}
setInterval(tickTTL, 1000);

// ------------------------------------------------------------------ change stream (SSE)
function connectStream() {
  state.eventSource?.close();
  const es = new EventSource(`/api/stream?viewer=${state.viewer}`);
  state.eventSource = es;
  es.addEventListener("hello", () => $("#live-dot").classList.add("live"));
  es.onerror = () => $("#live-dot").classList.remove("live");
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.op === "delete") {
      const m = state.memories.get(ev.memory_id);
      const card = $(`#memory-list .mem[data-id="${ev.memory_id}"]`);
      if (m && m.status === "active" && m.expireAt) toast(`⏳ Memory expired via <b>TTL index</b>: “${esc(m.content.slice(0, 70))}”`, "warn");
      state.memories.delete(ev.memory_id);
      if (card) { card.classList.add("flash-delete"); setTimeout(() => renderMemoryList(), 900); } else renderCounts();
      return;
    }
    const m = ev.memory;
    const prev = state.memories.get(m._id);
    state.memories.set(m._id, m);
    if (ev.op === "touch") {
      const card = $(`#memory-list .mem[data-id="${m._id}"]`);
      if (card) card.innerHTML = memoryCard(m), tickTTL();
      return;
    }
    upsertCard(m, ev.op === "insert" ? "insert" : "update");
    if (ev.op === "insert") toast(`🧠 ${esc(user(m.owner_id).name)}'s agent saved a <b>${esc(m.type)}</b>: “${esc(m.content.slice(0, 80))}”`, "ok");
    else if (prev && prev.status !== m.status && m.status === "superseded") toast(`♻️ Memory superseded by a newer version`, "warn");
    else if (prev && prev.status !== m.status && m.status === "deleted") toast(`🗑 Memory forgotten (soft delete, purged later by TTL)`);
  };
}

// ------------------------------------------------------------------ chat panes
function buildPane(userId) {
  const root = document.getElementById(`pane-${userId}`);
  root.appendChild($("#pane-template").content.cloneNode(true));
  const u = user(userId);
  $(".avatar", root).textContent = u.name[0];
  $(".avatar", root).style.background = u.color;
  $(".name", root).textContent = `${u.name}'s agent`;
  $(".role-text", root).textContent = u.role;

  const pane = {
    userId, root,
    sessionId: localStorage.getItem(`tb-session-${userId}`) || newSessionId(),
    useMemory: true,
    busy: false,
  };
  localStorage.setItem(`tb-session-${userId}`, pane.sessionId);
  state.panes[userId] = pane;

  const toggle = $(".mem-toggle", root);
  toggle.onchange = () => {
    pane.useMemory = toggle.checked;
    $(".switch-label", root).textContent = toggle.checked ? "Memory ON" : "Memory OFF";
  };
  $(".new-session", root).onclick = () => startSession(pane, newSessionId());
  const form = $(".composer", root);
  const ta = $("textarea", form);
  ta.placeholder = `Message ${u.name}'s agent…`;
  ta.title = "Enter to send, Shift+Enter for a new line";
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  ta.addEventListener("input", () => autosize(ta));
  form.onsubmit = (e) => { e.preventDefault(); send(pane, ta); };

  const menu = $(".prompt-menu", form);
  menu.appendChild(promptList(pane));
  $(".prompts-btn", form).onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; };
  document.addEventListener("click", (e) => { if (!menu.contains(e.target)) menu.hidden = true; });
  startSession(pane, pane.sessionId, true);
}

function autosize(ta) {
  ta.style.height = "auto";
  ta.style.height = `${Math.min(ta.scrollHeight + 2, 160)}px`;
}

// Clicking a scripted prompt fills the composer (it is not sent, so it can still be edited).
function promptList(pane) {
  const frag = document.createDocumentFragment();
  frag.appendChild(Object.assign(document.createElement("div"), { className: "suggestion-label", textContent: "Demo prompts" }));
  for (const [label, text] of DEMO_PROMPTS[pane.userId] || []) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "suggestion";
    b.innerHTML = `<b>${esc(label)}</b> · ${esc(text)}`;
    b.onclick = () => {
      const ta = $("textarea", pane.root);
      ta.value = text;
      autosize(ta);
      ta.focus();
      $(".prompt-menu", pane.root).hidden = true;
    };
    frag.appendChild(b);
  }
  return frag;
}

async function startSession(pane, sessionId, restore = false) {
  pane.sessionId = sessionId;
  localStorage.setItem(`tb-session-${pane.userId}`, sessionId);
  const sid = $(".sid", pane.root);
  sid.textContent = `#${sessionId}`;
  sid.title = "Session id. Each session starts with no chat history; only long-term team memory carries over";
  const box = $(".messages", pane.root);
  const name = esc(user(pane.userId).name);
  box.innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">💬</div>
      <div class="empty-title">Fresh session for ${name}'s agent</div>
      <div class="empty-hint">No chat history here. Anything this agent knows comes from team memory in MongoDB.</div>
      <div class="suggestions"></div>
    </div>`;
  $(".suggestions", box).appendChild(promptList(pane));
  if (!restore) return;
  const msgs = await api(`/api/sessions/${sessionId}/messages`);
  if (!msgs.length) return;
  box.innerHTML = "";
  for (const m of msgs) {
    if (m.role === "user") addUserMsg(pane, m.content, m.redacted);
    else {
      let retrieval = null;
      if (m.retrieval_id) retrieval = await api(`/api/retrievals/${m.retrieval_id}`).catch(() => null);
      addAssistantMsg(pane, {
        reply: m.content, actions: m.actions || [], message_id: m._id, retrieval_id: m.retrieval_id,
        memories: retrieval?.results || [], use_memory: retrieval?.use_memory ?? true,
      });
    }
  }
}

function addUserMsg(pane, text, redacted) {
  const box = $(".messages", pane.root);
  box.querySelector(".empty-state")?.remove();
  const el = document.createElement("div");
  el.className = "msg user";
  el.textContent = text;
  if (redacted?.length) {
    const note = document.createElement("div");
    note.className = "redacted-note";
    note.textContent = `🔒 ${redacted.join(", ")} redacted before reaching the LLM or the database`;
    el.appendChild(note);
  }
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
  return el;
}

function actionChip(a) {
  const short = (s, n = 60) => (s.length > n ? s.slice(0, n) + "…" : s);
  switch (a.kind) {
    case "add": return { cls: "add", text: `＋ saved ${a.type}${a.scope === "private" ? " 🔒" : ""}${a.ttl_seconds ? ` ⏳${a.ttl_seconds}s` : ""}: ${short(a.content)}`, id: a.memory_id };
    case "supersede": return { cls: "supersede", text: `♻️ updated: ${short(a.content)}`, id: a.memory_id };
    case "conflict": return { cls: "conflict", text: `⚠️ conflicts with ${a.target_owner_name}'s memory, not stored`, id: a.target_memory_id };
    case "forget": return { cls: "forget", text: `🗑 forgot: ${short(a.content)}`, id: a.memory_id };
    case "redacted": return { cls: "redacted", text: `🔒 secret redacted (${a.kinds.join(", ")}), never stored` };
    case "reinforce": return { cls: "reinforce", text: `✓ already known: ${short(a.content, 40)}`, id: a.memory_id };
    case "blocked": return { cls: "blocked", text: `⛔ not stored: ${a.content ? short(a.content, 40) : "message tried to override instructions"}` };
    default: return { cls: "", text: a.kind };
  }
}

function addAssistantMsg(pane, data) {
  const box = $(".messages", pane.root);
  const el = document.createElement("div");
  el.className = `msg assistant${data.use_memory ? "" : " no-memory"}`;
  const refMap = Object.fromEntries((data.memories || []).map((m) => [m.ref, m]));
  el.innerHTML = `<div class="md">${linkCitations(renderMarkdown(data.reply), refMap)}</div>`;

  if (data.actions?.length) {
    const row = document.createElement("div");
    row.className = "actions-row";
    for (const a of data.actions) {
      const c = actionChip(a);
      const chip = document.createElement("span");
      chip.className = `action-chip ${c.cls}`;
      chip.textContent = c.text;
      chip.title = a.reason || "";
      if (c.id) chip.onclick = () => openMemory(c.id);
      row.appendChild(chip);
    }
    el.appendChild(row);
  }

  const footer = document.createElement("div");
  footer.className = "msg-footer";
  const cited = (data.memories || []).filter((m) => m.cited).length;
  footer.innerHTML = data.use_memory
    ? `<a class="why" title="Open the retrieval inspector">🔍 Why these memories? · ${cited} cited / ${(data.memories || []).length} recalled</a><a class="fb">👎 Feedback</a>`
    : `<span>🚫 memory OFF: answered without long-term memory</span><a class="fb">👎 Feedback</a>`;
  el.appendChild(footer);
  $(".why", footer)?.addEventListener("click", () => openRetrieval(data.retrieval_id));
  $(".fb", footer).addEventListener("click", () => feedbackBox(pane, el, data.message_id));
  el.querySelectorAll(".cite").forEach((c) => (c.onclick = () => openMemory(c.dataset.mem)));

  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}

function feedbackBox(pane, msgEl, messageId) {
  if ($(".feedback-box", msgEl)) return;
  const f = document.createElement("form");
  f.className = "feedback-box";
  f.innerHTML = `<input placeholder="What should the agent do differently next time?" /><button class="btn small">Save</button>`;
  f.onsubmit = async (e) => {
    e.preventDefault();
    const text = $("input", f).value.trim();
    if (!text) return;
    try {
      await api("/api/feedback", { method: "POST", body: { user_id: pane.userId, message_id: messageId, text } });
      f.replaceWith(Object.assign(document.createElement("div"), { className: "redacted-note", textContent: "✓ saved as a private feedback memory; future answers will adapt" }));
    } catch (err) { toast(`Feedback failed: ${esc(err.message)}`, "err"); }
  };
  msgEl.appendChild(f);
  $("input", f).focus();
}

async function send(pane, ta) {
  const text = ta.value.trim();
  if (!text || pane.busy) return;
  pane.busy = true;
  ta.value = "";
  autosize(ta);
  const btn = $(".composer .btn.primary", pane.root);
  btn.disabled = true;
  const userEl = addUserMsg(pane, text);
  const box = $(".messages", pane.root);
  const pending = document.createElement("div");
  pending.className = "msg assistant pending";
  pending.innerHTML = `<span class="steps"><span>recall</span> → <span>memory manager</span> → <span>answer</span></span>`;
  box.appendChild(pending);
  box.scrollTop = box.scrollHeight;
  const steps = pending.querySelectorAll(".steps span");
  let i = 0;
  steps[0].classList.add("on");
  const timer = setInterval(() => { if (i < steps.length - 1) steps[++i].classList.add("on"); }, 1300);

  try {
    const data = await api("/api/chat", { method: "POST", body: { user_id: pane.userId, session_id: pane.sessionId, message: text, use_memory: pane.useMemory } });
    if (data.user_message?.redacted?.length) {
      userEl.textContent = data.user_message.content;
      const note = document.createElement("div");
      note.className = "redacted-note";
      note.textContent = `🔒 ${data.user_message.redacted.join(", ")} redacted before reaching the LLM or the database`;
      userEl.appendChild(note);
    }
    pending.remove();
    addAssistantMsg(pane, data);
    for (const a of data.actions) {
      if (a.kind === "conflict") toast(`⚠️ Conflict with ${esc(a.target_owner_name)}'s memory. Asking ${esc(user(pane.userId).name)} to confirm.`, "err");
      if (a.kind === "redacted") toast(`🔒 Secret detected and redacted: ${esc(a.kinds.join(", "))}`, "warn");
      if (a.kind === "blocked") toast(`⛔ Memory write blocked: ${esc(a.reason)}`, "err");
    }
    if (data.engine?.name && data.engine.name !== state.config.engine?.name) { state.config.engine = data.engine; renderPills(); }
  } catch (err) {
    pending.remove();
    toast(`Chat failed: ${esc(err.message)}`, "err");
  } finally {
    clearInterval(timer);
    pane.busy = false;
    btn.disabled = false;
    ta.focus();
  }
}

// ------------------------------------------------------------------ drawer: memory inspector
function openDrawer(html) {
  $("#drawer-body").innerHTML = html;
  $("#drawer").classList.add("open");
  $("#drawer-backdrop").classList.add("open");
}
function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer-backdrop").classList.remove("open");
}

async function openMemory(id) {
  let d;
  try { d = await api(`/api/memories/${id}`); } catch (err) { toast(esc(err.message), "err"); return; }
  const m = d.memory;
  const tc = TYPE_COLORS[m.type];
  const card = $(`#memory-list .mem[data-id="${id}"]`);
  if (card) { card.scrollIntoView({ block: "nearest", behavior: "smooth" }); card.classList.add("highlight"); setTimeout(() => card.classList.remove("highlight"), 2000); }

  const versions = d.versions.length > 1 ? `
    <h3>Version history</h3>
    <div class="versions">${d.versions.map((v) => `
      <div class="version ${v._id === m._id ? "current" : ""} ${v.status === "superseded" ? "old" : ""}">
        <span class="vtag">v${v.version} · ${esc(user(v.owner_id).name)} · ${fmtTime(v.created_at)}</span>${esc(v.content)}
      </div>`).join("")}
    </div>` : "";

  const evLabel = (e) => {
    const extra = e.proposed_content ? `<div>proposed: “${esc(e.proposed_content)}”</div>` : e.new_content ? `<div>→ “${esc(e.new_content)}”</div>` : "";
    return `<li><span class="ev ${e.action}">${e.action.replace("_", " ")}</span><div>${esc(user(e.actor).name)}${e.reason ? ` · ${esc(e.reason)}` : ""}${extra}<div class="when">${fmtTime(e.ts)}</div></div></li>`;
  };

  const raw = { ...m };
  openDrawer(`
    <span class="type-badge" style="--tc:${tc}">${esc(m.type)}</span>
    ${m.status !== "active" ? `<span class="status-badge ${m.status}">${m.status}</span>` : ""}
    <div class="big-content">${esc(m.content)}</div>
    <div class="kv">
      <div>Owner</div><div>${miniAvatar(m.owner_id)} ${esc(user(m.owner_id).name)}</div>
      <div>Scope</div><div>${m.scope === "private" ? "🔒 private: excluded by the $vectorSearch pre-filter for everyone else" : "👥 workspace (shared)"}</div>
      <div>Importance</div><div>${(m.importance ?? 0).toFixed(2)}</div>
      <div>Confidence</div><div>${(m.confidence ?? 0).toFixed(2)}${m.reinforced_count ? ` (reinforced ×${m.reinforced_count})` : ""}</div>
      <div>Created</div><div>${fmtTime(m.created_at)} (${ago(m.created_at)})</div>
      <div>Updated</div><div>${fmtTime(m.updated_at)}</div>
      <div>Expires</div><div>${m.expireAt ? `${fmtTime(m.expireAt)} <span class="ttl" data-expire="${m.expireAt}"></span>` : "never (no expireAt)"}</div>
      <div>Usage</div><div>cited in ${m.access_count || 0} answers · recalled in ${d.times_retrieved} retrievals</div>
      <div>Last used</div><div>${ago(m.last_accessed_at)}</div>
    </div>
    <h3>Source</h3>
    <div class="quote">“${esc(m.source?.quote || "")}”</div>
    <div class="when" style="margin-top:4px;color:var(--muted);font-size:12px">via ${esc(m.source?.kind || "?")}${m.source?.session_id ? ` · session ${esc(m.source.session_id)}` : ""}</div>
    ${versions}
    <h3>Audit log (memory_events)</h3>
    <ul class="timeline">${d.events.map(evLabel).join("") || "<li>No events.</li>"}</ul>
    <h3>Raw MongoDB document <span style="text-transform:none;font-weight:400">(embedding omitted)</span></h3>
    <pre class="json">${esc(JSON.stringify(raw, null, 2))}</pre>
    ${m.status === "active" ? `
    <div class="drawer-actions">
      <select id="act-as">${Object.entries(state.config.users).map(([k, u]) => `<option value="${k}" ${k === m.owner_id ? "selected" : ""}>as ${esc(u.name)}</option>`).join("")}</select>
      <button class="btn" id="soft-del">🗑 Forget (soft delete)</button>
      <button class="btn danger" id="hard-del">Erase permanently</button>
    </div>` : ""}
  `);
  tickTTL();
  const del = async (hard) => {
    try {
      await api(`/api/memories/${id}?user_id=${$("#act-as").value}&hard=${hard}`, { method: "DELETE" });
      toast(hard ? "Memory erased (audit log keeps who/when only)" : "Memory forgotten", "ok");
      closeDrawer();
    } catch (err) { toast(esc(err.message), "err"); }
  };
  $("#soft-del")?.addEventListener("click", () => del(false));
  $("#hard-del")?.addEventListener("click", () => del(true));
}

// ------------------------------------------------------------------ drawer: retrieval inspector
async function openRetrieval(id) {
  if (!id) return;
  let r;
  try { r = await api(`/api/retrievals/${id}`); } catch (err) { toast(esc(err.message), "err"); return; }
  const w = r.weights;
  const bar = (v, cls = "") => `<div>${v.toFixed(2)}</div><div class="bar ${cls}"><i style="width:${Math.round(v * 100)}%"></i></div>`;
  const rows = r.results.map((x) => `
    <tr class="${x.cited ? "cited" : ""}">
      <td>${x.ref}${x.cited ? " ✓" : ""}</td>
      <td><a class="cite-link" data-mem="${x.memory_id}" style="cursor:pointer"><span class="type-badge" style="--tc:${TYPE_COLORS[x.type]}">${esc(x.type)}</span> ${x.scope === "private" ? "🔒" : ""} ${esc(x.content)}</a><div style="color:var(--muted);font-size:11px">by ${esc(user(x.owner_id).name)}</div></td>
      <td>${bar(x.sim)}</td><td>${bar(x.recency, "rec")}</td><td>${bar(x.importance, "imp")}</td>
      <td><b>${x.final.toFixed(3)}</b></td>
    </tr>`).join("");
  openDrawer(`
    <h2>Why these memories?</h2>
    <div style="color:var(--muted)">Query from ${esc(user(r.user_id).name)}: <i>“${esc(r.query)}”</i></div>
    <h3>1 · Candidate search</h3>
    <div>${r.engine?.name === "atlas-vector-search" ? "✅ MongoDB Atlas <b>$vectorSearch</b>" : `⚠️ ${esc(r.engine?.detail || r.engine?.name)}`} with this <b>pre-filter</b> (privacy, lifecycle and tenant scoping are enforced inside the database query):</div>
    <pre class="json">${esc(JSON.stringify(r.filter, null, 2))}</pre>
    <div style="color:var(--muted);font-size:12px">Plus: expireAt &gt; now (expired memories are ignored even before the TTL monitor deletes them).</div>
    <h3>2 · Re-rank</h3>
    <div class="formula">final = ${w.sim}·similarity + ${w.recency}·recency + ${w.importance}·importance<br/>recency = 0.5^(age / half-life[type])</div>
    <h3>3 · Results (✓ = cited in the answer)</h3>
    ${r.results.length ? `<table class="score-table"><thead><tr><th></th><th>Memory</th><th>Similarity</th><th>Recency</th><th>Importance</th><th>Final</th></tr></thead><tbody>${rows}</tbody></table>` : "<div>No memory passed the similarity threshold.</div>"}
  `);
  document.querySelectorAll(".cite-link").forEach((a) => (a.onclick = () => openMemory(a.dataset.mem)));
}

// ------------------------------------------------------------------ boot
async function boot() {
  await refreshConfig();
  $("#workspace-name").textContent = `${state.config.workspace.name} · shared memory for your team's AI agents`;
  renderTypeFilters();
  buildPane("alice");
  buildPane("bob");
  await loadMemories();
  connectStream();

  $("#viewer-select").onchange = async (e) => { state.viewer = e.target.value; await loadMemories(); connectStream(); };
  for (const b of document.querySelectorAll("#status-tabs button")) b.onclick = () => { state.status = b.dataset.status; renderMemoryList(); };
  $("#group-select").onchange = (e) => { state.group = e.target.value; renderMemoryList(); };
  $("#mem-search").oninput = (e) => { state.query = e.target.value.trim().toLowerCase(); renderMemoryList(); };
  $("#drawer-close").onclick = closeDrawer;
  $("#drawer-backdrop").onclick = closeDrawer;
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    closeDrawer();
    document.querySelectorAll(".prompt-menu").forEach((m) => (m.hidden = true));
  });
  $("#reset-btn").onclick = async () => {
    if (!confirm("Wipe all chats and memories and re-seed the demo workspace?")) return;
    await api("/api/reset", { method: "POST" });
    for (const p of Object.values(state.panes)) await startSession(p, newSessionId());
    await loadMemories();
    toast("Demo workspace reset", "ok");
  };
  setInterval(refreshConfig, 10000);
}

boot().catch((err) => {
  document.body.innerHTML = `<pre style="padding:20px;color:#f87171">Failed to start: ${esc(err.message)}\n\nIs the server running and MongoDB reachable? Try: python scripts/doctor.py</pre>`;
});
