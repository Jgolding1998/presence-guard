"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const hints = {
    observe: "Show local presence and camera state. No desktop actions.",
    privacy: "Request a Windows lock after sustained absence, a stale camera, or an extra person. Detection is imperfect.",
    boss: "An extra person triggers a playful workbook titled Boss Alert. It contains synthetic demonstration data. This mode does not lock Windows."
  };
  let token = null;
  let state = null;
  let busy = false;
  let connected = false;
  let generation = 0;

  async function request(path, data) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 4000);
    const options = {cache: "no-store", credentials: "same-origin", redirect: "error", signal: controller.signal};
    if (data) {
      options.method = "POST";
      options.headers = {"Content-Type": "application/json"};
      options.body = JSON.stringify({...data, token});
    }
    let response, result;
    try {
      response = await fetch(path, options);
      result = await response.json();
    } finally {
      window.clearTimeout(timer);
    }
    if (response.status === 403) token = null;
    if (!response.ok) {
      const error = new Error("Local request rejected");
      error.publicMessage = response.status === 409 && typeof result.error === "string" ? result.error : "The local request was rejected. Refresh the page and check launch permissions.";
      throw error;
    }
    return result;
  }

  function describeMode() {
    const mode = $("mode").value;
    $("mode-hint").textContent = hints[mode];
    const note = $("arming-note");
    note.hidden = mode === "observe";
    if (!note.hidden) {
      note.textContent = state && state.demo
        ? "Simulation only. Starting this mode cannot lock Windows or open a workbook."
        : mode === "privacy"
          ? "Pressing Start explicitly arms real Windows lock requests for this session."
          : "Pressing Start explicitly permits opening a synthetic workbook in your associated spreadsheet app.";
    }
  }

  function render(next) {
    state = next;
    $("source-badge").textContent = state.demo ? "DEMO / NO CAMERA" : "LOCAL CAMERA";
    $("demo-controls").hidden = !state.demo;
    for (const option of $("mode").options) option.disabled = !state.allowed_modes.includes(option.value);
    if (!state.allowed_modes.includes($("mode").value) || state.active) $("mode").value = state.mode;
    $("mode").disabled = busy || !connected || state.active || state.camera_closing;
    $("start").disabled = busy || !connected || state.active || state.camera_closing;
    $("stop").disabled = busy || !connected || !(state.active || state.camera_closing || state.lock_armed || state.sheet_armed);
    $("safetybar").hidden = !(state.active || state.camera_closing);
    $("quick-stop").disabled = busy || !connected;
    $("session-label").textContent = !connected ? "Connection lost. State unknown." : state.camera_closing ? "Disarmed / camera closing" : state.mode_label + (state.demo ? " / simulation" : " / active");
    const pill = $("active-pill");
    pill.textContent = state.camera_closing ? "Closing" : state.active ? "Active" : "Inactive";
    pill.classList.toggle("active", state.active);
    $("mode-label").textContent = state.mode_label + (state.demo ? " / simulation" : "");
    const fault = state.active && state.camera === "fault";
    $("status-orbit").classList.toggle("on", state.active);
    $("status-orbit").classList.toggle("fault", fault);
    $("status-heading").textContent = state.camera_closing ? "Camera is closing." : !state.active ? "Ready when you are." : fault ? "Camera unavailable." : "Guard is watching.";
    $("status-detail").textContent = state.camera_closing ? "Actions are disarmed. If this persists, close the terminal before restarting." : !state.active ? "Select a mode, then press Start." : fault ? "People count is unknown. Privacy mode uses the camera fault rule." : state.demo ? "Synthetic observations only. No devices or desktop actions." : "Frames are processed locally, then discarded.";
    $("camera-state").textContent = state.camera_closing ? "Closing" : !state.active ? "Closed" : fault ? "Fault" : state.camera === "receiving" ? (state.demo ? "Synthetic" : "Receiving") : "Starting";
    $("people-count").textContent = state.people === null ? "Unknown" : String(state.people);
    $("permission-state").textContent = !state.active || state.mode === "observe" ? "Disarmed" : state.demo ? "Simulated" : state.mode === "privacy" ? "Lock armed" : "Workbook armed";
    $("absence-time").firstChild.textContent = fault ? "Unknown " : Number(state.absent_seconds).toFixed(1) + "s ";
    $("absence-progress").value = fault ? 0 : Math.min(30, state.absent_seconds);
    $("absence-note").textContent = fault ? "A camera fault is not proof of absence. The absence timer is not used while frames are stale." : !state.established ? "The absence timer starts only after at least one person is observed." : state.people === 0 ? "Fresh observations show no person. Re-entry resets this timer." : "Presence established. The absence timer is clear.";
    for (const button of document.querySelectorAll("[data-people]")) {
      button.setAttribute("aria-pressed", String(Number(button.dataset.people) === state.demo_people));
      button.disabled = busy || !connected;
    }
    $("fault").setAttribute("aria-pressed", String(state.demo_people === null));
    $("fault").disabled = busy || !connected;
    const action = $("last-action");
    action.hidden = !state.last_action;
    action.classList.toggle("error", Boolean(state.last_action && state.last_action.status === "failed"));
    if (state.last_action) action.textContent = state.last_action.message + ". " + state.last_action.reason + ".";
    const events = $("events");
    events.replaceChildren();
    for (const message of state.events.length ? state.events : ["No activity yet."]) {
      const item = document.createElement("li");
      item.textContent = message;
      events.append(item);
    }
    describeMode();
  }

  async function mutate(path, values) {
    if (busy || !token || !connected) return;
    busy = true;
    generation += 1;
    $("request-error").hidden = true;
    if (state) render(state);
    try {
      render(await request(path, values));
    } catch (error) {
      $("request-error").textContent = error.publicMessage || "The local request failed. The guard may still be running; reconnect or close its terminal.";
      $("request-error").hidden = false;
      if (!error.publicMessage) {
        connected = false;
        $("connection-error").hidden = false;
      }
    } finally {
      busy = false;
      if (state) render(state);
    }
  }

  $("mode").addEventListener("change", describeMode);
  $("start").addEventListener("click", () => mutate("/api/start", {mode: $("mode").value}));
  $("stop").addEventListener("click", () => mutate("/api/stop", {}));
  $("quick-stop").addEventListener("click", () => mutate("/api/stop", {}));
  for (const button of document.querySelectorAll("[data-people]")) {
    button.addEventListener("click", () => mutate("/api/demo", {people: Number(button.dataset.people)}));
  }
  $("fault").addEventListener("click", () => mutate("/api/demo", {people: state && state.demo_people === null ? 1 : null}));

  async function poll() {
    try {
      if (!token) token = (await request("/api/session")).token;
      const started = generation;
      if (!busy) {
        const next = await request("/api/status");
        connected = true;
        $("connection-error").hidden = true;
        if (!busy && started === generation) render(next);
      }
    } catch (_) {
      connected = false;
      $("connection-error").hidden = false;
      $("start").disabled = true;
      $("stop").disabled = true;
      $("quick-stop").disabled = true;
      $("mode").disabled = true;
      if (state) render(state);
    }
    window.setTimeout(poll, 700);
  }
  poll();
})();
