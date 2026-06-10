// app.js — vanilla ES module. Three parallel state buffers feed from
// one SSE connection: conversation, timeline, raw stream.

const CATEGORY = {
  RUN_STARTED: "lifecycle", RUN_FINISHED: "lifecycle", RUN_ERROR: "lifecycle",
  TEXT_MESSAGE_START: "text", TEXT_MESSAGE_CONTENT: "text", TEXT_MESSAGE_END: "text",
  TOOL_CALL_START: "tool", TOOL_CALL_ARGS: "tool", TOOL_CALL_END: "tool",
  STATE_SNAPSHOT: "state", STATE_DELTA: "state", STATE_UPDATED: "state",
  FLOW_STARTED: "flow", FLOW_STATE_CHANGED: "flow", FLOW_COMPLETED: "flow", FLOW_ERROR: "flow",
  REASONING_START: "reasoning", REASONING_STEP: "reasoning", REASONING_END: "reasoning",
  INTENT_CLASSIFIED: "reasoning",
  MEMORY_RETRIEVED: "memory",
  RAG_HIT: "rag",
  GUARDRAIL_CHECK: "guardrail",
  COGNITIVE_MODE_CHANGED: "reasoning",
};

const $ = (sel) => document.querySelector(sel);

const state = {
  apiKey: localStorage.getItem("beui-api-key") || "",
  properties: [],
  scenario: null,
  streaming: false,
  currentAssistantText: "",
  currentAssistantMessageId: null,
  conversationHistory: [],
};

function setApiKey(k) {
  state.apiKey = k;
  if (k) localStorage.setItem("beui-api-key", k);
  else localStorage.removeItem("beui-api-key");
  localStorage.setItem("beui-api-key-prompted", "1");
  $("#api-key-indicator").classList.toggle("hidden", !k);
}

function apiHeaders(extra) {
  extra = extra || {};
  const h = Object.assign({"Content-Type": "application/json"}, extra);
  if (state.apiKey) h["X-API-Key"] = state.apiKey;
  return h;
}

async function api(path, init) {
  init = init || {};
  const resp = await fetch(path, Object.assign({}, init, {headers: apiHeaders(init.headers)}));
  if (!resp.ok) throw new Error(path + ": " + resp.status + " " + (await resp.text()));
  return resp.json();
}

async function loadProperties() {
  state.properties = await api("/test-ui/properties");
  const sel = $("#property-select");
  sel.replaceChildren();
  for (const p of state.properties) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.name;
    sel.appendChild(opt);
  }
  $("#seed-btn").disabled = state.properties.length === 0;
  if (state.properties.length === 0) $("#seed-status").textContent = "no properties for this workspace";
}

async function loadChannels() {
  const channels = await api("/test-ui/channels");
  const sel = $("#res-channel");
  for (const code of channels) {
    const opt = document.createElement("option");
    opt.value = code;
    opt.textContent = code;
    sel.appendChild(opt);
  }
}

function seedRequestFromForm() {
  const body = {
    property_id: $("#property-select").value,
    guest_first_message: $("#first-message").value,
  };
  const add = (key, value) => {
    if (value !== "" && value !== null && value !== undefined) body[key] = value;
  };
  // datetime-local returns local-naive ISO — send as-is; FastAPI parses and the
  // router converts to UTC ISO before forwarding to PMS.
  add("check_in", $("#check-in").value || null);
  add("check_out", $("#check-out").value || null);
  add("status", $("#res-status").value);
  add("channel_code", $("#res-channel").value);
  const adults = parseInt($("#res-adults").value, 10);
  if (!Number.isNaN(adults)) body.adults = adults;
  const children = parseInt($("#res-children").value, 10);
  if (!Number.isNaN(children)) body.children = children;
  const amount = parseFloat($("#res-amount").value);
  if (!Number.isNaN(amount)) body.amount = amount;
  add("currency", ($("#res-currency").value || "").toUpperCase());
  body.paid = $("#res-paid").checked;
  add("guest_first_name", $("#guest-first").value);
  add("guest_last_name", $("#guest-last").value);
  return body;
}

