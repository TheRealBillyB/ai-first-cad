/* Plan panel. Reviews blueprint sheets, answers questions, and approves.
   It installs itself. The rest of the workspace page is left alone. */
(function () {
  const ui = { id: null, version: null, plan: null, summaries: [], error: "", notice: "", editing: false };

  function esc(value) {
    return String(value ?? "").replace(/\p{Cf}/gu, "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  // The workspace page that requires a session puts it in this meta tag.
  // With no tag, this page sends no token. An empty header is refused once
  // that server is the one answering.
  function sessionToken() {
    const meta = document.querySelector('meta[name="aicad-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  function sessionHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    const token = sessionToken();
    if (token) headers["X-Aicad-Token"] = token;
    return headers;
  }

  async function planCommand(name, args) {
    const response = await fetch("/api/command", {
      method: "POST",
      headers: sessionHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name, arguments: args || {}, source: "ui" }),
    });
    const body = await response.json();
    if (!body.ok) throw new Error(body.error || "Command failed");
    return body.result;
  }

  function fileUrl(path) {
    const token = sessionToken();
    const suffix = token ? "&token=" + encodeURIComponent(token) : "";
    return "/api/file?path=" + encodeURIComponent(path) + suffix;
  }

  function page() {
    return document.getElementById("page-plan");
  }

  function install() {
    const nav = document.querySelector("header nav");
    if (nav && !document.getElementById("nav-plan")) {
      const button = document.createElement("button");
      button.type = "button";
      button.id = "nav-plan";
      button.textContent = "Plan";
      nav.appendChild(button);
      const root = document.createElement("div");
      root.id = "page-plan";
      root.className = "plan-page";
      root.hidden = true;
      root.innerHTML = `
        <aside class="plan-list">
          <h2>Plans</h2>
          <p class="plan-kicker">Before a solid</p>
          <div id="plan-rows"></div>
        </aside>
        <section class="plan-main" id="plan-main"></section>
        <aside class="plan-side" id="plan-side"></aside>`;
      document.body.appendChild(root);
      button.addEventListener("click", () => showPlan());
      document.querySelector("header").addEventListener("click", (event) => {
        const hit = event.target.closest("button");
        if (!hit || hit.id === "nav-plan") return;
        hidePlan();
      });
      root.addEventListener("focusin", (event) => {
        if (event.target.matches("textarea, input")) ui.editing = true;
      });
      root.addEventListener("focusout", () => {
        setTimeout(() => {
          const active = document.activeElement;
          ui.editing = !!(active && root.contains(active) && active.matches("textarea, input"));
        }, 0);
      });
      root.addEventListener("click", onClick);
      setInterval(() => {
        if (ui.editing) return;
        const mounted = document.getElementById("plan-body");
        if ((page() && !page().hidden) || mounted) refreshSummaries().catch(() => {});
      }, 2500);
    }
    refreshSummaries(true).catch(() => {});
  }

  function otherPages() {
    return Array.from(document.querySelectorAll("[id^='page-']")).filter((node) => node.id !== "page-plan");
  }

  function hidePlan() {
    const root = page();
    if (root) root.hidden = true;
    const button = document.getElementById("nav-plan");
    if (button) button.classList.remove("on");
    otherPages().forEach((node) => node.style.removeProperty("display"));
  }

  function showPlan() {
    if (!page()) return;
    // Those pages set display in their own CSS, which beats the hidden attribute.
    otherPages().forEach((node) => node.style.setProperty("display", "none", "important"));
    document.querySelectorAll("header nav button").forEach((node) => {
      node.classList.toggle("on", node.id === "nav-plan");
    });
    page().hidden = false;
    refreshSummaries(true).catch((error) => showError(error));
  }

  async function refreshSummaries(force) {
    const response = await fetch("/api/state", { headers: sessionHeaders() });
    const body = await response.json();
    if (!body.ok) throw new Error(body.error || "Could not read the project.");
    const summaries = body.result.plans || [];
    const previous = JSON.stringify(ui.summaries);
    ui.summaries = summaries;
    const readable = summaries.filter((item) => item.status !== "unreadable");
    if (!ui.id && readable.length) ui.id = readable[0].id;
    if (ui.id && !readable.some((item) => item.id === ui.id)) {
      ui.id = readable.length ? readable[0].id : null;
      ui.plan = null;
    }
    if (!ui.id) {
      const broken = summaries.filter((item) => item.status === "unreadable");
      if (broken.length) {
        ui.error = broken.map((item) => `${item.title}: ${item.error || "could not be read"}`).join(" ");
      }
    }
    const stamp = summaries.map((item) => item.id + item.updated_at + item.status).join("|");
    if (force || stamp !== ui.stamp || previous !== JSON.stringify(summaries)) {
      ui.stamp = stamp;
      if (ui.id) await loadPlan(ui.id);
      else render();
    } else {
      renderList();
    }
  }

  async function loadPlan(id) {
    ui.plan = await planCommand("get_plan", { name: id });
    ui.id = id;
    if (ui.version && !(ui.plan.revisions || []).some((item) => item.version === ui.version)) {
      ui.version = null;
    }
    render();
  }

  function selectedRevision() {
    if (!ui.plan) return null;
    const version = ui.version || ui.plan.current_revision;
    return (ui.plan.revisions || []).find((item) => item.version === version) || ui.plan.revision;
  }

  function render() {
    renderList();
    const main = document.getElementById("plan-main");
    const side = document.getElementById("plan-side");
    if (!main || !side) return;
    if (!ui.plan) {
      main.innerHTML = `
        <div class="plan-empty">
          <h2>No plan yet</h2>
          <p>A plan is the step before a solid. The assistant records research, draws a rough dimensioned sheet, and lists assumptions and open questions. You answer those questions here, then approve. The assistant cannot approve.</p>
          <p>The shelf example and the click-by-click test are in <code>docs/PLAN_AND_SKETCH.md</code>.</p>
          ${ui.error ? `<p class="plan-error">${esc(ui.error)}</p>` : ""}
        </div>`;
      side.innerHTML = "";
      return;
    }
    const plan = ui.plan;
    const revision = selectedRevision();
    const current = revision && revision.version === plan.current_revision;
    const sheets = (revision && revision.sheets) || [];
    const pictures = sheets.filter((sheet) => sheet.file || sheet.path).map((sheet) => `
      <figure class="plan-sheet">
        <img alt="${esc(sheet.title)}" src="${fileUrl(sheet.file || sheet.path)}" />
        <figcaption>${esc(sheet.title)} · v${revision.version}</figcaption>
      </figure>`).join("");
    const versions = (plan.revisions || []).map((item) => {
      const on = item.version === revision.version ? " on" : "";
      return `<button type="button" class="plan-version${on}" data-version="${item.version}">v${item.version}</button>`;
    }).join("");
    main.innerHTML = `
      <h2>${esc(plan.title)}</h2>
      <div class="plan-bar">
        <span class="plan-pill ${esc(plan.pill || plan.status)}">${esc(plan.status_text)}</span>
        <span>revision v${plan.current_revision}</span>
        <span class="plan-kicker" style="margin:0">${plan.modeling === "open" ? "modeling open" : "modeling closed"}</span>
      </div>
      <p>${esc(plan.summary || "")}</p>
      <div class="plan-banner ${plan.modeling === "open" ? "open" : "closed"}">${esc(plan.modeling_note)}</div>
      ${ui.notice ? `<div class="plan-banner">${esc(ui.notice)}</div>` : ""}
      ${ui.error ? `<p class="plan-error">${esc(ui.error)}</p>` : ""}
      <div class="plan-bar">${versions}</div>
      ${current ? "" : `<p class="plan-banner">You are looking at v${revision.version}. The current revision is v${plan.current_revision}. Answers and approval apply to the current revision.</p>`}
      <p><strong>What changed.</strong> ${esc(revision.changed || "")}</p>
      ${pictures || "<p>This revision has no sheet yet.</p>"}
      ${sheets.some((sheet) => sheet.error) ? `<p class="plan-error">${esc(sheets.map((sheet) => sheet.error).filter(Boolean).join(" "))}</p>` : ""}
      <h3>Activity</h3>
      <ul class="plan-activity">${(revision.activity || []).map((item) =>
        `<li>v${revision.version} · ${esc(item.actor)} · ${esc(item.action)}${item.item && item.item !== "plan" ? " · " + esc(item.item) : ""} — ${esc(item.text)}</li>`
      ).join("")}</ul>`;
    side.innerHTML = sideHtml(plan, revision, current);
  }

  function sideHtml(plan, revision, current) {
    const research = (revision.research || []).map((item) => `
      <article class="plan-card">
        <header><strong>${esc(item.title || item.id)}</strong><span>${esc(item.id)}</span></header>
        <p>${esc(item.note)}</p>
        <p class="plan-source">Source: ${esc(item.source)}</p>
      </article>`).join("");
    const assumptions = (revision.assumptions || []).map((item) => `
      <article class="plan-card">
        <header><strong>${esc(item.id)}</strong><span class="plan-pill">${esc(item.status)}</span></header>
        <p>${esc(item.text)}</p>
        ${item.comment ? `<p class="plan-source">Comment: ${esc(item.comment)}</p>` : ""}
        ${item.trust_note ? `<p class="plan-error">${esc(item.trust_note)}</p>` : ""}
        ${current && plan.status !== "approved" ? `
          <div class="plan-actions">
            <button type="button" data-mark="${esc(item.id)}" data-value="accepted">Accept</button>
            <button type="button" data-mark="${esc(item.id)}" data-value="rejected">Reject</button>
          </div>` : ""}
      </article>`).join("");
    const questions = (revision.questions || []).map((item) => `
      <article class="plan-card">
        <header><strong>${esc(item.id)}</strong><span class="plan-pill">${esc(item.status)}</span></header>
        <p>${esc(item.text)}</p>
        ${item.answer ? `<p>Answer: ${esc(item.answer)}</p>` : ""}
        ${item.trust_note ? `<p class="plan-error">${esc(item.trust_note)}</p>` : ""}
        ${current && plan.status !== "approved" && item.status !== "answered" ? `
          <textarea data-answer="${esc(item.id)}" placeholder="Your answer"></textarea>
          <div class="plan-actions">
            <button type="button" class="primary" data-send-answer="${esc(item.id)}">Answer</button>
            <button type="button" data-mark="${esc(item.id)}" data-value="waived">Waive</button>
          </div>` : ""}
      </article>`).join("");
    const dims = (revision.dimensions || []).map((item) =>
      `<tr><td>${esc(item.name)}</td><td>${esc(item.value_mm)} mm</td><td>${esc(item.note || "")}</td></tr>`
    ).join("");
    const actions = !current ? "" : `
      <div class="plan-actions">
        ${plan.status === "draft" ? `<button type="button" id="plan-submit" class="primary">Submit for review</button>` : ""}
        ${plan.status === "in_review" || plan.approval === "missing" ? `<button type="button" id="plan-approve" class="primary">Approve</button>` : ""}
        <button type="button" id="plan-try-model">Try modeling</button>
        <button type="button" id="plan-try-sim">Try simulation</button>
      </div>
      <textarea id="plan-comment" placeholder="Comment on this revision"></textarea>
      <div class="plan-actions"><button type="button" id="plan-send-comment">Comment</button></div>`;
    return `
      ${actions}
      <h3>Research</h3>
      ${research || "<p class='plan-source'>No research yet.</p>"}
      <h3>Assumptions</h3>
      ${assumptions || "<p class='plan-source'>No assumptions yet.</p>"}
      <h3>Open questions</h3>
      ${questions || "<p class='plan-source'>No questions yet.</p>"}
      <h3>Dimensions for the model</h3>
      ${dims ? `<table class="plan-dims">${dims}</table>` : "<p class='plan-source'>No named dimensions yet.</p>"}
      <p class="plan-source">Only you can answer, mark, and approve. The assistant cannot.</p>`;
  }

  function renderList() {
    const rows = document.getElementById("plan-rows");
    if (!rows) return;
    if (!ui.summaries.length) {
      rows.innerHTML = "<p class='plan-source'>None in this project.</p>";
      return;
    }
    rows.innerHTML = ui.summaries.map((item) => {
      if (item.status === "unreadable") {
        return `<div class="plan-row">
          <span>${esc(item.title)}</span>
          <span>unreadable</span>
          <p class="plan-error">${esc(item.error || "This file could not be read.")}</p>
        </div>`;
      }
      const mark = item.status_text ? ` · ${esc(item.status_text)}` : "";
      return `<button type="button" class="plan-row${item.id === ui.id ? " on" : ""}" data-plan="${esc(item.id)}">
        <span>${esc(item.title)}${mark}</span>
        <span>v${item.current_revision}</span>
      </button>`;
    }).join("");
    renderMount();
  }

  function renderMount() {
    const slot = document.getElementById("plan-body");
    if (!slot) return;
    const readable = ui.summaries.filter((item) => item.status !== "unreadable");
    const broken = ui.summaries.filter((item) => item.status === "unreadable");
    if (!ui.summaries.length) {
      slot.innerHTML = `<p class="muted">Nothing planned yet. A plan and a rough sketch will show here before a model starts.</p>`;
      return;
    }
    const current = readable.find((item) => item.id === ui.id) || readable[0];
    const lines = [];
    if (current) {
      const questions = current.open_questions
        ? `${current.open_questions} question${current.open_questions === 1 ? "" : "s"} still open`
        : "no open questions";
      lines.push(`<p><strong>${esc(current.title)}</strong></p>`);
      lines.push(`<p>${esc(current.status_text || current.status)} · v${current.current_revision} · ${questions}</p>`);
      lines.push(`<p>${current.modeling === "open"
        ? "Modeling may start from this revision."
        : "Modeling stays off until you approve."}</p>`);
    }
    if (readable.length > 1) {
      lines.push(`<p class="muted">${readable.length} plans in this project. The full review lists every one.</p>`);
    }
    broken.forEach((item) => {
      lines.push(`<p class="plan-error">${esc(item.title)} could not be read. ${esc(item.error || "")}</p>`);
    });
    lines.push(`<button type="button" id="plan-mount-open">Open the plan</button>`);
    slot.innerHTML = lines.join("");
    const opener = document.getElementById("plan-mount-open");
    if (opener) opener.addEventListener("click", () => showPlan());
  }

  function showError(error) {
    ui.error = error && error.message ? error.message : String(error);
    ui.notice = "";
    render();
  }

  async function onClick(event) {
    const planButton = event.target.closest("[data-plan]");
    if (planButton) {
      ui.error = "";
      ui.notice = "";
      ui.version = null;
      ui.id = planButton.getAttribute("data-plan");
      loadPlan(ui.id).catch(showError);
      return;
    }
    const versionButton = event.target.closest("[data-version]");
    if (versionButton) {
      ui.version = Number(versionButton.getAttribute("data-version"));
      render();
      return;
    }
    if (!ui.id) return;
    const mark = event.target.closest("[data-mark]");
    if (mark) {
      run(() => planCommand("mark_plan_item", {
        name: ui.id,
        item: mark.getAttribute("data-mark"),
        mark: mark.getAttribute("data-value"),
      }));
      return;
    }
    const send = event.target.closest("[data-send-answer]");
    if (send) {
      const id = send.getAttribute("data-send-answer");
      const box = page().querySelector(`[data-answer="${id}"]`);
      const text = box ? box.value.trim() : "";
      if (!text) {
        showError(new Error("Write an answer first."));
        return;
      }
      run(() => planCommand("answer_plan_question", { name: ui.id, item: id, text }));
      return;
    }
    if (event.target.id === "plan-submit") {
      run(() => planCommand("submit_plan", { name: ui.id }));
      return;
    }
    if (event.target.id === "plan-approve") {
      if (!window.confirm("Approve this revision? Modeling and simulation can start from it. A later revision clears that approval.")) return;
      run(() => planCommand("approve_plan", { name: ui.id }));
      return;
    }
    if (event.target.id === "plan-try-model") {
      run(() => planCommand("start_model_from_plan", { name: ui.id }), true);
      return;
    }
    if (event.target.id === "plan-try-sim") {
      run(() => planCommand("start_simulation_from_plan", { name: ui.id }), true);
      return;
    }
    if (event.target.id === "plan-send-comment") {
      const text = (document.getElementById("plan-comment").value || "").trim();
      if (!text) return;
      run(() => planCommand("comment_on_plan", { name: ui.id, item: "plan", text }));
    }
  }

  function run(action, asNotice) {
    ui.editing = false;
    ui.error = "";
    action().then((result) => {
      const dims = (result.dimensions || [])
        .map((item) => item.name + " " + item.value_mm + " mm")
        .join(", ");
      const note = result.note || (asNotice ? "Released." : "");
      ui.notice = dims ? note + " " + dims + "." : note;
      return loadPlan(ui.id);
    }).catch(showError);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
})();
