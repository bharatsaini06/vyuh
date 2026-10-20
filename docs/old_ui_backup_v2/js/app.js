/* Vyuh portal. Plain JavaScript, no build step, no external libraries.
   CSP-safe: no inline scripts or inline event handlers; all clicks go through
   one delegated listener (data-act / data-go attributes). */
(function () {
  "use strict";
  const C = window.VyuhCharts;
  const { esc, inr, short } = C;
  const app = document.getElementById("app");
  const tourRoot = document.getElementById("tour-root");

  // Per-browser conveniences only (text size, contrast, guided-demo progress).
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage blocked */ } },
  };
  const S = {
    token: sessionStorage.getItem("vyuh_token"), user: JSON.parse(sessionStorage.getItem("vyuh_user") || "null"),
    timers: [], status: null, playbooks: [], aligned: true, filter: "all",
    tour: store.get("vyuh_tour", {}), tourOpen: store.get("vyuh_tour_open", false),
    fs: store.get("vyuh_fs", ""), contrast: store.get("vyuh_contrast", ""),
  };
  applyDisplayPrefs();

  // ------------------------------------------------------------------ helpers
  const fmtT = (t) => t ? new Date(t * 1000).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "–";
  const pct = (v) => (v * 100).toFixed(0) + "%";
  const num = (v) => Number(v || 0).toLocaleString("en-IN");
  const can = (...roles) => S.user && roles.includes(S.user.role);
  function toast(msg) { const el = document.getElementById("toast"); el.textContent = msg; el.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove("show"), 4200); }
  function clearTimers() { S.timers.forEach(clearInterval); S.timers = []; }
  function every(ms, fn) { const run = () => Promise.resolve(fn()).catch((e) => console.warn(e)); run(); S.timers.push(setInterval(run, ms)); }
  function applyDisplayPrefs() {
    const h = document.documentElement;
    h.classList.remove("fs-sm", "fs-lg", "fs-xl"); if (S.fs) h.classList.add(S.fs);
    if (S.contrast) h.dataset.contrast = S.contrast; else delete h.dataset.contrast;
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, { method: opts.method || "GET", headers: { "content-type": "application/json", ...(S.token ? { authorization: "Bearer " + S.token } : {}) },
                                    body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (res.status === 401 && path !== "/api/auth/login") { logout(); throw new Error("Your session has expired. Please sign in again."); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
    return data;
  }
  function logout() { sessionStorage.removeItem("vyuh_token"); sessionStorage.removeItem("vyuh_user"); S.token = null; S.user = null; S.status = null; location.hash = "#/login"; render(); }
  async function loadPlaybooks() { if (!S.playbooks.length) { try { S.playbooks = (await api("/api/playbooks")).playbooks; } catch (e) { /* shown elsewhere */ } } }
  const pbTitle = (name) => S.playbooks.find((p) => p.name === name)?.title || name;

  // ------------------------------------------------------------------ plain-language dictionaries
  const ROLE_INFO = {
    investigator: "Reviews scam cases, confirms or rejects them, calls back customers and drafts 1930 reports.",
    supervisor: "Everything an investigator can do, plus sharing with partner banks, exporting case packages and approving new scam types.",
    auditor: "Read-only. Can see everything, including the tamper-evident audit log, but cannot change anything.",
  };
  const STATUS_LABEL = { new: "New – needs review", under_review: "Under review", confirmed: "Confirmed scam", monitoring: "Watching quietly", rejected: "Rejected", closed: "Closed" };
  const LEVEL_LABEL = { R0: "No action", R1: "Warning shown", R2: "Payment paused", R3: "Paused + bank call-back" };
  const PB_INFO = {
    task_scam: "Victim is offered an easy online job (liking videos, rating hotels). Small payouts build trust, then they must pay growing ‘task fees’ to unlock earnings.",
    digital_arrest: "Callers pretend to be police, customs or a regulator on a video call, claim the victim is under ‘digital arrest’ and make them transfer money for ‘verification’.",
    investment_group: "A WhatsApp/Telegram ‘stock tips’ group shows fake profits in a fake app; the victim keeps investing and then pays ‘tax’ to withdraw.",
    kyc_remote_access: "A fake bank/KYC call asks the victim to install a screen-sharing app, then money is moved out while they watch.",
    collect_request: "A fake buyer sends a UPI ‘collect request’ and tells the seller to enter their PIN ‘to receive money’ – which actually sends money.",
    parcel_customs: "Caller says a parcel in the victim’s name is held at customs with illegal items and demands ‘clearance’ or ‘penalty’ fees.",
    digital_arrest_loan: "New variant the models never saw: the ‘digital arrest’ script plus forcing the victim to take an instant loan first. Used to show Vyuh spotting unknown scams.",
  };
  const STAGES = [
    ["S3", "Trust", "A small credit or ‘refund’ arrives from a stranger to build trust."],
    ["S4", "Control", "Calls from new numbers, screen-sharing or remote-access apps."],
    ["S5", "Extraction", "Rising payments to new, unknown receivers."],
    ["S6", "Cash-out", "Receiving accounts forward the money onward within minutes."],
    ["S7", "Re-victimisation", "A ‘fee to withdraw’ or ‘recovery agent’ asks for even more."],
  ];
  const GLOSSARY = [
    ["Campaign / case", "One scam operation (a gang running one script). Vyuh puts all its victims, receiving accounts and evidence into a single case."],
    ["Playbook / scam type", "The step-by-step script a scam follows, e.g. ‘part-time job → small payout → task fees’. Scammers can change words and accounts, but rarely the script."],
    ["Journey", "The sequence of a customer's recent activity – credits, calls, new payees, payments – read as a story instead of single transactions."],
    ["Collector (receiving) account", "An account that suddenly receives money from many unrelated first-time payers and quickly moves it on. Often a ‘mule’ – sometimes itself a recruited victim."],
    ["Emerging pattern", "A group of victims whose journeys match no known scam type. This is how Vyuh flags brand-new scams."],
    ["Confidence", "How sure the model is that the linked customers really are one scam campaign (0–100%). Calibrated, so 80% means right about 8 times in 10."],
    ["Saveable money", "Money that victims in this case are still likely to lose if nobody acts – the money a human can still protect."],
    ["Priority", "Saveable money × confidence × growth. The board is sorted by this, so the most urgent case is always on top."],
    ["Growth", "How fast the case is gaining victims in the last 6 hours (2× = doubled)."],
    ["R1 / R2 / R3", "Customer protections, lightest first: R1 specific warning, R2 30-minute cooling-off pause, R3 pause plus a bank call-back."],
    ["R4 / R5", "Human actions after confirmation: R4 receiving accounts sent for review and a 1930 report drafted; R5 sharing (pseudonymised) with partner banks – supervisors only."],
    ["Rules engine (B0)", "A typical fixed-rule fraud system (e.g. ‘new payee + large amount’). Used as the ‘old way’ for comparison."],
    ["Transaction ML (B1)", "A machine-learning model that scores each payment on its own. The usual modern approach, also used for comparison."],
    ["Drift (PSI)", "A check that today's data still looks like the data the model learned from. Above 0.25 means the model should be reviewed or retrained."],
    ["Audit log", "A record of every login, view, decision and export. Each entry is chained to the previous one by a hash, so any edit is detectable."],
    ["Pseudonymised", "Real account numbers are replaced with keyed codes (e.g. acct_3f9a…) so staff and models never see raw identities."],
  ];

  // ------------------------------------------------------------------ icons (simple, original line icons)
  const I = {
    home: '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    ops: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
    board: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M8 14h8"/>',
    phone: '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M11 18h2"/>',
    playbooks: '<path d="M4 4h10a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4z"/><path d="M8 8h6M8 12h6"/>',
    report: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    monitor: '<path d="M2 12h4l3-7 4 14 3-7h6"/>',
    audit: '<path d="M12 2l8 3v7c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V5z"/><path d="M9 12l2 2 4-4"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.7M12 17h.01"/>',
    play: '<path d="M7 4l13 8-13 8z"/>', pause: '<path d="M7 4h3v16H7zM14 4h3v16h-3z"/>',
    skip: '<path d="M5 4l10 8-10 8zM17 4h2v16h-2z"/>', reset: '<path d="M4 4v6h6"/><path d="M20 12a8 8 0 1 1-2.3-5.7L20 9"/>',
    bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>', check: '<path d="M4 12l5 5L20 6"/>', x: '<path d="M6 6l12 12M18 6L6 18"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>', flag: '<path d="M5 21V4h11l-2 4 2 4H5"/>', guide: '<path d="M12 2l3 7h7l-5.5 4.5 2 7.5L12 17l-6.5 4 2-7.5L2 9h7z"/>',
  };
  const ic = (k) => `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${I[k] || ""}</svg>`;
  const EMBLEM = `<svg class="emblem" viewBox="0 0 64 64" aria-hidden="true"><path d="M32 3l24 8v18c0 16-10.5 27-24 32C18.5 56 8 45 8 29V11z" fill="#0b2a55"/>
    <path d="M32 8l19 6.4V29c0 13-8.3 22-19 26.3C21.3 51 13 42 13 29V14.4z" fill="none" stroke="#ff9933" stroke-width="2.5"/>
    <path d="M21 22l11 20 11-20" fill="none" stroke="#fff" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="21" cy="22" r="3" fill="#138808"/><circle cx="43" cy="22" r="3" fill="#138808"/><circle cx="32" cy="42" r="3" fill="#ff9933"/></svg>`;

  // ------------------------------------------------------------------ shell (header, menu, footer)
  const NAV = [
    ["home", "Home"], ["ops", "Control room"], ["board", "Scam cases"], ["phone", "Customer phone"],
    ["playbooks", "Scam types"], ["report", "Results"], ["monitor", "Model health"], ["audit", "Audit log"], ["help", "Help & glossary"],
  ];
  function utilBar() {
    const fsBtn = (cls, label, title) => `<button data-act="fs" data-v="${cls}" class="${S.fs === cls ? "on" : ""}" title="${title}" aria-label="${title}">${label}</button>`;
    return `<div class="util"><div class="wrap">
      <span><b>Demonstration system</b> · synthetic data only · not a government or bank service</span><span class="spacer"></span>
      <span>Text size</span>${fsBtn("fs-sm", "A−", "Smaller text")}${fsBtn("", "A", "Normal text")}${fsBtn("fs-lg", "A+", "Larger text")}${fsBtn("fs-xl", "A++", "Largest text")}
      <span class="sep">|</span><button data-act="contrast" class="${S.contrast ? "on" : ""}" aria-pressed="${!!S.contrast}">High contrast</button>
    </div></div><div class="tricolour" aria-hidden="true"></div>`;
  }
  function masthead(withUser) {
    return `<header class="masthead"><div class="wrap">
      <a href="#/home" style="display:flex;gap:.8rem;align-items:center;text-decoration:none">${EMBLEM}
        <span class="titles"><b>VYUH <span class="hi">व्यूह</span></b><span class="sub">Scam Campaign Intelligence Portal · RAKSHAM AI Cybersecurity Hackathon (PS-2)</span></span></a>
      <span class="spacer"></span>
      ${withUser ? `<div class="status-box" id="hdr-status">${statusChips()}</div>
      <div class="user-box"><span class="muted">Signed in as</span><b>${esc(S.user.username)}</b></div>
      <button class="btn" data-act="logout">Sign out</button>` : ""}
    </div></header>`;
  }
  function statusChips() {
    const st = S.status;
    if (!st) return '<span class="chip">Connecting…</span>';
    return `<span class="chip" title="Whether the simulated bank is currently replaying payments"><span class="dot ${st.running ? "on" : ""}"></span>${st.running ? "Simulation running" : "Simulation paused"}</span>
      <span class="chip mono" title="Current time inside the simulation">${esc(st.sim_time.replace("T", " ").slice(0, 16))}</span>
      ${st.adversary ? '<span class="chip" style="border-color:var(--purple);color:var(--purple)">Scammers adapting</span>' : ""}`;
  }
  function footer() {
    return `<footer class="footer"><div class="wrap">
      <div><b>About this portal</b>Vyuh is a hackathon prototype that detects scam <i>campaigns</i> from customer payment journeys. All customers, accounts and money shown here are synthetic (generated by the VyuhSim simulator). Model outputs are risk signals for human review – never accusations.</div>
      <div><b>Facing a real cyber fraud?</b>Call the national cybercrime helpline <b>1930</b> or report at <a href="https://cybercrime.gov.in" target="_blank" rel="noopener">cybercrime.gov.in</a>. Never share your UPI PIN or OTP.</div>
      <div><b>Quick links</b><a href="#/help">Help & glossary</a><br><a href="#/report">Results</a><br><a href="/docs" target="_blank" rel="noopener">API documentation</a></div>
    </div><div class="bottom">Vyuh · RAKSHAM AI Cybersecurity Hackathon demonstration · Not affiliated with any government department or bank</div></footer>`;
  }
  function shell(active, inner) {
    const count = S.status ? S.status.cases_open : null;
    return `${utilBar()}${masthead(true)}
      <nav class="menubar" aria-label="Main menu"><div class="wrap">
        ${NAV.map(([k, l]) => `<a href="#/${k}" class="${active === k ? "active" : ""}" ${active === k ? 'aria-current="page"' : ""}>${ic(k)}${esc(l)}${k === "board" ? `<span class="count" id="nav-count"${count ? "" : ' style="display:none"'}>${count ?? ""}</span>` : ""}</a>`).join("")}
        <a href="#" class="tourbtn" data-act="tour-toggle">${ic("guide")}Guided demo</a>
      </div></nav>
      <main class="page" id="main" tabindex="-1">${inner}</main>${footer()}`;
  }
  function pageHead(title, lead, howto, crumbs) {
    return `${crumbs ? `<div class="crumbs"><a href="#/home">Home</a> › ${crumbs}</div>` : ""}
      <div class="page-head"><h1>${title}</h1><p class="lead">${lead}</p>
      ${howto ? `<details class="howto"><summary>How to use this page</summary>${howto}</details>` : ""}</div>`;
  }
  function kpi(color, label, value, sub, help) {
    return `<div class="card kpi ${color}"><button class="info-btn" data-act="info" aria-label="What does this mean?" title="What does this mean?">i</button>
      <div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div><div class="info-text">${help}</div></div>`;
  }
  async function refreshStatus() {
    try {
      S.status = await api("/api/status");
      const h = document.getElementById("hdr-status"); if (h) h.innerHTML = statusChips();
      const c = document.getElementById("nav-count");
      if (c) { c.textContent = S.status.cases_open; c.style.display = S.status.cases_open ? "" : "none"; }
      tourAutoCheck();
    } catch (e) { /* header keeps last value */ }
  }

  // ------------------------------------------------------------------ guided demo
  const TOUR = [
    { k: "start", t: "Start the simulation clock", go: "#/ops", btn: "Open Control room",
      p: "In the <b>Control room</b>, press <b>Start</b> in Step 1. A simulated bank with thousands of synthetic customers starts making normal UPI payments.",
      why: "Vyuh watches every payment in real time, exactly as it would inside a bank." },
    { k: "inject", t: "Launch a test scam", go: "#/ops", btn: "Open Control room",
      p: "In Step 2 of the Control room, pick a scam type (try <b>Part-time job / task scam</b>) and press <b>Launch scam campaign</b>.",
      why: "This plants a fake scam gang that targets several customers so you can watch Vyuh catch it." },
    { k: "phone", t: "See what the customer sees", go: "#/phone", btn: "Open Customer phone",
      p: "Open <b>Customer phone</b>. When a payment looks like part of a scam, it is paused and the customer sees a warning that names the trick. Try <b>Cancel payment</b>.",
      why: "Protection happens before the money leaves – not after the complaint." },
    { k: "case", t: "Wait for a scam case to appear", go: "#/board", btn: "Open Scam cases",
      p: "Open <b>Scam cases</b>. Within a few simulated hours Vyuh links the victims together into one case. (Use <b>Jump 1 hour</b> in the Control room to speed up.)",
      why: "An investigator gets one explained case per scam gang instead of thousands of alerts." },
    { k: "open", t: "Open the case and read the evidence", go: "#/board", btn: "Open Scam cases",
      p: "Click <b>Open case</b> on a card. Read <b>Why Vyuh flagged this</b>, the victim timelines and the money-flow map.",
      why: "Every case is explained in plain language so that a human makes the decision." },
    { k: "decide", t: "Confirm or reject the case", go: null,
      p: "On the case page, press <b>Confirm scam campaign</b> if the evidence convinces you (or <b>Reject</b> if not).",
      why: "No account is restricted and nobody is reported until a person confirms." },
    { k: "act", t: "Take action", go: null,
      p: "After confirming, use <b>Call back customers</b>, <b>Send receiving accounts for review</b> or <b>Draft 1930 report</b>.",
      why: "Every action is written to the tamper-evident audit log." },
    { k: "results", t: "Compare with existing systems", go: "#/report", btn: "Open Results",
      p: "Open <b>Results</b> to compare Vyuh with a rules engine and with transaction-level AI on a month of test data.",
      why: "This is the evidence: far fewer alerts with much higher precision, and unknown scams still caught." },
  ];
  function tourMark(k) { if (!S.tour[k]) { S.tour[k] = 1; store.set("vyuh_tour", S.tour); renderTour(); } }
  function tourAutoCheck() {
    const st = S.status; if (!st) return;
    if (st.running || st.events_processed > 0) tourMark("start");
    if ((st.injected || []).length) tourMark("inject");
    if (st.cases_open > 0) tourMark("case");
  }
  function renderTour() {
    if (!S.token) { tourRoot.innerHTML = ""; return; }
    const cur = TOUR.findIndex((s) => !S.tour[s.k]);
    const done = TOUR.filter((s) => S.tour[s.k]).length;
    tourRoot.innerHTML = `<aside class="tour ${S.tourOpen ? "open" : ""}" aria-label="Guided demo" ${S.tourOpen ? "" : "aria-hidden=\"true\""}>
      <header>${ic("guide")}<b>Guided demo</b><button data-act="tour-toggle" aria-label="Close guided demo">Close ✕</button></header>
      <div class="progress"><div class="row"><span><b>${done}</b> of ${TOUR.length} steps done</span><span class="spacer"></span><a href="#" data-act="tour-reset" class="small">Restart</a></div>
        <div class="bar" style="margin-top:.35rem"><i style="width:${(done / TOUR.length) * 100}%;background:var(--green)"></i></div>
        ${cur === -1 ? '<p class="notice ok" style="margin-top:.6rem"><b>All done!</b> You have seen the full Vyuh flow. Explore any page from the menu.</p>' : ""}</div>
      <ol>${TOUR.map((s, i) => `<li class="${S.tour[s.k] ? "done" : i === cur ? "current" : ""}">
        <div class="t"><span class="mark">${S.tour[s.k] ? "✓" : i + 1}</span>${esc(s.t)}</div>
        <p>${s.p}</p><div class="why-it"><b>Why it matters:</b> ${esc(s.why)}</div>
        <div class="row" style="margin-top:.5rem">${s.go ? `<a class="btn primary" href="${s.go}">${esc(s.btn)} ${ic("arrow")}</a>` : ""}
        <button class="btn" data-act="tour-done" data-k="${s.k}">Mark done</button></div></li>`).join("")}</ol></aside>`;
  }

  // ------------------------------------------------------------------ login
  function viewLogin() {
    app.innerHTML = `${utilBar()}${masthead(false)}<div class="menubar"><div class="wrap"><a class="active">Sign in</a></div></div>
      <main id="main" class="login-wrap" tabindex="-1">
        <section class="card"><h2 class="card-title">Welcome to Vyuh</h2>
          <p><b>Vyuh</b> (व्यूह, “battle formation”) helps a bank's fraud team spot <b>organised scam campaigns</b> – such as fake job offers, ‘digital arrest’ calls and fake investment groups – while the victims are still paying, and stop the money before it is lost.</p>
          <div class="notice info"><b>New here?</b> Sign in as <b>investigator</b>, then click <b>Guided demo</b> (top-right of the menu). It walks you through the full flow in about 5 minutes.</div>
          <h3 style="margin-top:1rem">Who can sign in</h3>
          <div class="role-list">${Object.entries(ROLE_INFO).map(([r, d]) => `<div><b class="mono">${r}</b> – ${esc(d)}</div>`).join("")}</div>
          <p class="small muted">Passwords are set in the <span class="mono">.env</span> file on the server (see the setup guide, section “Sign in”). Change them before sharing this system with anyone.</p>
        </section>
        <section class="card login-card"><h2 class="card-title">Sign in</h2>
          <form id="login">
            <label class="field">Role / username
              <select name="username" id="username"><option value="investigator">investigator – reviews cases</option><option value="supervisor">supervisor – reviews and shares</option><option value="auditor">auditor – read-only</option></select></label>
            <label class="field">Password<input type="password" name="password" autocomplete="current-password" required placeholder="Enter password from your .env file"></label>
            <button class="btn primary big" type="submit">Sign in ${ic("arrow")}</button>
            <div id="err" role="alert" class="small" style="color:var(--red);font-weight:600"></div>
          </form>
          <p class="small muted" style="margin-top:1rem">Sessions expire after 8 hours. Repeated wrong passwords temporarily lock the account.</p>
        </section>
      </main>${footer()}`;
    renderTour();
    document.getElementById("login").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.target);
      try {
        const r = await api("/api/auth/login", { method: "POST", body: { username: f.get("username"), password: f.get("password") } });
        S.token = r.token; S.user = { username: r.username, role: r.role };
        sessionStorage.setItem("vyuh_token", r.token); sessionStorage.setItem("vyuh_user", JSON.stringify(S.user));
        location.hash = "#/home";
      } catch (e) { document.getElementById("err").textContent = e.message; }
    });
  }

  // ------------------------------------------------------------------ home
  async function viewHome() {
    await loadPlaybooks();
    app.innerHTML = shell("home", `
      <section class="hero"><div><span class="tag">DEMONSTRATION · SYNTHETIC DATA</span>
        <h1 style="margin-top:.6rem">Catch the scam gang, not just one payment</h1>
        <p>Vyuh reads each customer's recent activity as a story, protects people while a scam is still happening, and gives investigators <b>one clear case per scam operation</b> instead of thousands of alerts.</p>
        <div class="row" style="margin-top:1rem"><button class="btn saffron big" data-act="tour-open">${ic("guide")} Start guided demo</button>
          <a class="btn big" href="#/ops" style="background:transparent;color:#fff;border-color:rgba(255,255,255,.5)">Go to Control room ${ic("arrow")}</a></div></div>
        <div class="quote">“AI lets scammers change every message and every account – but not the playbook. <b>Vyuh detects the playbook.</b>”</div></section>

      <h2 style="margin:1.5rem 0 .8rem">How Vyuh works – in 4 steps</h2>
      <div class="flow">
        <div class="card"><div class="big-no">1</div><h3>Watch the journey</h3><p class="small">Every payment is read together with what came before it: a stranger's small ‘refund’, a call from a new number, a screen-sharing app, rising payments to new people.</p></div>
        <div class="card"><div class="big-no">2</div><h3>Protect the customer</h3><p class="small">If the journey looks like a scam, the customer sees a warning that names the trick, or the payment is paused for 30 minutes. The customer can always choose.</p></div>
        <div class="card"><div class="big-no">3</div><h3>Link the victims</h3><p class="small">Customers paying the same receiving accounts, or following the same script, are joined into one <b>campaign</b> – even when every account and message is different.</p></div>
        <div class="card"><div class="big-no">4</div><h3>A human decides</h3><p class="small">The investigator sees one ranked, explained case, confirms or rejects it, and acts: call-backs, account review, 1930 report. Everything is audited.</p></div>
      </div>

      <h2 style="margin:1.5rem 0 .8rem">Right now</h2><div id="homebody"></div>

      <h2 style="margin:1.5rem 0 .8rem">Where do I go?</h2>
      <div class="grid g3 quick">
        <a class="card" href="#/ops"><b>${ic("ops")} Control room</b><span class="small muted">Start/pause the simulated bank, launch a test scam, see live numbers.</span></a>
        <a class="card" href="#/board"><b>${ic("board")} Scam cases</b><span class="small muted">The investigator's worklist – one card per scam operation, most urgent first.</span></a>
        <a class="card" href="#/phone"><b>${ic("phone")} Customer phone</b><span class="small muted">What a victim sees in their banking app when Vyuh steps in.</span></a>
        <a class="card" href="#/playbooks"><b>${ic("playbooks")} Scam types</b><span class="small muted">The scam scripts Vyuh knows, and new ones it has discovered.</span></a>
        <a class="card" href="#/report"><b>${ic("report")} Results</b><span class="small muted">How Vyuh compares with a rules engine and normal fraud AI.</span></a>
        <a class="card" href="#/help"><b>${ic("help")} Help & glossary</b><span class="small muted">Every term on this portal explained in plain words.</span></a>
      </div>`);
    every(2500, async () => {
      await refreshStatus();
      const st = S.status, el = document.getElementById("homebody"); if (!st || !el) return;
      const fresh = !st.events_processed;
      el.innerHTML = (fresh ? `<div class="next-step" style="margin-bottom:1rem"><b>Nothing is running yet.</b> Open the <a href="#/ops">Control room</a> and press <b>Start</b>, or follow the <a href="#" data-act="tour-open">Guided demo</a>.</div>` : "") +
        `<div class="grid g4">
          ${kpi("green", "Money saved (simulated)", inr(st.money.protected), `${num(st.prevented_payments)} scam payments stopped`, "Scam payments that did not happen because the customer was warned or paused and chose to stop.")}
          ${kpi("blue", "Customers protected", num(st.interventions.R1 + st.interventions.R2 + st.interventions.R3), "warnings, pauses and call-backs", "Customers who received a warning (R1), a 30-minute pause (R2) or a bank call-back request (R3).")}
          ${kpi("red", "Open scam cases", num(st.cases_open), `investigator capacity ${st.capacity}`, "Each case is one suspected scam operation. Open the Scam cases page to review them.")}
          ${kpi("saffron", "Payments checked", num(st.events_processed), `average check time ${st.latency_ms.p50} ms`, "Every event (payment, credit, call, login) is scored in about a millisecond, fast enough to sit inside a payment flow.")}
        </div>`;
    });
  }

  // ------------------------------------------------------------------ control room
  function pbDescHtml(name) {
    const p = S.playbooks.find((x) => x.name === name); if (!p) return "";
    return `<b>${esc(p.title)}</b>${p.known ? "" : ' <span class="badge b-emerging">unknown to the models</span>'}<br>${esc(PB_INFO[p.name] || "")}
      <div class="small muted" style="margin-top:.3rem"><b>Warning the customer would see:</b> ${esc(p.warning)}</div>`;
  }
  async function viewOps() {
    await loadPlaybooks();
    const opts = S.playbooks.map((p) => `<option value="${esc(p.name)}">${esc(p.title)}${p.known ? "" : " – NEW, unseen by models"}</option>`).join("");
    const controls = can("investigator", "supervisor") ? `
      <div class="grid g2" style="margin-bottom:1rem">
        <section class="card"><h2><span class="step-no">1</span>Run the simulated bank</h2>
          <p class="explain">The simulator replays payments from thousands of synthetic customers. Nothing happens until you press Start.</p>
          <div class="controls">
            <div class="control"><button class="btn green big" data-act="start">${ic("play")} Start</button><span class="btn-help">Begin replaying payments.</span></div>
            <div class="control"><button class="btn big" data-act="pause">${ic("pause")} Pause</button><span class="btn-help">Freeze the clock. Nothing is lost.</span></div>
            <div class="control"><label class="field">Speed<select id="speed"><option value="60">Slow – 1 sim-minute per second</option><option value="300">Normal – 5 sim-minutes per second</option><option value="1200">Fast – 20 sim-minutes per second</option><option value="3600">Very fast – 1 sim-hour per second</option></select></label></div>
            <div class="control"><button class="btn" data-act="step">${ic("skip")} Jump 1 hour</button><span class="btn-help">Skip ahead one simulated hour instantly.</span></div>
            <div class="control"><button class="btn" data-act="reset">${ic("reset")} Reset demo</button><span class="btn-help">Clear this run's cases and start again. The audit log is kept.</span></div>
          </div></section>
        <section class="card"><h2><span class="step-no">2</span>Launch a test scam</h2>
          <p class="explain">Plant a scam gang that targets some customers. Watch Vyuh warn them and build a case.</p>
          <div class="pb-grid">
            <label class="field">Type of scam<select id="pb">${opts}</select></label>
            <label class="field">Customers targeted<input type="number" id="nv" value="8" min="1" max="60"></label>
          </div>
          <div class="pb-desc" id="pbdesc" style="margin:.7rem 0">${pbDescHtml(S.playbooks[0]?.name)}</div>
          <div class="row"><button class="btn red big" data-act="inject">${ic("flag")} Launch scam campaign</button>
            <span class="spacer"></span>
            <div class="control"><button class="btn" data-act="adversary" id="advbtn">${ic("bolt")} Make scammers smarter: OFF</button><span class="btn-help">Optional stress test: new accounts, random amounts, no tell-tale words.</span></div></div>
        </section></div>`
      : `<div class="notice info" style="margin-bottom:1rem">You are signed in as <b>auditor</b> (read-only). The simulation controls are hidden; you can still watch everything below.</div>`;
    app.innerHTML = shell("ops", pageHead("Control room",
      "Run the demonstration from here. A simulated bank replays real-time payments; you can plant a scam and watch Vyuh react – warning victims, pausing payments and opening a case.",
      `<ol><li><b>Start</b> the simulated bank (Step 1).</li><li>Choose a scam type and press <b>Launch scam campaign</b> (Step 2).</li>
        <li>Watch the numbers below change. Open <a href="#/phone">Customer phone</a> to see the victim's screen.</li>
        <li>After a few simulated hours a case appears in <a href="#/board">Scam cases</a>. Use <b>Jump 1 hour</b> to get there faster.</li></ol>`, "Control room") +
      controls + `<div id="opsbody"><div class="card empty">Loading live numbers…</div></div>`);
    every(1500, renderOps);
  }
  async function renderOps() {
    await refreshStatus();
    const st = S.status; if (!st) return;
    const body = document.getElementById("opsbody"); if (!body) return;
    const sp = document.getElementById("speed"); if (sp && !sp.dataset.set) { sp.value = String(st.speed); sp.dataset.set = 1; }
    const ab = document.getElementById("advbtn"); if (ab) ab.innerHTML = `${ic("bolt")} Make scammers smarter: ${st.adversary ? "ON" : "OFF"}`;
    const t = st.today;
    const ratio = st.cases_open ? Math.round(st.baseline_alerts_total / st.cases_open) : null;
    body.innerHTML = `
      <section class="card" style="margin-bottom:1rem"><h2>Alerts vs cases – the core idea</h2>
        <div class="compare">
          <div class="side old"><div class="small"><b>A typical rules-based fraud system</b> would have raised</div><div class="big-num">${num(st.baseline_alerts_total)}</div><div class="small">separate payment alerts since the start</div></div>
          <div class="vs">vs</div>
          <div class="side new"><div class="small"><b>Vyuh</b> asks an investigator to review</div><div class="big-num">${num(st.cases_open)}</div><div class="small">case${st.cases_open === 1 ? "" : "s"} – one per scam operation</div></div>
        </div>
        <p class="center ratio" style="margin-top:.7rem">${ratio ? `That is about <b>${num(ratio)}×</b> fewer items for a human to check.` : "Start the simulation and launch a scam to see the difference."}</p></section>
      <div class="grid g4">
        ${kpi("green", "Money saved (simulated)", inr(st.money.protected), `${num(st.prevented_payments)} scam payments stopped · ${inr(st.money.lost)} already lost`, "Scam payments that did not happen because the customer was warned or paused and chose to stop. ‘Already lost’ is money victims paid before or despite protection.")}
        ${kpi("blue", "Customers protected live", num(st.interventions.R1 + st.interventions.R2 + st.interventions.R3), `${num(st.interventions.R1)} warned · ${num(st.interventions.R2)} paused · ${num(st.interventions.R3)} call-backs`, "R1 = a warning naming the scam trick. R2 = a 30-minute cooling-off pause. R3 = pause plus a request for a bank call-back.")}
        ${kpi("red", "Open scam cases", num(st.cases_open), `capacity ${st.capacity} · ${num(st.monitoring)} being watched quietly`, "Cases shown to investigators. ‘Watched quietly’ means linked customers that do not yet have enough evidence to bother a human.")}
        ${kpi("saffron", "Genuine customers disturbed", num(st.legit_friction.paused + st.legit_friction.warned), `${num(st.legit_friction.paused)} paused · ${num(st.legit_friction.warned)} warned`, "Normal customers who got a pause or warning by mistake. Lower is better – protecting victims must not annoy everyone else.")}
      </div>
      <div class="grid g3" style="margin-top:1rem">
        <section class="card"><h2>Today's activity (simulated day ${t.day})</h2><p class="explain">From all payments down to the few cases a human needs to see.</p>${C.funnel([
          { label: "Payments and events", value: t.payments }, { label: "Scored by the AI models", value: t.scored },
          { label: "Looked risky (score ≥ 0.5)", value: t.high_risk }, { label: "Customers warned or paused", value: t.interventions },
          { label: "Open cases for investigators", value: st.cases_open, accent: true }])}</section>
        <section class="card"><h2>Latest notifications</h2><p class="explain">New cases and important updates appear here.</p><div class="feed">${(st.notifications || []).slice().reverse().map((n) => `<div class="item">${n.kind === "error" ? `<b style="color:var(--red)">${esc(n.text)}</b>` :
          `<b>${n.kind === "new_case" ? "New scam case" : "Case updated"}</b> <a href="#/case/${esc(n.case_id)}">${esc(n.case_id)}</a>${n.emerging ? ' <span class="badge b-emerging">new scam type</span>' : ""}<br>${num(n.victims)} victims · ${inr(n.saveable)} still saveable <span class="muted small">· ${fmtT(n.sim_t)}</span>`}</div>`).join("") ||
          '<div class="empty"><b>No notifications yet</b>Start the simulation and launch a scam.</div>'}</div></section>
        <section class="card"><h2>System health</h2><table>
          <tr><td>Events processed</td><td class="num">${num(st.events_processed)}</td></tr>
          <tr><td>Customers currently on a risky path</td><td class="num">${num(st.active_journeys)}</td></tr>
          <tr><td title="p50 = typical, p95 = slowest 5%">Time to score one event (typical / slowest)</td><td class="num">${st.latency_ms.p50} / ${st.latency_ms.p95} ms</td></tr>
          <tr><td>Events waiting in the replay queue</td><td class="num">${num(st.queue)}</td></tr>
          <tr><td>Test scams launched</td><td class="num">${(st.injected || []).map((i) => esc(i.campaign_id)).join(", ") || "none yet"}</td></tr>
        </table></section></div>`;
  }

  // ------------------------------------------------------------------ board
  function caseTitle(c) { return c.emerging ? "New scam type – matches no known script" : (c.playbook ? pbTitle(c.playbook) : "Unclassified pattern"); }
  function caseCard(c) {
    const cls = c.emerging ? "emerging" : c.status === "confirmed" ? "confirmed" : "";
    const growth = c.growth >= 1.5 ? "growing fast" : c.growth < 1 ? "slowing" : "steady";
    return `<article class="card case ${cls}" data-go="#/case/${esc(c.case_id)}">
      <div><div class="row"><span class="mono">${esc(c.case_id)}</span><span class="badge b-${esc(c.status)}">${esc(STATUS_LABEL[c.status] || c.status)}</span>
        ${c.kind !== "campaign" ? `<span class="badge b-${esc(c.kind)}">${c.kind === "lone" ? "single victim" : "similar journeys"}</span>` : ""}
        ${c.emerging ? '<span class="badge b-emerging">Emerging</span>' : ""}</div>
        <div class="title">${esc(caseTitle(c))}</div>
        <div class="metrics"><span><b>${num(c.victims)}</b>victims (${num(c.live_victims)} still active)</span><span><b>${inr(c.saveable)}</b>still saveable</span>
          <span><b>${inr(c.paid)}</b>already paid</span><span><b>${num(c.collectors)}</b>receiving accounts</span><span><b>${pct(c.confidence)}</b>confidence</span>
          <span><b>${c.growth}×</b>${growth} (6 h)</span></div>
        <div class="small muted" style="margin-top:.35rem">Evidence: ${esc(c.evidence.join(" · ") || "–")}</div></div>
      <div class="prio"><div class="small muted">Priority score</div><b>${num(Math.round(c.priority))}</b>
        <div class="open"><button class="btn primary" data-go="#/case/${esc(c.case_id)}">Open case ${ic("arrow")}</button></div></div></article>`;
  }
  async function viewBoard() {
    await loadPlaybooks();
    app.innerHTML = shell("board", pageHead("Scam cases",
      "This is the investigator's worklist. Each card is <b>one suspected scam operation</b> with all its victims, receiving accounts and evidence. The most urgent case is always on top.",
      `<ul><li><b>Priority score</b> = money still saveable × confidence × how fast it is growing.</li>
        <li><b>New – needs review</b>: nobody has looked at it yet. Open it and decide.</li><li><b>Confirmed scam</b>: a person has confirmed it; actions are unlocked.</li>
        <li><b>Emerging</b> (purple): victims follow a script that matches none of the known scam types – possibly a brand-new scam.</li>
        <li>The board only shows as many cases as the team can handle (capacity). Lower-priority ones are listed under “Over capacity”, so nothing is hidden.</li></ul>`, "Scam cases") +
      `<div class="tabs" role="tablist">${[["all", "All open"], ["new", "Needs review"], ["under_review", "Under review"], ["confirmed", "Confirmed"], ["emerging", "New scam types"]]
        .map(([k, l]) => `<button role="tab" data-act="filter" data-f="${k}" class="${S.filter === k ? "on" : ""}" aria-selected="${S.filter === k}">${l}</button>`).join("")}</div>
      <div id="boardbody"><div class="card empty">Loading cases…</div></div>`);
    every(2000, async () => {
      const b = await api("/api/board"); refreshStatus();
      const el = document.getElementById("boardbody"); if (!el) return;
      const keep = (c) => S.filter === "all" || (S.filter === "emerging" ? c.emerging : c.status === S.filter);
      const cases = b.cases.filter(keep), over = b.overflow.filter(keep);
      const fresh = !(S.status && S.status.events_processed);
      el.innerHTML = `<div class="cases">${cases.map(caseCard).join("") || `<div class="card empty"><b>${S.filter === "all" ? "No open cases right now" : "No cases match this filter"}</b>${fresh ? 'The simulation has not started. Go to the <a href="#/ops">Control room</a>, press Start and launch a scam.' : "That is good news. If you just launched a scam, give it a few simulated hours (use Jump 1 hour in the Control room)."}</div>`}</div>
        ${over.length ? `<h2 style="margin:1.4rem 0 .4rem">Over capacity (${over.length})</h2><p class="explain">Lower priority than the top ${b.capacity}. Shown so that nothing is silently dropped.</p><div class="cases">${over.map(caseCard).join("")}</div>` : ""}
        <details style="margin-top:1rem"><summary>Being watched quietly (${b.monitoring.length}) – linked customers without enough evidence to alert a human yet</summary>
          <div class="card scroll"><table><tr><th>ID</th><th>Customers</th><th>Confidence</th><th>Evidence so far</th><th class="num">Saveable</th></tr>
          ${b.monitoring.map((m) => `<tr data-go="#/case/${esc(m.case_id)}" style="cursor:pointer"><td class="mono">${esc(m.case_id)}</td><td>${m.victims}</td><td>${pct(m.confidence)}</td><td>${esc(m.evidence.join(", ") || "–")}</td><td class="num">${inr(m.saveable)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">Nothing being watched.</td></tr>'}</table></div></details>`;
    });
  }

  // ------------------------------------------------------------------ case detail
  async function viewCase(id) {
    await loadPlaybooks();
    tourMark("open");
    app.innerHTML = shell("board", `<div class="crumbs"><a href="#/home">Home</a> › <a href="#/board">Scam cases</a> › ${esc(id)}</div><div id="casebody"><div class="card empty">Loading case…</div></div>`);
    every(3000, () => renderCase(id));
  }
  function nextStepBox(d) {
    if (!can("investigator", "supervisor")) return `<div class="notice info">You are signed in as <b>auditor</b>: you can read this case but cannot change it.</div>`;
    if (d.status === "new" || d.status === "monitoring") return `<div class="next-step"><b>What to do next:</b> read <i>Why Vyuh flagged this</i> and the timelines below. If convinced, press <b>Confirm scam campaign</b>. If not, choose a reason and <b>Reject</b>, or <b>Keep under review</b> to wait for more evidence.</div>`;
    if (d.status === "under_review") return `<div class="next-step"><b>What to do next:</b> this case is waiting for more evidence. Check back as new victims or payments arrive, then confirm or reject.</div>`;
    if (d.status === "confirmed") return `<div class="next-step"><b>Confirmed.</b> Now protect the people still at risk: <b>Call back customers</b> who are still paying, <b>Send receiving accounts for review</b>, and <b>Draft a 1930 report</b>.</div>`;
    return `<div class="notice">This case is <b>${esc(STATUS_LABEL[d.status] || d.status)}</b>. No further action is needed.</div>`;
  }
  async function renderCase(id) {
    let d; try { d = await api("/api/cases/" + encodeURIComponent(id)); } catch (e) { const el = document.getElementById("casebody"); if (el) el.innerHTML = `<div class="card empty"><b>Could not load this case</b>${esc(e.message)}</div>`; return; }
    const el = document.getElementById("casebody"); if (!el) return;
    const keepTruth = document.getElementById("truth")?.innerHTML || "";
    const conf = d.status === "confirmed";
    const act = (a, label, help, needConfirm, cls = "") => `<div class="control"><button class="btn ${cls}" data-act="action" data-a="${a}" ${needConfirm && !conf ? "disabled" : ""}>${label}</button><span class="btn-help">${help}${needConfirm && !conf ? " <i>(confirm the case first)</i>" : ""}</span></div>`;
    el.innerHTML = `<div class="page-head"><div class="row"><h1 style="margin:0">${esc(d.case_id)} · ${esc(d.emerging ? "New scam type (matches no known script)" : (d.playbook ? pbTitle(d.playbook) : "Unclassified pattern"))}</h1>
        <span class="badge b-${esc(d.status)}">${esc(STATUS_LABEL[d.status] || d.status)}</span>${d.emerging ? '<span class="badge b-emerging">Emerging</span>' : ""}</div>
        <p class="lead">One suspected scam operation. Everything Vyuh knows about it is on this page – victims, the accounts receiving the money, and the reasons it was flagged.</p></div>
      ${nextStepBox(d)}
      <div class="grid g4" style="margin-top:1rem">
        ${kpi("blue", "Victims", `${num(d.victims)} <span class="small muted">(${num(d.live_victims)} active)</span>`, "customers linked to this scam", "‘Active’ victims are still making payments that look like part of the scam – these are the people a call-back can still save.")}
        ${kpi("green", "Still saveable", inr(d.saveable), "money at risk if nobody acts", "An estimate of how much the active victims are still likely to pay the scammers.")}
        ${kpi("red", "Already paid", inr(d.paid), "sent to the scammers so far", "Total money linked victims have already sent to this campaign's receiving accounts.")}
        ${kpi("saffron", "Confidence · growth", `${pct(d.confidence)} · ${d.growth}×`, "how sure · how fast it spreads", "Confidence: how likely these customers really are one scam (calibrated). Growth: new victims in the last 6 hours compared with before.")}
      </div>
      ${can("investigator", "supervisor") ? `<div class="grid g2" style="margin-top:1rem">
        <section class="card"><h2><span class="step-no">1</span>Your decision</h2><p class="explain">Nothing happens to any account until a person confirms. Your decision also teaches Vyuh which links to trust.</p>
          <div class="controls">
            <div class="control"><button class="btn green" data-act="decide" data-d="confirm" ${conf ? "disabled" : ""}>${ic("check")} Confirm scam campaign</button><span class="btn-help">The evidence convinces you. Unlocks the actions.</span></div>
            <div class="control"><button class="btn" data-act="decide" data-d="monitor">Keep under review</button><span class="btn-help">Not sure yet – wait for more evidence.</span></div>
            <div class="control"><label class="field">Reason to reject<select id="reason"><option value="weak_evidence">Weak evidence</option><option value="legitimate_business">Legitimate business (needs a second person)</option><option value="duplicate">Duplicate of another case</option><option value="other">Other</option></select></label>
              <button class="btn red" data-act="decide" data-d="reject">${ic("x")} Reject</button></div>
            <div class="control"><button class="btn" data-act="decide" data-d="close">Close case</button><span class="btn-help">Finished – nothing more to do.</span></div>
          </div></section>
        <section class="card"><h2><span class="step-no">2</span>Actions</h2><p class="explain">Each action is written to the audit log with your name.</p>
          <div class="controls">
            ${act("callback_live_victims", "Call back customers (R3)", "Ask the bank to phone victims who are still paying.", false, "primary")}
            ${act("collector_review", "Send receiving accounts for review (R4)", "Flags the accounts collecting the money for the bank's review team.", true)}
            ${act("draft_1930_report", "Draft 1930 report", "Prepares a report for the national cybercrime helpline.", true)}
            ${can("supervisor") ? `${act("share_with_partners", "Share with partner banks (R5)", "Shares pseudonymised receiving accounts so other banks can protect their customers.", true)}
              <div class="control"><button class="btn" data-act="export" ${conf ? "" : "disabled"}>Download case package</button><span class="btn-help">JSON file with all evidence, for records or police.</span></div>
              <div class="control"><button class="btn" data-act="approvepb" ${d.emerging ? "" : "disabled"}>Add as new scam type</button><span class="btn-help">Only for Emerging cases: teach Vyuh this new script.</span></div>
              <div class="control"><button class="btn" data-act="truth">Show simulator answer key</button><span class="btn-help">Demo only: what really happened (the detector never sees this).</span></div>` : ""}
          </div><div id="truth" class="small" style="margin-top:.6rem">${keepTruth}</div></section></div>` : ""}
      <div class="grid g2" style="margin-top:1rem">
        <section class="card"><h2>Why Vyuh flagged this</h2><p class="explain">The evidence in plain language. Each line is something an investigator can check.</p><ul class="why">${d.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></section>
        <section class="card"><h2>Receiving (collector) accounts</h2><p class="explain">Accounts that collect money from many unrelated victims and move it on quickly. Many holders are themselves recruited victims – act only through bank procedure, after confirmation.</p>
          <div class="scroll"><table><tr><th>Account</th><th class="num" title="0–1, how much this account behaves like a collector">Collector score</th><th class="num">Account age (days)</th><th class="num">Payers in 24 h</th><th class="num" title="Typical minutes before received money is sent onward">Forwards money in (min)</th><th class="num">Money at risk</th></tr>
          ${d.collectors.map((c) => `<tr><td class="mono">${esc(short(c.account))}${c.business ? ' <span class="badge b-lone">business</span>' : ""}</td><td class="num">${c.score.toFixed(2)}</td><td class="num">${c.age_days ?? "–"}</td><td class="num">${c.payers_24h}</td><td class="num">${c.median_forward_min ?? "–"}</td><td class="num">${inr(c.balance_7d)}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">None identified yet.</td></tr>'}</table></div></section></div>
      <section class="card" style="margin-top:1rem"><div class="row"><h2 style="margin:0">Victim journeys ${S.aligned ? "– lined up at each victim's first step" : "– in real time"}</h2><span class="spacer"></span>
        <button class="btn" data-act="align">${S.aligned ? "Show in real time" : "Line up journeys"}</button></div>
        <p class="explain" style="margin-top:.5rem">Each row is one victim; each dot is an event, coloured by scam stage. When many strangers show the <b>same pattern of dots</b>, it is the same script – the same gang.</p>
        <div class="legend">${STAGES.map(([s, n]) => `<span><i style="background:var(--${s.toLowerCase()})"></i>${s} ${n}</span>`).join("")}<span>○ hollow = money received</span><span>✓ = customer protected</span></div>
        ${C.timelines(d.members, { aligned: S.aligned })}</section>
      <section class="card" style="margin-top:1rem"><h2>Where the money went</h2><p class="explain">Left: victims. Middle: accounts that received the money. Right: where it was forwarded. Thicker lines = more money.</p>${C.moneyFlow(d.graph)}</section>
      <div class="grid g2" style="margin-top:1rem">
        <section class="card"><h2>Victims in this case</h2><div class="scroll"><table><tr><th>Customer</th><th class="num" title="0–1 risk score">Risk</th><th class="num">Payments</th><th class="num">Paid</th><th>Protection given</th><th>State</th></tr>
          ${d.members.map((m) => `<tr><td class="mono">${esc(short(m.token))}</td><td class="num">${(m.score ?? 0).toFixed(2)}</td><td class="num">${m.n_payments ?? "–"}</td><td class="num">${inr(m.paid || 0)}</td><td><span class="badge b-${esc(m.level || "R0")}">${esc(LEVEL_LABEL[m.level || "R0"] || m.level)}</span></td><td class="small">${m.stopped ? "stopped / protected" : m.live ? "still active" : "inactive"}</td></tr>`).join("")}</table></div></section>
        <section class="card"><h2>Case history</h2><div class="feed">${[...d.history.map((h) => ({ t: h.t, text: h.text, sim: true })), ...d.decisions.map((x) => ({ text: `${x.actor} (${x.role}): ${x.decision}${x.reason ? " – " + x.reason : ""}`, real: x.ts })), ...d.actions.map((a) => ({ text: `${a.actor}: ${a.action} – ${a.detail}`, real: a.ts }))]
          .map((h) => `<div class="item">${esc(h.text)} <span class="small muted">· ${h.sim ? fmtT(h.t) + " (simulated time)" : new Date(h.real * 1000).toLocaleTimeString()}</span></div>`).join("") || '<div class="empty">No history yet.</div>'}</div></section></div>`;
    el.dataset.caseId = id;
  }

  // ------------------------------------------------------------------ customer phone
  async function viewPhone() {
    tourMark("phone");
    app.innerHTML = shell("phone", pageHead("Customer phone (demo)",
      "This shows what a victim of the latest test scam sees in their banking app. Vyuh does not block people – it explains the specific trick and gives them time to think.",
      `<ol><li>Launch a scam in the <a href="#/ops">Control room</a> first. Its first victim appears here.</li><li>Wait for a <b>Payment paused</b> message (the clock must be running).</li><li>Press <b>Cancel payment</b> (the customer listened) or <b>Continue anyway</b> (the customer insisted).</li></ol>`, "Customer phone") +
      `<div id="phonebody"><div class="card empty">Loading…</div></div>`);
    every(1500, async () => {
      const p = await api("/api/phone"); refreshStatus();
      const el = document.getElementById("phonebody"); if (!el) return;
      if (!p.featured) { el.innerHTML = '<div class="card empty"><b>No test scam running yet</b>Go to the <a href="#/ops">Control room</a>, press Start, and launch a scam campaign. Its first victim will appear here.</div>'; return; }
      const last = (p.interventions || [])[0];
      let alert = "";
      if (p.pending) {
        alert = `<div class="alert"><h3>⚠ Payment paused for 30 minutes</h3><p>${esc(p.pending.message)}</p>
          <p class="small"><b>Why:</b> ${esc((p.pending.reasons || []).slice(0, 3).map((r) => r.text).join("; "))}</p>
          <p class="small">Amount: <b>${inr(p.pending.amount)}</b> to a new payee.</p>
          <div class="row"><button class="btn red" data-act="phone" data-c="cancel">Cancel payment</button><button class="btn" data-act="phone" data-c="continue">Continue anyway</button></div>
          <p class="small" style="color:#556171">Not sure? Call your bank, or report fraud at 1930.</p></div>`;
      } else if (p.stopped) {
        alert = `<div class="alert warn"><h3>You cancelled the payment</h3><p class="small">Thank you. The bank has been notified. If someone asks you to pay to unlock money or to avoid arrest, it is a scam – report it at 1930.</p></div>`;
      } else if (last) {
        alert = `<div class="alert warn"><h3>Be careful</h3><p class="small">${esc(last.message)}</p></div>`;
      }
      el.innerHTML = `<div class="grid g2"><div class="phone"><div class="bar-top"><b>DemoBank UPI</b><div class="small">Account ${esc(short(p.featured))} · synthetic customer</div></div>${alert}
        ${(p.transactions || []).map((t) => `<div class="tx"><span>${t.kind === "credit" ? "Received from" : t.kind === "debit" ? "Paid to" : esc(t.kind)} <span class="mono">${esc(short(t.counterparty))}</span><br><span class="small" style="color:#556171">${fmtT(t.t)}</span></span><b style="color:${t.kind === "credit" ? "#138808" : "#1b2430"}">${t.kind === "credit" ? "+" : "−"}${inr(t.amount)}</b></div>`).join("")}</div>
        <div><section class="card"><h2>What is happening?</h2>
          <p class="small">The phone on the left belongs to a synthetic customer being targeted by the test scam. As their journey moves through the scam stages, Vyuh's response gets stronger:</p>
          <table><tr><td><span class="badge b-R1">R1</span></td><td>A warning that names the exact trick (e.g. “No real job asks you to pay to unlock earnings”).</td></tr>
          <tr><td><span class="badge b-R2">R2</span></td><td>A 30-minute cooling-off pause on a payment to a new payee. The customer can still continue.</td></tr>
          <tr><td><span class="badge b-R3">R3</span></td><td>Pause plus a request for the bank to call the customer.</td></tr></table></section>
        <section class="card" style="margin-top:1rem"><h2>Protections given to this customer</h2><div class="scroll"><table><tr><th>When</th><th>Protection</th><th class="num">Risk</th><th class="num">Amount</th><th>Outcome</th></tr>
        ${(p.interventions || []).map((i) => `<tr><td>${fmtT(i.sim_t)}</td><td><span class="badge b-${esc(i.level)}">${esc(LEVEL_LABEL[i.level] || i.level)}</span></td><td class="num">${(+i.risk).toFixed(2)}</td><td class="num">${inr(i.amount)}</td><td>${esc(i.outcome)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">None yet – keep the simulation running.</td></tr>'}</table></div></section></div></div>`;
    });
  }

  // ------------------------------------------------------------------ scam types
  async function viewPlaybooks() {
    const d = await api("/api/playbooks"); S.playbooks = d.playbooks;
    app.innerHTML = shell("playbooks", pageHead("Scam types (playbooks)",
      "A playbook is the step-by-step script a scam follows. These were written from public advisories (I4C, MHA, RBI). Scammers can change messages and accounts, but the script stays the same – that is what Vyuh recognises.",
      `<ul><li><b>Known</b>: the models learned this scam type from past victims.</li><li><b>Held out</b>: deliberately hidden from the models, to prove Vyuh can flag a scam it has never seen.</li>
        <li>When Vyuh finds a new script, it appears under <b>Newly discovered scam types</b>. A supervisor can add it to the library from the case page.</li></ul>`, "Scam types") +
      `<div class="grid g2">${d.playbooks.map((p) => `<section class="card"><div class="row"><h2 style="margin:0">${esc(p.title)}</h2><span class="spacer"></span>${p.known ? '<span class="badge b-new">known</span>' : '<span class="badge b-emerging">held out – unseen</span>'}</div>
        <p style="margin-top:.6rem">${esc(PB_INFO[p.name] || "")}</p>
        <div class="notice warn small"><b>Warning shown to customers:</b> ${esc(p.warning)}</div>
        <p class="small muted" style="margin:.5rem 0 0">${p.in_library ? `Learned from ${num(p.library_size)} past victims.` : "Not in the learned library."} · id <span class="mono">${esc(p.name)}</span></p></section>`).join("")}</div>
      <h2 style="margin:1.5rem 0 .6rem">Newly discovered scam types</h2>
      <div class="card scroll"><table><tr><th>Name</th><th>Status</th><th>Found in case</th><th>Approved by</th></tr>
      ${d.candidates.map((c) => `<tr><td>${esc(c.title)}</td><td><span class="badge b-${c.status === "library" ? "new" : "emerging"}">${esc(c.status === "library" ? "added to library" : c.status)}</span></td><td><a href="#/case/${esc(c.case_id)}">${esc(c.case_id)}</a></td><td>${esc(c.approved_by || "–")}</td></tr>`).join("") ||
        '<tr><td colspan="4" class="muted">None yet. Launch the “Digital arrest + instant loan” scam in the Control room to see one discovered.</td></tr>'}</table></div>`);
  }

  // ------------------------------------------------------------------ results
  const KEY_LABEL = {
    ground_truth_campaigns: "Scam campaigns in the test month", detected: "Campaigns detected", detected_early: "Detected while victims were still paying",
    median_victim_rank_at_detection: "Victims already hit when detected (median)", investigator_cases: "Cases shown to investigators", mean_case_purity: "Case purity (share of members who are real victims)",
    mean_case_homogeneity: "Case homogeneity (same scam type)", cases_with_any_victim_from_another_playbook: "Cases mixing two scam types", cases_merging_same_playbook_runs: "Cases merging two runs of the same scam",
    alert_reduction_vs_B0: "Fewer items to review than the rules engine (×)", emerging_playbook_hours_to_flag: "Hours to flag the unseen scam type",
    vyuh_money_protected_clean: "Vyuh – money protected (normal)", vyuh_money_protected_adversarial: "Vyuh – money protected (scammers adapting)",
    vyuh_victim_recall_clean: "Vyuh – victims protected (normal)", vyuh_victim_recall_adversarial: "Vyuh – victims protected (scammers adapting)",
    b0_money_protected_adversarial: "Rules engine – money protected (adapting)", b1_money_protected_adversarial: "Transaction ML – money protected (adapting)",
    b1_at_vyuh_friction_money_protected_adversarial: "Transaction ML at Vyuh's friction – money protected (adapting)", campaigns_detected_clean: "Campaigns detected (normal)",
    campaigns_detected_adversarial: "Campaigns detected (scammers adapting)", latency_p50_ms: "Scoring time, typical (ms)", latency_p95_ms: "Scoring time, slowest 5% (ms)",
  };
  const fmtVal = (k, v) => {
    if (v && typeof v === "object") return Object.entries(v).map(([a, b]) => `${esc(a)}: ${esc(b)} h`).join("<br>");
    if (typeof v !== "number") return esc(v);
    if (/money_protected|recall|purity|homogeneity/.test(k)) return (v * 100).toFixed(1) + "%";
    return v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  };
  async function viewReport() {
    tourMark("results");
    let r; try { r = await api("/api/report"); } catch (e) { app.innerHTML = shell("report", pageHead("Results", "Evaluation report") + `<div class="card empty"><b>No evaluation report found</b>${esc(e.message)}. Run <span class="mono">python -m scripts.prepare</span> (see the setup guide).</div>`); return; }
    const sys = r.systems || {}, keys = Object.keys(sys);
    const rows = [["Items an investigator must review per day", "items_per_day", "", "Lower is better – this is alert fatigue."],
      ["Of reviewed items, how many were real scams", "precision_at_budget", "pct", "Higher is better."],
      ["Victims protected before their last payment", "victim_recall", "pct", "Higher is better."],
      ["Scam money stopped (if warned customers listen)", "money_protected_pct", "pct", "Higher is better."],
      ["Genuine payments interrupted", "friction_rate", "pct4", "Lower is better – annoying honest customers has a cost."],
      ["Scam payments made before first protection (median)", "payments_before_protection", "", "Lower is better."]];
    const fmt = (v, t) => v === null || v === undefined ? "–" : t === "pct" ? (v * 100).toFixed(1) + "%" : t === "pct4" ? (v * 100).toFixed(3) + "%" : (+v).toLocaleString("en-IN", { maximumFractionDigits: 1 });
    const b0 = sys.B0, vy = sys.Vyuh;
    app.innerHTML = shell("report", pageHead("Results",
      "How Vyuh compares with the two common approaches – a fixed-rule fraud engine and a machine-learning model that scores each payment on its own – on a month of test data that includes a scam type the models never saw.",
      `<ul><li><b>B0 rules engine</b>: typical fixed rules such as ‘new payee + large amount’.</li><li><b>B1 transaction ML</b>: an AI model that looks at one payment at a time.</li>
        <li><b>B1 tuned to Vyuh's friction</b>: the same AI, tuned to disturb genuine customers as rarely as Vyuh does – a fair, like-for-like comparison.</li>
        <li>All data is synthetic, so treat absolute numbers as an upper bound; the <i>comparison</i> is the evidence.</li></ul>`, "Results") +
      (b0 && vy ? `<section class="card" style="margin-bottom:1rem"><h2>The headline</h2><div class="compare">
        <div class="side old"><div class="small"><b>Rules engine</b> – items to review per day</div><div class="big-num">${fmt(b0.items_per_day)}</div><div class="small">${fmt(b0.precision_at_budget, "pct")} of them real scams</div></div>
        <div class="vs">vs</div>
        <div class="side new"><div class="small"><b>Vyuh</b> – cases to review per day</div><div class="big-num">${fmt(vy.items_per_day)}</div><div class="small">${fmt(vy.precision_at_budget, "pct")} of them real scams</div></div></div>
        <p class="center ratio" style="margin-top:.7rem">Similar scam money protected (${fmt(b0.money_protected_pct, "pct")} vs ${fmt(vy.money_protected_pct, "pct")}) with a tiny fraction of the workload and far fewer genuine customers disturbed.</p></section>` : "") +
      `<section class="card scroll"><h2>Side-by-side comparison</h2><table><tr><th>Measure</th>${keys.map((k) => `<th class="num">${esc(sys[k].label)}</th>`).join("")}<th>Better is</th></tr>
      ${rows.map(([l, k, t, h]) => `<tr><td>${esc(l)}</td>${keys.map((s) => `<td class="num" ${s === "Vyuh" ? 'style="font-weight:800;background:var(--green-soft)"' : ""}>${fmt(sys[s][k], t)}</td>`).join("")}<td class="small muted">${esc(h)}</td></tr>`).join("")}</table></section>
      <div class="grid g2" style="margin-top:1rem"><section class="card"><h2>Campaign-level results (Vyuh)</h2><table>${Object.entries(r.campaigns || {}).map(([k, v]) => `<tr><td>${esc(KEY_LABEL[k] || k.replaceAll("_", " "))}</td><td class="num">${fmtVal(k, v)}</td></tr>`).join("")}</table></section>
      <section class="card"><h2>When scammers adapt, and speed</h2><p class="explain">“Scammers adapting” = new accounts, random amounts, no tell-tale words – with no retraining.</p><table>${Object.entries(r.robustness || {}).map(([k, v]) => `<tr><td>${esc(KEY_LABEL[k] || k.replaceAll("_", " "))}</td><td class="num">${fmtVal(k, v)}</td></tr>`).join("")}</table></section></div>
      <p class="small muted" style="margin-top:1rem">Report generated ${esc(r.generated_at || "")}. ${esc(r.description || "")}</p>`);
  }

  // ------------------------------------------------------------------ model health
  async function viewMonitor() {
    const [m, dr] = await Promise.all([api("/api/model"), api("/api/monitoring/drift")]);
    const met = m.metadata.metrics || {};
    const MODEL_NAME = { journey: "Journey model (reads the customer's story)", fused_risk: "Final combined risk score", collector: "Receiving-account model", txn_baseline_b1: "Comparison: per-payment model (B1)" };
    const allOk = Object.values(m.hashes_valid).every(Boolean);
    app.innerHTML = shell("monitor", pageHead("Model health",
      "Checks that the AI models are the approved ones and still behave as expected. This page is mainly for the technical owner, not for investigators.",
      `<ul><li><b>AUC / PR-AUC</b>: how well a model separates scam from normal activity on held-back data (1.0 = perfect).</li>
        <li><b>File integrity</b>: each model file's fingerprint (SHA-256) is checked so nobody can swap a model unnoticed.</li>
        <li><b>Drift (PSI)</b>: compares today's data with the training data. Below 0.1 is stable, 0.1–0.25 watch, above 0.25 review or retrain.</li></ul>`, "Model health") +
      `<div class="grid g2">
      <section class="card"><h2>Models in use</h2><table><tr><td>Version</td><td class="mono">${esc(m.metadata.version)}</td></tr><tr><td>Trained at</td><td>${esc(m.metadata.trained_at)}</td></tr>
        <tr><td>Training data period</td><td class="small">${esc((m.metadata.training_window || []).join(" → "))}</td></tr>
        ${["journey", "fused_risk", "collector", "txn_baseline_b1"].map((k) => `<tr><td>${esc(MODEL_NAME[k])}</td><td class="small">AUC <b>${met[k]?.auc ?? "–"}</b> · PR-AUC <b>${met[k]?.pr_auc ?? "–"}</b> · ${met[k]?.positives ?? "–"} scam examples</td></tr>`).join("")}
        <tr><td>File integrity</td><td>${allOk ? '<span class="badge b-new">all files verified</span>' : '<span class="badge b-confirmed">mismatch – investigate</span>'}${Object.entries(m.hashes_valid).map(([f, ok]) => `<div class="small">${ok ? "✔" : "✖"} ${esc(f)}</div>`).join("")}</td></tr></table>
        <p class="small muted">${esc(m.metadata.note || "")}</p></section>
      <section class="card"><h2>Is today's data still like the training data?</h2>${dr.status !== "ok" ? `<div class="empty"><b>Collecting samples (${dr.samples}/200)</b>Keep the simulation running; the check starts after 200 scored payments.</div>` :
        `<p>Overall: <b>${esc(dr.overall)}</b> (largest drift ${dr.worst_psi}) over ${num(dr.samples)} recent payments.</p><div class="scroll" style="max-height:360px"><table><tr><th>Feature</th><th class="num">PSI</th><th>State</th></tr>${dr.features.filter((f) => f.psi !== null).map((f) => `<tr><td class="mono">${esc(f.feature)}</td><td class="num">${f.psi}</td><td>${esc(f.state)}</td></tr>`).join("")}</table></div>`}
        <p class="small muted">Drift alarms go to the model owner, not to investigators.</p></section></div>
      <section class="card" style="margin-top:1rem"><h2>Learned scam-type library</h2><p class="explain">For each known scam type: how many past victims taught it, and how different a new journey must be to count as a possible new scam.</p><table><tr><th>Scam type</th><th class="num">Victims learned from</th><th class="num">Novelty threshold</th></tr>${Object.entries(m.library).map(([k, v]) => `<tr><td>${esc(v.title || k)}</td><td class="num">${v.victims ?? v.n}</td><td class="num">${v.threshold}</td></tr>`).join("")}</table></section>`);
  }

  // ------------------------------------------------------------------ audit
  async function viewAudit() {
    const head = pageHead("Audit log", "A permanent record of every sign-in, case view, decision, action and export. Each entry contains a fingerprint (hash) of the previous one, so editing or deleting any entry breaks the chain and is detected.", "", "Audit log");
    if (!can("supervisor", "auditor")) { app.innerHTML = shell("audit", head + `<div class="card empty"><b>Restricted page</b>Only supervisors and auditors can view the audit log. You are signed in as ${esc(S.user.role)}.</div>`); return; }
    const a = await api("/api/audit");
    app.innerHTML = shell("audit", head + `<div class="notice ${a.verify.valid ? "ok" : "warn"}" style="margin-bottom:1rem">${a.verify.valid ? `${ic("check")} <b>Chain verified.</b> All ${num(a.verify.entries)} entries are intact – nothing has been edited or removed.` : `<b>Warning: the chain is broken at entry ${a.verify.broken_at}.</b> Someone may have edited the log. Escalate immediately.`}</div>
      <section class="card scroll"><table><tr><th>Time</th><th>Who</th><th>Role</th><th>What they did</th><th>On</th><th>Details</th></tr>
      ${a.entries.map((e) => `<tr><td class="small">${new Date(e.ts * 1000).toLocaleString("en-IN")}</td><td>${esc(e.actor)}</td><td>${esc(e.role)}</td><td>${esc(e.action.replaceAll("_", " "))}</td><td class="mono">${esc(e.target)}</td><td class="small mono">${esc(e.details).slice(0, 140)}</td></tr>`).join("")}</table></section>`);
  }

  // ------------------------------------------------------------------ help
  function viewHelp() {
    app.innerHTML = shell("help", pageHead("Help & glossary", "Everything on this portal explained in plain words. New here? Start the <a href=\"#\" data-act=\"tour-open\">Guided demo</a>.", "", "Help & glossary") + `
      <div class="grid g2">
        <section class="card"><h2>The idea in one minute</h2>
          <p>Most fraud systems look at <b>one payment at a time</b> and raise an alert for each suspicious one – thousands per day, mostly false. Scammers beat them by changing amounts, accounts and messages.</p>
          <p>Vyuh looks at the <b>whole journey</b> of a customer and at <b>many customers together</b>. Organised scams follow a fixed script, so their victims leave the same pattern – even when every account and message is different.</p>
          <h3>Three kinds of evidence</h3>
          <ol><li><b>Journey</b> – this customer is moving through scam stages in order.</li><li><b>Destination</b> – their money goes to accounts that behave like collectors.</li><li><b>Crowd</b> – other strangers are following the same path right now.</li></ol></section>
        <section class="card"><h2>The stages of a scam</h2><table>${STAGES.map(([s, n, d]) => `<tr><td><span class="badge" style="background:var(--${s.toLowerCase()});color:#fff">${s}</span></td><td><b>${n}</b><br><span class="small">${esc(d)}</span></td></tr>`).join("")}</table></section>
        <section class="card"><h2>How Vyuh responds (lightest first)</h2><table>
          <tr><td><span class="badge b-R1">R1</span></td><td>Specific in-app warning naming the trick</td><td class="small muted">automatic</td></tr>
          <tr><td><span class="badge b-R2">R2</span></td><td>30-minute cooling-off pause on a payment to a new payee</td><td class="small muted">automatic, within policy</td></tr>
          <tr><td><span class="badge b-R3">R3</span></td><td>Bank call-back to the customer</td><td class="small muted">system requests, a person calls</td></tr>
          <tr><td><b>R4</b></td><td>Receiving accounts sent for review; 1930 report drafted</td><td class="small muted">investigator, after confirming</td></tr>
          <tr><td><b>R5</b></td><td>Pseudonymised sharing with partner banks</td><td class="small muted">supervisor only</td></tr></table>
          <p class="small muted">The customer can always choose to continue. Anything stronger than a pause needs a human.</p></section>
        <section class="card"><h2>Who can do what</h2><div class="role-list">${Object.entries(ROLE_INFO).map(([r, d]) => `<div><b class="mono">${r}</b> – ${esc(d)}</div>`).join("")}</div>
          <h3 style="margin-top:.8rem">How Vyuh avoids alert fatigue</h3><ul><li>One case per scam operation; new evidence updates it instead of creating new alerts.</li><li>A case needs at least 3 victims, 2 kinds of evidence and enough confidence.</li><li>The board shows only what the team can handle; very large cases (≥ ₹5 lakh saveable) always show.</li><li>Rejecting a case as “legitimate business” needs a second person.</li></ul></section>
      </div>
      <section class="card" style="margin-top:1rem"><h2>Glossary</h2><dl class="glossary grid g2">${GLOSSARY.map(([t, d]) => `<div><dt>${esc(t)}</dt><dd>${esc(d)}</dd></div>`).join("")}</dl></section>
      <section class="card" style="margin-top:1rem"><h2>Common questions</h2>
        <details><summary>Nothing is happening on the Control room</summary><p>Press <b>Start</b> in Step 1. If the clock is running but no case appears, launch a scam in Step 2 and use <b>Jump 1 hour</b> a few times.</p></details>
        <details><summary>The Customer phone page is empty</summary><p>It shows the first victim of the most recent test scam. Launch a scam campaign first.</p></details>
        <details><summary>Why are some action buttons greyed out?</summary><p>Actions that affect accounts or involve the police (R4, 1930 report, sharing, export) only unlock after a person confirms the case. Some are supervisor-only.</p></details>
        <details><summary>Is any of this real customer data?</summary><p>No. Every customer, account and payment is generated by the VyuhSim simulator. Account codes are pseudonyms.</p></details>
        <details><summary>How do I start over?</summary><p>Control room → <b>Reset demo</b>. To restart the guided demo, open it and press <b>Restart</b>.</p></details>
      </section>`);
  }

  // ------------------------------------------------------------------ actions (event delegation; CSP-safe)
  document.addEventListener("click", async (ev) => {
    if (ev.target.closest(".skip")) { ev.preventDefault(); document.getElementById("main")?.focus(); return; }
    const go = ev.target.closest("[data-go]");
    if (go && (!ev.target.closest("button") || ev.target.closest("button").dataset.go)) { location.hash = go.dataset.go; return; }
    const el = ev.target.closest("[data-act]"); if (!el) return;
    const act = el.dataset.act;
    if (el.tagName === "A") ev.preventDefault();
    try {
      if (act === "logout") logout();
      else if (act === "fs") { S.fs = el.dataset.v; store.set("vyuh_fs", S.fs); applyDisplayPrefs(); document.querySelectorAll('[data-act="fs"]').forEach((b) => b.classList.toggle("on", b.dataset.v === S.fs)); }
      else if (act === "contrast") { S.contrast = S.contrast ? "" : "high"; store.set("vyuh_contrast", S.contrast); applyDisplayPrefs(); el.classList.toggle("on", !!S.contrast); el.setAttribute("aria-pressed", String(!!S.contrast)); }
      else if (act === "info") el.closest(".kpi").classList.toggle("info-open");
      else if (act === "tour-toggle" || act === "tour-open") { S.tourOpen = act === "tour-open" ? true : !S.tourOpen; store.set("vyuh_tour_open", S.tourOpen); renderTour(); }
      else if (act === "tour-done") tourMark(el.dataset.k);
      else if (act === "tour-reset") { S.tour = {}; store.set("vyuh_tour", S.tour); renderTour(); }
      else if (act === "filter") { S.filter = el.dataset.f; document.querySelectorAll('[data-act="filter"]').forEach((b) => { b.classList.toggle("on", b.dataset.f === S.filter); b.setAttribute("aria-selected", String(b.dataset.f === S.filter)); }); toast("Showing: " + el.textContent); }
      else if (["start", "pause", "reset", "step"].includes(act)) {
        if (act === "reset" && !confirm("Reset the demo?\n\nCases and protections from this run are cleared. The audit log is kept.")) return;
        const speed = +(document.getElementById("speed")?.value || 300);
        await api("/api/replay", { method: "POST", body: { action: act, speed, minutes: 60 } });
        if (act === "start") tourMark("start");
        toast(act === "reset" ? "Demo reset. Press Start to begin again." : act === "start" ? "Simulation running. Now launch a test scam (Step 2)." : act === "step" ? "Jumped ahead 1 simulated hour." : "Simulation paused.");
        refreshStatus();
      } else if (act === "inject") {
        const r = await api("/api/simulate/inject", { method: "POST", body: { playbook: document.getElementById("pb").value, victims: +document.getElementById("nv").value } });
        tourMark("inject");
        toast(`Scam ${r.campaign_id} launched against ${r.victims} customers. Open “Customer phone” to see the first victim.`);
        if (!S.status?.running) toast(`Scam ${r.campaign_id} launched. Press Start – the clock is paused.`);
      } else if (act === "adversary") {
        const r = await api("/api/simulate/adversary", { method: "POST", body: { on: !(S.status && S.status.adversary) } });
        toast(r.adversary ? "Scammers now use new accounts, random amounts and no tell-tale words." : "Scammers back to normal behaviour.");
        refreshStatus();
      } else if (act === "decide") {
        const id = document.getElementById("casebody").dataset.caseId;
        const decision = el.dataset.d;
        const reason = decision === "reject" ? document.getElementById("reason").value : "";
        await api(`/api/cases/${encodeURIComponent(id)}/decision`, { method: "POST", body: { decision, reason } });
        tourMark("decide");
        toast({ confirm: `Case ${id} confirmed. Actions are now unlocked.`, monitor: `Case ${id} kept under review.`, reject: `Case ${id} rejected.`, close: `Case ${id} closed.` }[decision] || `Case ${id}: ${decision}`);
        renderCase(id);
      } else if (act === "action") {
        const id = document.getElementById("casebody").dataset.caseId;
        const r = await api(`/api/cases/${encodeURIComponent(id)}/actions`, { method: "POST", body: { action: el.dataset.a } });
        tourMark("act"); toast(r.detail); renderCase(id);
      } else if (act === "export") {
        const id = document.getElementById("casebody").dataset.caseId;
        const r = await api(`/api/cases/${encodeURIComponent(id)}/export`);
        const blob = new Blob([JSON.stringify(r, null, 2)], { type: "application/json" });
        const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${id}-package.json`; a.click();
        toast("Case package downloaded.");
      } else if (act === "approvepb") {
        const id = document.getElementById("casebody").dataset.caseId;
        const r = await api(`/api/playbooks/${encodeURIComponent(id)}/approve`, { method: "POST" });
        S.playbooks = []; toast(`Added to the scam-type library as ${r.name}.`);
      } else if (act === "truth") {
        const id = document.getElementById("casebody").dataset.caseId;
        const r = await api(`/api/cases/${encodeURIComponent(id)}/truth`);
        document.getElementById("truth").innerHTML = `<div class="notice info"><b>Simulator answer key</b> (never visible to the detector): ${esc(Object.entries(r.true_victims_by_campaign).map(([k, v]) => `${v} real victims of ${k}`).join(", ") || "no real victims")}; ${r.members_not_victims} linked customer(s) are not victims; ${r.collectors_confirmed} of ${r.collectors_total} receiving accounts are real mule accounts.</div>`;
      } else if (act === "align") { S.aligned = !S.aligned; renderCase(document.getElementById("casebody").dataset.caseId); }
      else if (act === "phone") { await api("/api/phone/respond", { method: "POST", body: { choice: el.dataset.c } }); toast(el.dataset.c === "cancel" ? "The customer cancelled the payment – money saved." : "The customer chose to continue."); }
    } catch (e) { toast(e.message); }
  });
  document.addEventListener("change", async (ev) => {
    if (ev.target.id === "speed") { try { await api("/api/replay", { method: "POST", body: { action: S.status?.running ? "start" : "pause", speed: +ev.target.value } }); toast("Speed changed."); } catch (e) { toast(e.message); } }
    if (ev.target.id === "pb") { const d = document.getElementById("pbdesc"); if (d) d.innerHTML = pbDescHtml(ev.target.value); }
  });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && S.tourOpen) { S.tourOpen = false; store.set("vyuh_tour_open", false); renderTour(); } });

  // ------------------------------------------------------------------ router
  async function render() {
    clearTimers();
    if (!S.token) { viewLogin(); return; }
    const [route, arg] = location.hash.replace(/^#\//, "").split("/");
    try {
      if (!S.status) await refreshStatus();
      renderTour();
      switch (route || "home") {
        case "login": case "home": await viewHome(); break;
        case "ops": await viewOps(); break;
        case "board": await viewBoard(); break;
        case "case": await viewCase(decodeURIComponent(arg || "")); break;
        case "phone": await viewPhone(); break;
        case "playbooks": await viewPlaybooks(); break;
        case "report": await viewReport(); break;
        case "monitor": await viewMonitor(); break;
        case "audit": await viewAudit(); break;
        case "help": viewHelp(); break;
        default: location.hash = "#/home"; return;
      }
      window.scrollTo(0, 0);
    } catch (e) { app.innerHTML = shell(route, `<div class="card empty"><b>Something went wrong</b>${esc(e.message)}</div>`); }
  }
  window.addEventListener("hashchange", render);
  render();
})();