function reservationContextFromForm() {
  // Snapshot of the reservation form so we can ship it inside every
  // run's `state.reservation_context`. This is the source of truth the
  // pipeline grounds the system prompt on; without it the agent has no
  // way to quote the dates the user picked in the seed panel.
  const ctx = {};
  const set = (key, value) => {
    if (value !== "" && value !== null && value !== undefined) ctx[key] = value;
  };
  const checkIn = $("#check-in").value || "";
  const checkOut = $("#check-out").value || "";
  if (checkIn) {
    const [date, time] = checkIn.split("T");
    set("check_in", date);
    if (time) set("check_in_time", time.slice(0, 5));
  }
  if (checkOut) {
    const [date, time] = checkOut.split("T");
    set("check_out", date);
    if (time) set("check_out_time", time.slice(0, 5));
  }
  set("status", $("#res-status").value);
  set("booking_channel", $("#res-channel").value);
  const adults = parseInt($("#res-adults").value, 10);
  if (!Number.isNaN(adults)) ctx.num_guests = adults;
  const children = parseInt($("#res-children").value, 10);
  if (!Number.isNaN(children)) ctx.num_children = children;
  const amount = parseFloat($("#res-amount").value);
  if (!Number.isNaN(amount)) ctx.total_price = String(amount);
  set("currency", ($("#res-currency").value || "").toUpperCase());
  const first = $("#guest-first").value || "";
  const last = $("#guest-last").value || "";
  const fullName = `${first} ${last}`.trim();
  if (fullName) ctx.guest_name = fullName;
  ctx.current_time = new Date().toISOString();
  return ctx;
}

async function seed() {
  $("#seed-btn").disabled = true;
  $("#seed-status").textContent = "seeding...";
  try {
    state.scenario = await api("/test-ui/seed", {
      method: "POST",
      body: JSON.stringify(seedRequestFromForm()),
    });
    state.scenario.reservation_context = reservationContextFromForm();
    $("#seed-status").textContent = "seeded reservation=" + state.scenario.reservation_id;
    renderSeededConversation();
    $("#reset-btn").disabled = false;
    $("#compose-input").disabled = false;
    $("#compose-send").disabled = false;
  } catch (e) {
    $("#seed-status").textContent = "seed failed: " + e.message;
    $("#seed-btn").disabled = false;
  }
}

function renderSeededConversation() {
  $("#messages").replaceChildren();
  const firstMsg = $("#first-message").value;
  appendMessage("user", firstMsg);
  state.conversationHistory = [{role: "user", content: firstMsg}];
}

function resetScenario() {
  state.scenario = null;
  state.currentAssistantText = "";
  state.currentAssistantMessageId = null;
  state.conversationHistory = [];
  $("#messages").replaceChildren();
  $("#timeline").replaceChildren();
  $("#raw-stream").textContent = "";
  $("#seed-btn").disabled = false;
  $("#reset-btn").disabled = true;
  $("#compose-input").disabled = true;
  $("#compose-send").disabled = true;
  $("#seed-status").textContent = "";
}

function appendMessage(role, text, opts) {
  opts = opts || {};
  const div = document.createElement("div");
  div.className = "msg " + role + (opts.streaming ? " streaming" : "");
  div.textContent = text;
  $("#messages").appendChild(div);
  div.scrollIntoView({block: "end"});
  return div;
}

function appendTimeline(evt) {
  const t = new Date().toISOString().slice(11, 23);
  const category = CATEGORY[evt.type] || "state";
  let klass = category;
  if (evt.type === "GUARDRAIL_CHECK") klass = evt.decision === "fail" ? "guardrail-fail" : "guardrail-pass";
  const li = document.createElement("li");
  li.className = klass;
  const code = document.createElement("code");
  code.textContent = t;
  const strong = document.createElement("strong");
  strong.textContent = evt.type;
  const span = document.createElement("span");
  span.textContent = summarize(evt);
  li.append(code, " ", strong, " ", span);
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(evt, null, 2);
  li.appendChild(pre);
  li.addEventListener("click", () => li.classList.toggle("open"));
  $("#timeline").appendChild(li);
}

