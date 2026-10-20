/* Vyuh web console. Plain JavaScript, no build step, no external libraries. */
(function () {
  "use strict";
  const C = window.VyuhCharts;
  const { esc, inr, short } = C;
  const app = document.getElementById("app");
  const S = { token: sessionStorage.getItem("vyuh_token"), user: JSON.parse(sessionStorage.getItem("vyuh_user") || "null"),
              timers: [], status: null, playbooks: [], aligned: true };

  // ------------------------------------------------------------------ helpers
  const fmtT = (t) => t ? new Date(t * 1000).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "–";
  const pct = (v) => (v * 100).toFixed(0) + "%";
  const can = (...roles) => S.user && roles.includes(S.user.role);
  function toast(msg) { const el = document.getElementById("toast"); el.textContent = msg; el.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove("show"), 3200); }
  function clearTimers() { S.timers.forEach(clearInterval); S.timers = []; }
  function every(ms, fn) { fn(); S.timers.push(setInterval(fn, ms)); }

  async function api(path, opts = {}) {
    const res = await fetch(path, { method: opts.method || "GET", headers: { "content-type": "application/json", ...(S.token ? { authorization: "Bearer " + S.token } : {}) },
                                    body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (res.status === 401 && path !== "/api/auth/login") { logout(); throw new Error("Session expired"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
    return data;
  }
  function logout() { sessionStorage.clear(); S.token = null; S.user = null; location.hash = "#/login"; render(); }

  // ------------------------------------------------------------------ shell
  const NAV = [
    ["ops", "Live operations"], ["board", "Campaign board"], ["phone", "Customer phone (demo)"],
    ["playbooks", "Playbooks"], ["report", "Evaluation"], ["monitor", "Model & monitoring"], ["audit", "Audit log"], ["help", "How it works"],
  ];
  function shell(active, inner) {
    const st = S.status;
    return `<div class="shell"><aside class="side">
      <div class="brand"><div class="logo">V</div><div><b>Vyuh</b><span>Scam campaign intelligence</span></div></div>
      <nav class="nav">${NAV.map(([k, l]) => `<a href="#/${k}" class="${active === k ? "active" : ""}">${esc(l)}${k === "board" && st ? `<span class="count">${st.cases_shown}</span>` : ""}</a>`).join("")}</nav>
      <div class="notice" style="margin-top:18px">All data is <b>synthetic</b> (VyuhSim). Scores are risk signals for human review, never accusations.</div>
    </aside><div class="main"><div class="top">
      <span class="pill"><span class="dot ${st && st.running ? "on" : ""}"></span>${st ? (st.running ? "Live" : "Paused") : "…"}</span>
      <span class="pill mono">${st ? esc(st.sim_time.replace("T", " ")) : ""}</span>
      ${st && st.adversary ? '<span class="badge b-emerging">Adversary adapting</span>' : ""}
      <span class="spacer"></span>
      <span class="small muted">${esc(S.user.username)} · ${esc(S.user.role)}</span>
      <button class="btn ghost" data-act="logout">Log out</button>
    </div><div class="content" id="view">${inner}</div></div></div>`;
  }
  async function refreshStatus() { try { S.status = await api("/api/status"); const top = document.querySelector(".top"); if (top) { const tmp = document.createElement("div"); tmp.innerHTML = shell(curRoute(), ""); top.replaceWith(tmp.querySelector(".top")); const nav = document.querySelector(".nav"); if (nav) nav.replaceWith(tmp.querySelector(".nav")); } } catch (e) { /* ignore */ } }

  // ------------------------------------------------------------------ login
  function viewLogin() {
    app.innerHTML = `<div class="login"><div class="card">
      <div class="brand"><div class="logo">V</div><div><b>Vyuh</b><span>Scam campaign intelligence</span></div></div>
      <form id="login"><label class="field">Username<input type="text" name="username" autocomplete="username" required></label>
      <label class="field">Password<input type="password" name="password" autocomplete="current-password" required></label>
      <button class="btn primary" type="submit">Sign in</button><div id="err" class="small" style="color:var(--bad)"></div></form>
      <p class="notice">Default users are <b>investigator</b>, <b>supervisor</b> and <b>auditor</b>. Passwords are set in <span class="mono">.env</span> (see the setup guide). Change them before sharing this system.</p>
    </div></div>`;
    document.getElementById("login").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.target);
      try {
        const r = await api("/api/auth/login", { method: "POST", body: { username: f.get("username"), password: f.get("password") } });
        S.token = r.token; S.user = { username: r.username, role: r.role };
        sessionStorage.setItem("vyuh_token", r.token); sessionStorage.setItem("vyuh_user", JSON.stringify(S.user));
        location.hash = "#/ops";
      } catch (e) { document.getElementById("err").textContent = e.message; }
    });
  }

  // ------------------------------------------------------------------ live ops
  async function viewOps() {
    if (!S.playbooks.length) { try { S.playbooks = (await api("/api/playbooks")).playbooks; } catch (e) { /* */ } }
    const opts = S.playbooks.map((p) => `<option value="${esc(p.name)}">${esc(p.title)}${p.known ? "" : " (unseen by models)"}</option>`).join("");
    app.innerHTML = shell("ops", `<div class="row"><h1>Live operations</h1><span class="spacer"></span></div>
      <div class="card" style="margin:12px 0"><div class="row">
        ${can("investigator", "supervisor") ? `<button class="btn primary" data-act="start">Start</button><button class="btn" data-act="pause">Pause</button>
        <label class="field">Speed<select id="speed"><option value="60">60× (1 sim-min/s)</option><option value="300">300× (5 min/s)</option><option value="1200">1200× (20 min/s)</option><option value="3600">3600× (1 h/s)</option></select></label>
        <button class="btn" data-act="step">Jump 1 hour</button><button class="btn" data-act="reset">Reset demo</button>
        <span class="spacer"></span>
        <label class="field">Scam playbook<select id="pb">${opts}</select></label>
        <label class="field">Victims<input type="number" id="nv" value="8" min="1" max="60" style="width:80px"></label>
        <button class="btn bad" data-act="inject">Start scam campaign</button>
        <button class="btn" data-act="adversary" id="advbtn">Scammers adapt</button>` : '<span class="muted">Read-only role: simulation controls hidden.</span>'}
      </div></div><div id="opsbody"></div>`);
    every(1500, renderOps);
  }
  async function renderOps() {
    await refreshStatus();
    const st = S.status; if (!st) return;
    const body = document.getElementById("opsbody"); if (!body) return;
    const sp = document.getElementById("speed"); if (sp && !sp.dataset.set) { sp.value = String(st.speed); sp.dataset.set = 1; }
    const ab = document.getElementById("advbtn"); if (ab) ab.textContent = st.adversary ? "Scammers adapt: ON" : "Scammers adapt";
    const t = st.today;
    body.innerHTML = `<div class="grid g4">
      <div class="card kpi bad"><div class="label">Alerts a typical rules engine would raise (since start)</div><div class="value">${st.baseline_alerts_total.toLocaleString("en-IN")}</div><div class="sub">today: ${t.baseline_alerts.toLocaleString("en-IN")}</div></div>
      <div class="card kpi accent"><div class="label">Vyuh investigator cases (open)</div><div class="value">${st.cases_open}</div><div class="sub">capacity ${st.capacity} · ${st.monitoring} in Monitoring · ${st.overflow} over capacity</div></div>
      <div class="card kpi good"><div class="label">Money protected (simulated)</div><div class="value">${inr(st.money.protected)}</div><div class="sub">${st.prevented_payments} scam payments stopped · lost ${inr(st.money.lost)}</div></div>
      <div class="card kpi"><div class="label">Customers protected live</div><div class="value">${st.interventions.R1 + st.interventions.R2 + st.interventions.R3}</div><div class="sub">R1 warn ${st.interventions.R1} · R2 pause ${st.interventions.R2} · R3 call-back ${st.interventions.R3}</div></div>
    </div>
    <div class="grid g3" style="margin-top:16px">
      <div class="card"><h2>Today's volume funnel (day ${t.day})</h2>${C.funnel([
        { label: "Payments", value: t.payments }, { label: "Scored by models", value: t.scored },
        { label: "High-risk payments (≥0.5)", value: t.high_risk }, { label: "Customer interventions", value: t.interventions },
        { label: "Open investigator cases", value: st.cases_open, accent: true }])}</div>
      <div class="card"><h2>Notifications</h2><div class="feed">${(st.notifications || []).slice().reverse().map((n) => `<div class="item">${n.kind === "error" ? `<b style="color:var(--bad)">${esc(n.text)}</b>` :
        `<b>${n.kind === "new_case" ? "New case" : "Case update"}</b> <a href="#/case/${esc(n.case_id)}">${esc(n.case_id)}</a> · ${n.victims} victims · ${inr(n.saveable)} saveable ${n.emerging ? '<span class="badge b-emerging">emerging</span>' : ""} <span class="muted small">${fmtT(n.sim_t)}</span>`}</div>`).join("") || '<div class="empty">No notifications yet. Start the replay and a scam campaign.</div>'}</div></div>
      <div class="card"><h2>System</h2><table>
        <tr><td>Events processed</td><td class="num">${st.events_processed.toLocaleString("en-IN")}</td></tr>
        <tr><td>Active risky journeys</td><td class="num">${st.active_journeys}</td></tr>
        <tr><td>Fast-path latency p50 / p95</td><td class="num">${st.latency_ms.p50} / ${st.latency_ms.p95} ms</td></tr>
        <tr><td>Legit customers paused / warned</td><td class="num">${st.legit_friction.paused} / ${st.legit_friction.warned}</td></tr>
        <tr><td>Events waiting in replay</td><td class="num">${st.queue.toLocaleString("en-IN")}</td></tr>
        <tr><td>Campaigns injected</td><td class="num">${(st.injected || []).map((i) => esc(i.campaign_id)).join(", ") || "–"}</td></tr>
      </table></div></div>`;
  }

  // ------------------------------------------------------------------ board
  function caseCard(c) {
    const title = c.emerging ? "Emerging pattern — matches no known playbook" : (c.playbook ? (S.playbooks.find((p) => p.name === c.playbook)?.title || c.playbook) : "Unmatched pattern");
    return `<div class="card case" data-go="#/case/${esc(c.case_id)}">
      <div><div class="row"><span class="mono">${esc(c.case_id)}</span><span class="badge b-${esc(c.status)}">${esc(c.status.replace("_", " "))}</span>
        ${c.kind !== "campaign" ? `<span class="badge b-${esc(c.kind)}">${c.kind === "lone" ? "single victim" : "shape cluster"}</span>` : ""}
        ${c.emerging ? '<span class="badge b-emerging">Emerging</span>' : ""}</div>
        <div class="title">${esc(title)}</div>
        <div class="metrics"><span><b>${c.victims}</b>victims (${c.live_victims} live)</span><span><b>${inr(c.saveable)}</b>still saveable</span>
          <span><b>${inr(c.paid)}</b>already paid</span><span><b>${c.collectors}</b>collector accts</span><span><b>${pct(c.confidence)}</b>confidence</span>
          <span><b>${c.growth >= 1.5 ? "↑ " : c.growth < 1 ? "↓ " : "→ "}${c.growth}×</b>growth (6 h)</span></div></div>
      <div class="prio"><div class="small muted">priority</div><b>${Math.round(c.priority).toLocaleString("en-IN")}</b><div class="small muted">${esc(c.evidence.join(" · "))}</div></div></div>`;
  }
  async function viewBoard() {
    if (!S.playbooks.length) { try { S.playbooks = (await api("/api/playbooks")).playbooks; } catch (e) { /* */ } }
    app.innerHTML = shell("board", `<div class="row"><h1>Campaign board</h1><span class="spacer"></span><span class="small muted">One card per scam operation · sorted by priority = saveable × confidence × growth</span></div><div id="boardbody"></div>`);
    every(2000, async () => {
      const b = await api("/api/board"); refreshStatus();
      const el = document.getElementById("boardbody"); if (!el) return;
      el.innerHTML = `<div class="cases" style="margin-top:12px">${b.cases.map(caseCard).join("") || '<div class="card empty">No open cases. That is good news — or start the replay.</div>'}</div>
        ${b.overflow.length ? `<div class="section-title"><h2>Over capacity (${b.overflow.length})</h2><span class="small muted">Lower priority than the top ${b.capacity}; shown so nothing is silently dropped.</span></div><div class="cases">${b.overflow.map(caseCard).join("")}</div>` : ""}
        <details><summary>Monitoring (${b.monitoring.length}) — linked journeys below case threshold, no notification</summary><div class="card scroll"><table><tr><th>ID</th><th>Members</th><th>Confidence</th><th>Evidence</th><th class="num">Saveable</th></tr>
        ${b.monitoring.map((m) => `<tr data-go="#/case/${esc(m.case_id)}" style="cursor:pointer"><td class="mono">${esc(m.case_id)}</td><td>${m.victims}</td><td>${pct(m.confidence)}</td><td>${esc(m.evidence.join(", ") || "–")}</td><td class="num">${inr(m.saveable)}</td></tr>`).join("")}</table></div></details>`;
    });
  }

  // ------------------------------------------------------------------ case detail
  async function viewCase(id) {
    app.innerHTML = shell("board", `<a href="#/board" class="small">← Board</a><div id="casebody"><div class="empty">Loading…</div></div>`);
    every(3000, () => renderCase(id));
  }
  async function renderCase(id) {
    let d; try { d = await api("/api/cases/" + encodeURIComponent(id)); } catch (e) { document.getElementById("casebody").innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
    const el = document.getElementById("casebody"); if (!el) return;
    const pbTitle = d.emerging ? "Emerging pattern (no known playbook)" : (S.playbooks.find((p) => p.name === d.playbook)?.title || d.playbook || "Unmatched");
    const open = ["new", "under_review", "confirmed"].includes(d.status);
    el.innerHTML = `<div class="row" style="margin:8px 0"><h1>${esc(d.case_id)} · ${esc(pbTitle)}</h1><span class="badge b-${esc(d.status)}">${esc(d.status.replace("_", " "))}</span>${d.emerging ? '<span class="badge b-emerging">Emerging</span>' : ""}</div>
      <div class="grid g4"><div class="card kpi"><div class="label">Victims (live)</div><div class="value">${d.victims} <span class="small muted">(${d.live_victims})</span></div></div>
        <div class="card kpi good"><div class="label">Still saveable</div><div class="value">${inr(d.saveable)}</div></div>
        <div class="card kpi bad"><div class="label">Already paid</div><div class="value">${inr(d.paid)}</div></div>
        <div class="card kpi accent"><div class="label">Confidence · growth</div><div class="value">${pct(d.confidence)} · ${d.growth}×</div></div></div>
      ${can("investigator", "supervisor") ? `<div class="card" style="margin-top:16px"><div class="row">
        <b>Decision</b>
        <button class="btn good" data-act="decide" data-d="confirm" ${d.status === "confirmed" ? "disabled" : ""}>Confirm scam campaign</button>
        <button class="btn" data-act="decide" data-d="monitor">Keep under review</button>
        <select id="reason"><option value="weak_evidence">Reject: weak evidence</option><option value="legitimate_business">Reject: legitimate business</option><option value="duplicate">Reject: duplicate</option><option value="other">Reject: other</option></select>
        <button class="btn" data-act="decide" data-d="reject">Reject</button>
        <button class="btn ghost" data-act="decide" data-d="close">Close</button>
        <span class="spacer"></span>
        <button class="btn" data-act="action" data-a="callback_live_victims">Call back live victims (R3)</button>
        <button class="btn" data-act="action" data-a="collector_review" ${d.status !== "confirmed" ? "disabled title='Confirm first'" : ""}>Send collectors for review (R4)</button>
        <button class="btn" data-act="action" data-a="draft_1930_report" ${d.status !== "confirmed" ? "disabled title='Confirm first'" : ""}>Draft 1930 report</button>
        ${can("supervisor") ? `<button class="btn" data-act="action" data-a="share_with_partners" ${d.status !== "confirmed" ? "disabled" : ""}>Share with partners (R5)</button>
          <button class="btn" data-act="export" ${d.status !== "confirmed" ? "disabled" : ""}>Export package</button>
          <button class="btn" data-act="approvepb" ${d.emerging ? "" : "disabled"}>Approve as new playbook</button>
          <button class="btn ghost" data-act="truth">Show simulator ground truth</button>` : ""}
      </div><div id="truth" class="small"></div></div>` : ""}
      <div class="grid g2" style="margin-top:16px">
        <div class="card"><h2>Why this case</h2><ul class="why">${d.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>
        <div class="card"><h2>Collector accounts</h2><div class="scroll"><table><tr><th>Account</th><th class="num">Score</th><th class="num">Age (d)</th><th class="num">Payers 24h</th><th class="num">Forwards in (min)</th><th class="num">Balance at risk</th></tr>
          ${d.collectors.map((c) => `<tr><td class="mono">${esc(short(c.account))}${c.business ? ' <span class="badge b-lone">business</span>' : ""}</td><td class="num">${c.score.toFixed(2)}</td><td class="num">${c.age_days ?? "–"}</td><td class="num">${c.payers_24h}</td><td class="num">${c.median_forward_min ?? "–"}</td><td class="num">${inr(c.balance_7d)}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">None identified</td></tr>'}</table></div>
          <p class="small muted">Collector accounts are "accounts of interest". Many mule holders are recruited victims; act per bank procedure only after confirmation.</p></div></div>
      <div class="card" style="margin-top:16px"><div class="row"><h2>Victim journeys ${S.aligned ? "(aligned to each victim's first step)" : "(real time)"}</h2><span class="spacer"></span>
        <button class="btn" data-act="align">${S.aligned ? "Show real time" : "Align journeys"}</button></div>
        <div class="legend"><span><i style="background:var(--s3)"></i>S3 trust (credit/refund)</span><span><i style="background:var(--s4)"></i>S4 control (call/screen/remote)</span><span><i style="background:var(--s5)"></i>S5 extraction</span><span><i style="background:var(--s7)"></i>S7 fee to withdraw</span><span>hollow = money received</span><span>✓ = protected</span></div>
        ${C.timelines(d.members, { aligned: S.aligned })}</div>
      <div class="card" style="margin-top:16px"><h2>Money flow</h2>${C.moneyFlow(d.graph)}</div>
      <div class="grid g2" style="margin-top:16px">
        <div class="card"><h2>Victims</h2><div class="scroll"><table><tr><th>Account</th><th class="num">Risk</th><th class="num">Payments</th><th class="num">Paid</th><th>Response</th><th>State</th></tr>
          ${d.members.map((m) => `<tr><td class="mono">${esc(short(m.token))}</td><td class="num">${(m.score ?? 0).toFixed(2)}</td><td class="num">${m.n_payments ?? "–"}</td><td class="num">${inr(m.paid || 0)}</td><td><span class="badge b-${esc(m.level || "R0")}">${esc(m.level || "R0")}</span></td><td class="small">${m.stopped ? "stopped / protected" : m.live ? "live" : "inactive"}</td></tr>`).join("")}</table></div></div>
        <div class="card"><h2>Case history</h2><div class="feed">${[...d.history.map((h) => ({ t: h.t, text: h.text, sim: true })), ...d.decisions.map((x) => ({ text: `${x.actor} (${x.role}): ${x.decision}${x.reason ? " – " + x.reason : ""}`, real: x.ts })), ...d.actions.map((a) => ({ text: `${a.actor}: ${a.action} – ${a.detail}`, real: a.ts }))]
          .map((h) => `<div class="item">${esc(h.text)} <span class="small muted">${h.sim ? fmtT(h.t) + " (sim)" : new Date(h.real * 1000).toLocaleTimeString()}</span></div>`).join("")}</div></div></div>`;
    el.dataset.caseId = id;
  }

  // ------------------------------------------------------------------ phone
  async function viewPhone() {
    app.innerHTML = shell("phone", `<div class="row"><h1>Customer phone (demo)</h1><span class="spacer"></span><span class="small muted">What the featured victim of the latest injected campaign sees in their banking app.</span></div><div id="phonebody" style="margin-top:12px"></div>`);
    every(1500, async () => {
      const p = await api("/api/phone"); refreshStatus();
      const el = document.getElementById("phonebody"); if (!el) return;
      if (!p.featured) { el.innerHTML = '<div class="card empty">Start a scam campaign on Live operations first. Its first victim appears here.</div>'; return; }
      const last = (p.interventions || [])[0];
      let alert = "";
      if (p.pending) {
        alert = `<div class="alert"><h3>Payment paused for 30 minutes</h3><p>${esc(p.pending.message)}</p>
          <p class="small"><b>Why:</b> ${esc((p.pending.reasons || []).slice(0, 3).map((r) => r.text).join("; "))}</p>
          <p class="small">Amount: <b>${inr(p.pending.amount)}</b> to a new payee.</p>
          <div class="row"><button class="btn bad" data-act="phone" data-c="cancel">Cancel payment</button><button class="btn" data-act="phone" data-c="continue">Continue anyway</button></div>
          <p class="small muted">Talk to the bank: tap to call · or report at 1930</p></div>`;
      } else if (p.stopped) {
        alert = `<div class="alert warn"><h3>You cancelled the payment</h3><p class="small">Thank you. The bank has been notified. If you were asked to pay to unlock money, it is a scam — report it at 1930.</p></div>`;
      } else if (last) {
        alert = `<div class="alert warn"><h3>Be careful</h3><p class="small">${esc(last.message)}</p></div>`;
      }
      el.innerHTML = `<div class="grid g2"><div class="phone"><div class="bar-top"><b>DemoBank UPI</b><div class="small">Account ${esc(short(p.featured))} · synthetic customer</div></div>${alert}
        ${(p.transactions || []).map((t) => `<div class="tx"><span>${t.kind === "credit" ? "Received from" : t.kind === "debit" ? "Paid to" : esc(t.kind)} <span class="mono">${esc(short(t.counterparty))}</span><br><span class="small muted">${fmtT(t.t)}</span></span><b style="color:${t.kind === "credit" ? "var(--good)" : "var(--text)"}">${t.kind === "credit" ? "+" : "−"}${inr(t.amount)}</b></div>`).join("")}</div>
        <div class="card"><h2>Interventions for this customer</h2><table><tr><th>When</th><th>Level</th><th class="num">Risk</th><th class="num">Amount</th><th>Outcome</th></tr>
        ${(p.interventions || []).map((i) => `<tr><td>${fmtT(i.sim_t)}</td><td><span class="badge b-${esc(i.level)}">${esc(i.level)}</span></td><td class="num">${(+i.risk).toFixed(2)}</td><td class="num">${inr(i.amount)}</td><td>${esc(i.outcome)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">None yet</td></tr>'}</table>
        <p class="small muted">R1 = specific warning · R2 = cooling-off pause · R3 = pause + bank call-back. The customer can always choose; anything stronger needs a human investigator.</p></div></div>`;
    });
  }

  // ------------------------------------------------------------------ playbooks
  async function viewPlaybooks() {
    const d = await api("/api/playbooks"); S.playbooks = d.playbooks;
    app.innerHTML = shell("playbooks", `<h1>Playbooks</h1><p class="muted">Each playbook is a scam script written from public advisories (I4C, MHA, RBI). The library holds the journey-shape centroid the models learned from confirmed victims.</p>
      <div class="card scroll"><table><tr><th>Playbook</th><th>Status</th><th>Learned shape</th><th>Customer warning</th></tr>
      ${d.playbooks.map((p) => `<tr><td><b>${esc(p.title)}</b><div class="mono small muted">${esc(p.name)}</div></td><td>${p.known ? '<span class="badge b-new">known</span>' : '<span class="badge b-emerging">held out</span>'}</td><td>${p.in_library ? p.library_size + " victims" : "not in library"}</td><td class="small">${esc(p.warning)}</td></tr>`).join("")}</table></div>
      <h2 class="section-title">Emerging candidates & approved additions</h2><div class="card scroll"><table><tr><th>Name</th><th>Status</th><th>From case</th><th>Approved by</th></tr>
      ${d.candidates.map((c) => `<tr><td>${esc(c.title)}</td><td><span class="badge b-${c.status === "library" ? "new" : "emerging"}">${esc(c.status)}</span></td><td><a href="#/case/${esc(c.case_id)}">${esc(c.case_id)}</a></td><td>${esc(c.approved_by || "–")}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">None yet</td></tr>'}</table></div>`);
  }

  // ------------------------------------------------------------------ evaluation report
  async function viewReport() {
    let r; try { r = await api("/api/report"); } catch (e) { app.innerHTML = shell("report", `<h1>Evaluation</h1><div class="card empty">${esc(e.message)}</div>`); return; }
    const sys = r.systems || {};
    const keys = Object.keys(sys);
    const rows = [["Investigator items per day", "items_per_day"], ["Precision of reviewed items @ budget", "precision_at_budget", "pct"], ["Victims protected before their last payment", "victim_recall", "pct"],
      ["Scam money stopped (if warned victims comply)", "money_protected_pct", "pct"], ["Legit payments interrupted (friction)", "friction_rate", "pct4"], ["Scam payments made before first protection (median)", "payments_before_protection"]];
    const fmt = (v, t) => v === null || v === undefined ? "–" : t === "pct" ? (v * 100).toFixed(1) + "%" : t === "pct4" ? (v * 100).toFixed(3) + "%" : (+v).toLocaleString("en-IN", { maximumFractionDigits: 1 });
    app.innerHTML = shell("report", `<h1>Evaluation on the test period</h1><p class="muted">${esc(r.description || "")}</p>
      <div class="card scroll"><table><tr><th>Metric</th>${keys.map((k) => `<th class="num">${esc(sys[k].label)}</th>`).join("")}</tr>
      ${rows.map(([l, k, t]) => `<tr><td>${esc(l)}</td>${keys.map((s) => `<td class="num">${fmt(sys[s][k], t)}</td>`).join("")}</tr>`).join("")}</table></div>
      <div class="grid g2" style="margin-top:16px"><div class="card"><h2>Campaign-level results (Vyuh)</h2><table>${Object.entries(r.campaigns || {}).map(([k, v]) => `<tr><td>${esc(k.replaceAll("_", " "))}</td><td class="num">${esc(typeof v === "number" ? (+v).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : JSON.stringify(v))}</td></tr>`).join("")}</table></div>
      <div class="card"><h2>Robustness & latency</h2><table>${Object.entries(r.robustness || {}).map(([k, v]) => `<tr><td>${esc(k.replaceAll("_", " "))}</td><td class="num">${esc(typeof v === "number" ? (+v).toLocaleString("en-IN", { maximumFractionDigits: 3 }) : JSON.stringify(v))}</td></tr>`).join("")}</table></div></div>
      <p class="small muted">Generated ${esc(r.generated_at || "")}. Synthetic data is cleaner than real data; treat absolute numbers as an upper bound and the comparisons as the evidence.</p>`);
  }

  // ------------------------------------------------------------------ monitoring
  async function viewMonitor() {
    const [m, dr] = await Promise.all([api("/api/model"), api("/api/monitoring/drift")]);
    const met = m.metadata.metrics || {};
    app.innerHTML = shell("monitor", `<h1>Model & monitoring</h1><div class="grid g2">
      <div class="card"><h2>Models</h2><table><tr><td>Version</td><td class="mono">${esc(m.metadata.version)}</td></tr><tr><td>Trained at</td><td>${esc(m.metadata.trained_at)}</td></tr>
        <tr><td>Training window</td><td class="small">${esc((m.metadata.training_window || []).join(" → "))}</td></tr>
        ${["journey", "fused_risk", "collector", "txn_baseline_b1"].map((k) => `<tr><td>${esc(k)} (validation)</td><td class="small">AUC ${met[k]?.auc ?? "–"} · PR-AUC ${met[k]?.pr_auc ?? "–"} · ${met[k]?.positives ?? "–"} positives</td></tr>`).join("")}
        <tr><td>Artifact integrity</td><td>${Object.entries(m.hashes_valid).map(([f, ok]) => `<div class="small">${ok ? "✔" : "✖"} ${esc(f)}</div>`).join("")}</td></tr></table>
        <p class="small muted">${esc(m.metadata.note || "")}</p></div>
      <div class="card"><h2>Drift (PSI, live vs training)</h2>${dr.status !== "ok" ? `<div class="empty">Collecting live samples (${dr.samples}/200)…</div>` :
        `<p>Overall: <b>${esc(dr.overall)}</b> (worst PSI ${dr.worst_psi}) over ${dr.samples} recent scored payments.</p><div class="scroll" style="max-height:360px"><table><tr><th>Feature</th><th class="num">PSI</th><th>State</th></tr>${dr.features.filter((f) => f.psi !== null).map((f) => `<tr><td class="mono">${esc(f.feature)}</td><td class="num">${f.psi}</td><td>${esc(f.state)}</td></tr>`).join("")}</table></div>`}
        <p class="small muted">PSI &lt; 0.1 stable · 0.1–0.25 watch · &gt; 0.25 investigate/retrain. Alarms go to the ML owner, not investigators.</p></div></div>
      <div class="card" style="margin-top:16px"><h2>Playbook library</h2><table><tr><th>Playbook</th><th class="num">Victims learned from</th><th class="num">Novelty threshold</th></tr>${Object.entries(m.library).map(([k, v]) => `<tr><td>${esc(v.title || k)}</td><td class="num">${v.victims ?? v.n}</td><td class="num">${v.threshold}</td></tr>`).join("")}</table></div>`);
  }

  // ------------------------------------------------------------------ audit
  async function viewAudit() {
    if (!can("supervisor", "auditor")) { app.innerHTML = shell("audit", `<h1>Audit log</h1><div class="card empty">Only supervisors and auditors can view the audit log.</div>`); return; }
    const a = await api("/api/audit");
    app.innerHTML = shell("audit", `<div class="row"><h1>Audit log</h1><span class="spacer"></span>${a.verify.valid ? `<span class="badge b-new">Hash chain verified · ${a.verify.entries} entries</span>` : `<span class="badge b-confirmed">Chain BROKEN at entry ${a.verify.broken_at}</span>`}</div>
      <p class="muted">Every login, case view, decision, action and export is recorded. Each entry includes the hash of the previous one, so any edit or deletion is detectable.</p>
      <div class="card scroll"><table><tr><th>Time</th><th>Actor</th><th>Role</th><th>Action</th><th>Target</th><th>Details</th></tr>
      ${a.entries.map((e) => `<tr><td class="small">${new Date(e.ts * 1000).toLocaleString()}</td><td>${esc(e.actor)}</td><td>${esc(e.role)}</td><td>${esc(e.action)}</td><td class="mono">${esc(e.target)}</td><td class="small mono">${esc(e.details).slice(0, 140)}</td></tr>`).join("")}</table></div>`);
  }

  // ------------------------------------------------------------------ help
  function viewHelp() {
    app.innerHTML = shell("help", `<h1>How Vyuh works</h1><div class="grid g2">
      <div class="card"><h2>The idea in one line</h2><p>AI lets scammers change every message and every account, but not the playbook. Vyuh follows each customer's journey through scam stages, links customers walking the same path into one campaign, and gives investigators one explained case per campaign.</p>
      <h3>Three kinds of evidence</h3><ol><li><b>Journey</b> – this customer moves through scam stages in order (trust credit → control → rising payments to new payees).</li><li><b>Destination</b> – the money goes to accounts behaving like collectors (many unrelated first-time payers, forwarded within minutes).</li><li><b>Crowd</b> – other strangers follow the same path or pay the same collector cluster right now.</li></ol></div>
      <div class="card"><h2>Responses (lightest first)</h2><table><tr><td><span class="badge b-R1">R1</span></td><td>Specific in-app warning naming the trick</td><td>automatic</td></tr><tr><td><span class="badge b-R2">R2</span></td><td>30-minute cooling-off pause on a payment to a new payee</td><td>automatic, within policy</td></tr><tr><td><span class="badge b-R3">R3</span></td><td>Bank call-back to the customer</td><td>system requests, human performs</td></tr><tr><td><b>R4</b></td><td>Collector accounts sent for review; 1930 report</td><td>investigator, after confirming</td></tr><tr><td><b>R5</b></td><td>Pseudonymised sharing with partner institutions</td><td>supervisor only</td></tr></table>
      <h3>Alert fatigue controls</h3><ul><li>One case per campaign; new evidence updates the case.</li><li>Cases need ≥3 victims, ≥2 evidence types and calibrated confidence ≥ threshold.</li><li>Board limited to analyst capacity; ≥₹5 lakh saveable always shows.</li><li>Investigator decisions adjust link weights; legit-business suppressions need a second person.</li></ul></div></div>`);
  }

  // ------------------------------------------------------------------ actions (event delegation; CSP-safe)
  document.addEventListener("click", async (ev) => {
    const go = ev.target.closest("[data-go]");
    if (go && !ev.target.closest("button")) { location.hash = go.dataset.go; return; }
    const el = ev.target.closest("[data-act]"); if (!el) return;
    const act = el.dataset.act;
    try {
      if (act === "logout") logout();
      else if (["start", "pause", "reset", "step"].includes(act)) {
        if (act === "reset" && !confirm("Reset the demo? Cases and interventions from this run are cleared (audit log is kept).")) return;
        const speed = +(document.getElementById("speed")?.value || 300);
        await api("/api/replay", { method: "POST", body: { action: act, speed, minutes: 60 } });
        toast(act === "reset" ? "Demo reset" : act === "start" ? "Replay running" : act === "step" ? "Jumped 1 hour" : "Paused");
      } else if (act === "inject") {
        const r = await api("/api/simulate/inject", { method: "POST", body: { playbook: document.getElementById("pb").value, victims: +document.getElementById("nv").value } });
        toast(`Campaign ${r.campaign_id} started with ${r.victims} victims. Its first victim is on the phone view.`);
      } else if (act === "adversary") {
        const r = await api("/api/simulate/adversary", { method: "POST", body: { on: !(S.status && S.status.adversary) } });
        toast(r.adversary ? "Scammers adapted: new collector accounts, randomised amounts, no tell-tale remarks." : "Adversary mode off");
      } else if (act === "decide") {
        const id = document.getElementById("casebody").dataset.caseId;
        const decision = el.dataset.d;
        const reason = decision === "reject" ? document.getElementById("reason").value : "";
        await api(`/api/cases/${encodeURIComponent(id)}/decision`, { method: "POST", body: { decision, reason } });
        toast(`Case ${id}: ${decision}`); renderCase(id);
      } else if (act === "action") {
        const id = document.getElementById("casebody").dataset.caseId;
        const r = await api(`/api/cases/${encodeURIComponent(id)}/actions`, { method: "POST", body: { action: el.dataset.a } });
        toast(r.detail); renderCase(id);
      } else if (act === "export") {
        const id = document.getElementById("casebody").dataset.caseId;
        const r = await api(`/api/cases/${encodeURIComponent(id)}/export`);
        const blob = new Blob([JSON.stringify(r, null, 2)], { type: "application/json" });
        const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${id}-package.json`; a.click();
      } else if (act === "approvepb") {
        const id = document.getElementById("casebody").dataset.caseId;
        const r = await api(`/api/playbooks/${encodeURIComponent(id)}/approve`, { method: "POST" });
        toast(`Added to library as ${r.name}`);
      } else if (act === "truth") {
        const id = document.getElementById("casebody").dataset.caseId;
        const r = await api(`/api/cases/${encodeURIComponent(id)}/truth`);
        document.getElementById("truth").innerHTML = `<p><b>Simulator ground truth</b> (not visible to the detector): ${esc(Object.entries(r.true_victims_by_campaign).map(([k, v]) => `${v} × ${k}`).join(", ") || "no true victims")}; ${r.members_not_victims} member(s) are not victims; ${r.collectors_confirmed}/${r.collectors_total} collectors are real mules.</p>`;
      } else if (act === "align") { S.aligned = !S.aligned; renderCase(document.getElementById("casebody").dataset.caseId); }
      else if (act === "phone") { await api("/api/phone/respond", { method: "POST", body: { choice: el.dataset.c } }); toast(el.dataset.c === "cancel" ? "Payment cancelled by customer" : "Customer continued"); }
    } catch (e) { toast(e.message); }
  });
  document.addEventListener("change", async (ev) => {
    if (ev.target.id === "speed") { try { await api("/api/replay", { method: "POST", body: { action: S.status?.running ? "start" : "pause", speed: +ev.target.value } }); toast("Speed " + ev.target.value + "×"); } catch (e) { toast(e.message); } }
  });

  // ------------------------------------------------------------------ router
  function curRoute() { return (location.hash.replace(/^#\//, "").split("/")[0]) || "ops"; }
  async function render() {
    clearTimers();
    if (!S.token) { viewLogin(); return; }
    const [route, arg] = location.hash.replace(/^#\//, "").split("/");
    try {
      if (!S.status) await refreshStatus();
      switch (route || "ops") {
        case "login": case "ops": await viewOps(); break;
        case "board": await viewBoard(); break;
        case "case": await viewCase(decodeURIComponent(arg || "")); break;
        case "phone": await viewPhone(); break;
        case "playbooks": await viewPlaybooks(); break;
        case "report": await viewReport(); break;
        case "monitor": await viewMonitor(); break;
        case "audit": await viewAudit(); break;
        case "help": viewHelp(); break;
        default: location.hash = "#/ops";
      }
    } catch (e) { app.innerHTML = shell(route, `<div class="card empty">${esc(e.message)}</div>`); }
  }
  window.addEventListener("hashchange", render);
  render();
})();