function summarize(evt) {
  switch (evt.type) {
    case "INTENT_CLASSIFIED": return evt.intent + " (" + (evt.confidence || 0).toFixed(2) + ")";
    case "MEMORY_RETRIEVED":  return evt.tier + " " + (evt.hits ? evt.hits.length : 0) + " hits " + Math.round(evt.latency_ms) + "ms";
    case "RAG_HIT":           return evt.source + " " + (evt.docs ? evt.docs.length : 0) + " docs " + Math.round(evt.latency_ms) + "ms";
    case "GUARDRAIL_CHECK":   return evt.check_name + "=" + evt.decision;
    case "COGNITIVE_MODE_CHANGED": return evt.from + "->" + evt.to + " (" + evt.trigger + ")";
    case "TEXT_MESSAGE_CONTENT": return (evt.delta || "").slice(0, 60);
    default: return "";
  }
}

function appendRaw(chunk) {
  $("#raw-stream").textContent += chunk;
}

function nextFrameBoundary(buf) {
  const a = buf.indexOf("\n\n");
  const b = buf.indexOf("\r\n\r\n");
  if (a === -1) return b === -1 ? null : {index: b, len: 4};
  if (b === -1) return {index: a, len: 2};
  return a < b ? {index: a, len: 2} : {index: b, len: 4};
}

async function streamTurn(text) {
  appendMessage("user", text);
  $("#compose-input").disabled = true;
  $("#compose-send").disabled = true;
  state.currentAssistantText = "";
  state.currentAssistantMessageId = null;
  let runFinished = false;
  let assistantDiv = null;

  try {
    await api("/test-ui/user-message", {
      method: "POST",
      body: JSON.stringify({
        reservation_id: state.scenario.reservation_id,
        message_header_id: state.scenario.message_header_id,
        text: text,
      }),
    });

    assistantDiv = appendMessage("assistant", "", {streaming: true});

    state.conversationHistory.push({role: "user", content: text});
    // Refresh `current_time` per turn so the system prompt always
    // reflects when *this* guest message was sent — without it the
    // model had to guess "today" and produced wrong year/month.
    const reservationCtx = Object.assign(
      {},
      state.scenario.reservation_context || {},
      {current_time: new Date().toISOString()},
    );
    const body = {
      run_id: crypto.randomUUID(),
      thread_id: state.scenario.thread_id,
      messages: state.conversationHistory.slice(),
      state: {
        reservation_id: state.scenario.reservation_id,
        property_id: state.scenario.property_id,
        message_header_id: state.scenario.message_header_id,
        // customer_id / org_id drive per-customer settings, tools, RAG scope.
        // Without them the pipeline runs an empty-customer agent with no KB.
        customer_id: state.scenario.customer_id || "",
        org_id: state.scenario.org_id || "",
        reservation_context: reservationCtx,
        test_harness: true,
      },
    };

    const resp = await fetch("/", {method: "POST", headers: apiHeaders(), body: JSON.stringify(body)});
    if (!resp.ok || !resp.body) {
      assistantDiv.classList.add("warn");
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, {stream: true});
      appendRaw(chunk);
      buf += chunk;
      let m;
      while ((m = nextFrameBoundary(buf)) !== null) {
        const frame = buf.slice(0, m.index);
        buf = buf.slice(m.index + m.len);
        const finished = handleFrame(frame, assistantDiv);
        if (finished) runFinished = true;
      }
    }
    const tail = buf.trim();
    if (tail) {
      const finished = handleFrame(tail, assistantDiv);
      if (finished) runFinished = true;
    }
    if (!runFinished && state.currentAssistantText) {
      persistAIReply(state.currentAssistantText, assistantDiv);
    }
  } finally {
    if (assistantDiv) assistantDiv.classList.remove("streaming");
    state.streaming = false;
    $("#compose-input").disabled = false;
    $("#compose-send").disabled = false;
  }
}

function handleFrame(frame, assistantDiv) {
  let eventType = null;
  let dataLine = null;
  const lines = frame.split(/\r?\n/);
  for (const line of lines) {
    if (line.indexOf("event: ") === 0) eventType = line.slice(7).trim();
    else if (line.indexOf("data: ") === 0) dataLine = line.slice(6);
  }
  if (!eventType || !dataLine) return false;
  let payload;
  try { payload = JSON.parse(dataLine); } catch (e) { payload = {raw: dataLine}; }
  const evt = Object.assign({type: eventType}, payload);

  appendTimeline(evt);

  if (eventType === "TEXT_MESSAGE_START") {
    state.currentAssistantText = "";
    state.currentAssistantMessageId = payload.message_id;
  } else if (eventType === "TEXT_MESSAGE_CONTENT") {
    state.currentAssistantText += payload.delta || "";
    assistantDiv.textContent = state.currentAssistantText;
  } else if (eventType === "TEXT_MESSAGE_END") {
    assistantDiv.textContent = state.currentAssistantText;
  } else if (eventType === "RUN_FINISHED") {
    persistAIReply(state.currentAssistantText, assistantDiv);
    return true;
  } else if (eventType === "RUN_ERROR") {
    assistantDiv.classList.add("warn");
  }
  return false;
}

async function persistAIReply(text, assistantDiv) {
  if (!text) return;
  state.conversationHistory.push({role: "assistant", content: text});
  try {
    await api("/test-ui/ai-reply", {
      method: "POST",
      body: JSON.stringify({
        reservation_id: state.scenario.reservation_id,
        message_header_id: state.scenario.message_header_id,
        text: text,
      }),
    });
  } catch (e) {
    assistantDiv.classList.add("warn");
    assistantDiv.title = "persistence failed: " + e.message;
  }
}

function initTabs() {
  document.querySelectorAll("nav.tabs button").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("nav.tabs button").forEach(b => {
        const selected = b === btn;
        b.classList.toggle("active", selected);
        b.setAttribute("aria-selected", selected ? "true" : "false");
        b.setAttribute("tabindex", selected ? "0" : "-1");
      });
      document.querySelectorAll(".tab-panel").forEach(p => {
        const active = p.dataset.panel === btn.dataset.tab;
        p.classList.toggle("hidden", !active);
        if (active) {
          p.removeAttribute("hidden");
        } else {
          p.setAttribute("hidden", "");
        }
      });
    });
  });
  $("#raw-copy").addEventListener("click", () => navigator.clipboard.writeText($("#raw-stream").textContent));
}

async function bootstrap() {
  initTabs();
  if (!state.apiKey && !localStorage.getItem("beui-api-key-prompted")) {
    const dlg = $("#api-key-dialog");
    dlg.showModal();
    dlg.addEventListener("close", () => setApiKey($("#api-key-input").value.trim()));
  } else {
    setApiKey(state.apiKey);
  }
  try { await loadProperties(); }
  catch (e) { $("#seed-status").textContent = "failed to load properties: " + e.message; }
  try { await loadChannels(); }
  catch (e) { /* non-fatal — leaves dropdown with only "(use property default)" */ }

  $("#seed-btn").addEventListener("click", seed);
  $("#reset-btn").addEventListener("click", resetScenario);
  $("#compose").addEventListener("submit", e => {
    e.preventDefault();
    const text = $("#compose-input").value.trim();
    if (!text || state.streaming) return;
    state.streaming = true;
    $("#compose-input").value = "";
    streamTurn(text).catch(err => {
      state.streaming = false;
      $("#compose-input").disabled = false;
      $("#compose-send").disabled = false;
      $("#seed-status").textContent = "stream failed: " + err.message;
    });
  });
}

bootstrap();
