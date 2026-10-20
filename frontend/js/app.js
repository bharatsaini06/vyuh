/* Vyuh presentation console.
   Plain JavaScript, no build step, no libraries, works offline.
   CSP-safe: no inline scripts or handlers; every click goes through one
   delegated listener that reads data-act attributes. */
(function () {
  "use strict";
  const C = window.VyuhCharts;
  const { esc, inr, short } = C;
  const app = document.getElementById("app");

  // ---------------------------------------------------------------- state
  const pref = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage blocked */ } },
  };
  const S = {
    token: sessionStorage.getItem("vyuh_token"),
    user: JSON.parse(sessionStorage.getItem("vyuh_user") || "null"),
    status: null, board: null, playbooks: [], timers: [], statusTimer: null,
    filter: "all", aligned: true, caseData: null, caseKey: "", hideViews: true,
    big: pref.get("vyuh_big", false), role: "investigator",
    lang: pref.get("vyuh_lang", "en"), cancelIdx: 2, report: null,
  };
  document.documentElement.classList.toggle("big", S.big);

  // ---------------------------------------------------------------- helpers
  const num = (v) => Number(v || 0).toLocaleString("en-IN");
  const pct = (v, dp = 0) => (v === null || v === undefined ? "–" : (v * 100).toFixed(dp) + "%");
  const lakh = (v) => (v >= 1e7 ? "₹" + (v / 1e7).toFixed(2) + " Cr" : v >= 1e5 ? "₹" + (v / 1e5).toFixed(1) + " L" : inr(v));
  const fmtT = (t) => (t ? new Date(t * 1000).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");
  const can = (...roles) => !!S.user && roles.includes(S.user.role);
  const WORK = () => can("investigator", "supervisor");
  const $ = (id) => document.getElementById(id);
  const set = (id, html) => { const el = $(id); if (el) el.innerHTML = html; };
  function toast(msg) { const el = $("toast"); el.textContent = msg; el.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove("show"), 3800); }
  function every(ms, fn) { const run = () => Promise.resolve(fn()).catch((e) => console.warn(e)); run(); S.timers.push(setInterval(run, ms)); }
  function clearTimers() { S.timers.forEach(clearInterval); S.timers = []; }
  // Customers never see model scores (an attacker could use them to probe the system).
  const customerText = (t) => String(t || "").replace(/\s*\(\d+%\)/g, "");

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      method: opts.method || "GET",
      headers: { "content-type": "application/json", ...(S.token ? { authorization: "Bearer " + S.token } : {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (res.status === 401 && path !== "/api/auth/login") { logout(); throw new Error("Your session ended. Sign in again."); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : res.statusText || "Request failed");
    return data;
  }
  async function loadPlaybooks() {
    if (!S.playbooks.length) { try { S.playbooks = (await api("/api/playbooks")).playbooks; } catch (e) { /* shown elsewhere */ } }
  }
  const pbTitle = (name) => S.playbooks.find((p) => p.name === name)?.title || name || "Unclassified pattern";
  const caseTitle = (c) => (c.emerging ? "New scam type – matches no known script" : c.kind === "lone" ? "Single high-value victim" : pbTitle(c.playbook));

  // ---------------------------------------------------------------- words
  const STATUS = { new: ["Needs review", "new"], under_review: ["Under review", "review"], confirmed: ["Confirmed scam", "confirmed"], monitoring: ["Watching quietly", "monitoring"], rejected: ["Rejected", "rejected"], closed: ["Closed", "closed"], merged: ["Merged", "closed"] };
  const statusTag = (s) => `<span class="tag ${(STATUS[s] || [s, "closed"])[1]}">${esc((STATUS[s] || [s])[0])}</span>`;
  const LEVEL = { R0: "No action", R1: "Warned", R2: "Paused", R2T: "Trusted contact asked", R3: "Paused + call-back" };
  const FEEDBACK = { talk_to_bank: "asked for a call-back", customer_says_genuine: "said it is genuine", cancelled: "cancelled", customer_continued: "continued", trusted_contact_declined: "trusted contact declined", trusted_contact_approved: "trusted contact approved", kill_switch_on: "stopped all payments" };
  const MODES = [["shadow", "Shadow", "Score and build cases. Nothing reaches customers or investigators – the bank only measures."], ["analyst", "Analyst only", "Investigators get cases. Customers see nothing yet."], ["warnings", "Warnings", "Customers can get warnings, but no payment is paused."], ["full", "Full", "Warnings, pauses, trusted-contact approval and call-backs."]];
  const SCAM_INFO = {
    task_scam: "An easy online ‘job’ pays small rewards first, then asks for growing ‘task fees’ to unlock earnings.",
    digital_arrest: "Fake police or CBI on a video call keep the victim ‘under arrest’ and make them transfer savings for ‘verification’.",
    investment_group: "A stock-tips group shows fake profits in a fake app; the victim keeps investing, then pays ‘tax’ to withdraw.",
    kyc_remote_access: "A fake KYC call gets the victim to install a screen-sharing app, then money is moved out in quick transfers.",
    qr_refund_scam: "A fake buyer or ‘refund agent’ sends a QR code: ‘scan to receive your money’. A ₹1 test credit builds trust, then every scan pays the scammer. (Replaces the old collect-request scam – NPCI ended person-to-person collect requests on 1 Oct 2025.)",
    loan_app_extortion: "An instant-loan app credits a small loan, then ‘recovery agents’ threaten the victim and demand repeated payments to a new UPI ID every time.",
    parcel_customs: "A ‘parcel held at customs’ needs clearance fees that keep rising.",
    digital_arrest_loan: "A new variant the models have never seen: the digital-arrest script plus forcing the victim to take an instant loan first.",
  };
  const ROLES = [
    ["investigator", "Investigator", "Runs the demo, reviews cases, confirms or rejects, calls back customers."],
    ["supervisor", "Supervisor", "Everything an investigator does, plus sharing with partner banks and approving new scam types."],
    ["auditor", "Auditor", "Read-only. Sees every case and the tamper-evident audit log."],
  ];
  const ACTION_WORDS = { case_viewed: "Opened case", case_confirm: "Confirmed case", case_reject: "Rejected case", case_monitor: "Kept under review", case_close: "Closed case", login: "Signed in", login_failed: "Failed sign-in", new_case: "New case raised", material_change: "Case changed", campaign_injected: "Launched test scam", replay_start: "Started clock", replay_pause: "Paused clock", replay_step: "Jumped ahead", replay_reset: "Reset demo", case_exported: "Exported case", adversary_toggled: "Scammers adapt switched", customer_response_simulated: "Customer answered pause", playbook_approved: "Added new scam type", runtime_started: "System started", suppression_approved: "Approved suppression", suppression_declined: "Declined suppression", suppression_revoked: "Lifted suppression", suppression_expired: "Suppression expired", mode_changed: "Changed rollout mode", customer_kill_switch: "Customer kill switch", report_1930_downloaded: "Downloaded 1930 report", password_changed: "Changed password", password_change_failed: "Wrong current password", action_callback_live_victims: "Requested call-backs", action_collector_review: "Sent accounts for review", action_draft_1930_report: "Drafted 1930 report", action_share_with_partners: "Shared with partner banks" };

  // ---------------------------------------------------------------- customer screen words (English / Hindi)
  const PH = {
    en: { pause: "Payment paused for 30 minutes", toNew: "to a new payee", cancel: "Cancel payment", talk: "Talk to my bank", genuine: "This is me – it’s genuine", cont: "Continue anyway",
      unlock: (n) => `You are on a call. Scammers often stay on the line. “Continue” unlocks in ${n} s.`, help: "Not sure? Call your bank or 1930.", careful: "Be careful",
      saved: "Payment cancelled", savedBody: "The money stayed in your account. If someone asks you to pay to unlock money, get a refund or avoid arrest, it is a scam.",
      called: "Your bank will call you back shortly", calledBody: "The payment was stopped. Do not share any OTP or PIN with anyone in the meantime.",
      declined: "Your trusted contact said no", declinedBody: "The payment was not sent. Talk to them before trying again.",
      trust: "Waiting for your trusted contact", trustBody: "You asked that risky payments of ₹50,000 or more to a new payee are approved by a family member you trust.",
      tcSim: "Demo: your trusted contact answers", approve: "Approve", decline: "Decline",
      lock: "Stop all my online payments", lockOn: "All online payments are stopped", lockBody: "You switched on the kill switch. Turn it off here or at your branch when you are safe.",
      lang: "हिंदी", empty: "Launch a scam. Its first victim’s phone appears here.", paid: "Paid to", got: "Received from", none: "No customer yet", stopped: "Stopped – not sent to", loan: "Instant loan credited", fd: "Fixed deposit broken" },
    hi: { pause: "भुगतान 30 मिनट के लिए रोका गया", toNew: "नए प्राप्तकर्ता को", cancel: "भुगतान रद्द करें", talk: "मेरे बैंक से बात कराएं", genuine: "यह मैं ही हूँ – भुगतान सही है", cont: "फिर भी भेजें",
      unlock: (n) => `आप कॉल पर हैं। धोखेबाज़ अक्सर कॉल पर बने रहते हैं। ${n} सेकंड बाद “फिर भी भेजें” चालू होगा।`, help: "शक है? अपने बैंक या 1930 पर कॉल करें।", careful: "सावधान रहें",
      saved: "भुगतान रद्द हुआ", savedBody: "पैसे आपके खाते में सुरक्षित हैं। अगर कोई पैसे ‘अनलॉक’ करने, रिफ़ंड पाने या गिरफ़्तारी से बचने के लिए भुगतान मांगे, तो वह धोखा है।",
      called: "आपका बैंक जल्द आपको कॉल करेगा", calledBody: "भुगतान रोक दिया गया है। तब तक किसी को OTP या PIN न बताएं।",
      declined: "आपके भरोसेमंद व्यक्ति ने मना किया", declinedBody: "भुगतान नहीं भेजा गया। दोबारा कोशिश करने से पहले उनसे बात करें।",
      trust: "आपके भरोसेमंद व्यक्ति की मंज़ूरी का इंतज़ार", trustBody: "आपने तय किया था कि नए प्राप्तकर्ता को ₹50,000 या उससे ज़्यादा का जोखिम वाला भुगतान परिवार का भरोसेमंद सदस्य मंज़ूर करेगा।",
      tcSim: "डेमो: आपके भरोसेमंद व्यक्ति का जवाब", approve: "मंज़ूर करें", decline: "मना करें",
      lock: "मेरे सभी ऑनलाइन भुगतान रोकें", lockOn: "सभी ऑनलाइन भुगतान बंद हैं", lockBody: "आपने किल-स्विच चालू किया है। सुरक्षित होने पर इसे यहाँ या अपनी शाखा में बंद करें।",
      lang: "English", empty: "कोई स्कैम शुरू करें। उसके पहले पीड़ित का फ़ोन यहाँ दिखेगा।", paid: "भेजा", got: "मिला", none: "अभी कोई ग्राहक नहीं", stopped: "रोका गया – नहीं भेजा", loan: "तुरंत लोन जमा हुआ", fd: "FD तोड़ी गई" },
  };

  // ---------------------------------------------------------------- top bar
  const TABS = [["live", "Live demo"], ["cases", "Cases"], ["results", "Results"], ["safety", "Safety"]];
  function topbar(active) {
    const controls = WORK() ? `<span class="ctl">
      <button class="tb-btn play" data-act="play" id="tb-play" title="Space">Play</button>
      <select class="tb-sel" id="tb-speed" aria-label="Simulation speed">
        <option value="60">Slow</option><option value="300">Normal</option><option value="1200">Fast</option><option value="3600">Very fast</option></select>
      <button class="tb-btn" data-act="step" title="J">+1 hour</button>
      <button class="tb-btn" data-act="reset">Reset</button></span>` : "";
    return `<header class="topbar"><div class="in">
      <a class="brand" href="#/live"><b>VYUH</b><span>scam campaign detection</span></a>
      <nav class="tabs" aria-label="Main">${TABS.map(([k, l]) => `<a href="#/${k}" class="${active === k ? "on" : ""}"${active === k ? ' aria-current="page"' : ""}>${l}${k === "cases" ? '<span class="count hide" id="tb-count"></span>' : ""}</a>`).join("")}</nav>
      <span class="spacer"></span>
      <span id="tb-adv"></span>
      <span class="clock"><span class="dot" id="tb-dot"></span><span class="time" id="tb-time">–</span></span>
      ${controls}
      <button class="tb-btn" data-act="big" aria-pressed="${S.big}" title="Larger text for projectors">${S.big ? "A" : "A+"}</button>
      <span class="who"><b>${esc(S.user.username)}</b> · <a href="#" data-act="logout" style="color:#cdd6ea">Sign out</a></span>
    </div></header>`;
  }
  function paintTopbar() {
    const st = S.status; if (!st) return;
    set("tb-time", esc(st.sim_time.replace("T", " ")));
    const dot = $("tb-dot"); if (dot) dot.classList.toggle("on", st.running);
    const pb = $("tb-play"); if (pb) { pb.textContent = st.running ? "Pause" : "Play"; pb.classList.toggle("running", st.running); }
    const sp = $("tb-speed"); if (sp && document.activeElement !== sp) sp.value = String([60, 300, 1200, 3600].includes(st.speed) ? st.speed : 300);
    const cnt = $("tb-count"); if (cnt) { cnt.textContent = st.cases_open; cnt.classList.toggle("hide", !st.cases_open); }
    set("tb-adv", st.adversary ? '<span class="adv-flag">Scammers adapting</span>' : "");
    const m = MODES.find((x) => x[0] === st.mode);
    set("mode-banner", st.mode && st.mode !== "full" && m ? `<div class="mode-banner"><b>${esc(m[1])} mode.</b> ${esc(m[2])} <a href="#/safety">Change on the Safety tab</a></div>` : "");
  }
  async function refreshStatus() {
    try { S.status = await api("/api/status"); paintTopbar(); if (S.onStatus) S.onStatus(); } catch (e) { /* keep last value */ }
  }
  function shell(active, inner) {
    return `${topbar(active)}<div id="mode-banner"></div><main class="page" id="main" tabindex="-1">${inner}</main>
      <footer class="foot">Demonstration system · all customers, accounts and money are synthetic (VyuhSim) · model outputs are risk signals for human review, never accusations · real fraud: call 1930</footer>`;
  }

  // ---------------------------------------------------------------- sign in
  function viewLogin() {
    app.innerHTML = `<div class="login">
      <section class="pitch">
        <div class="mark">VYUH<small>व्यूह · scam campaign detection for UPI and banking</small></div>
        <p class="line">AI lets scammers change every message and every account. It cannot change the playbook.</p>
        <p class="sub">Vyuh follows each customer's payment journey, links strangers who are walking the same scam script, protects them while it is happening, and gives investigators one explained case per scam gang.</p>
      </section>
      <section class="form" id="main">
        <h1>Sign in</h1>
        <p class="muted" style="margin:0">Pick a role for the demo.</p>
        <div class="roles" role="radiogroup" aria-label="Role">${ROLES.map(([k, t, d]) => `<button type="button" class="role" role="radio" aria-checked="${S.role === k}" data-act="role" data-r="${k}"><span class="r"></span><span><b>${t}</b><span>${esc(d)}</span></span></button>`).join("")}</div>
        <form id="login-form">
          <label class="f">Password<input type="password" id="pw" autocomplete="current-password" required placeholder="From the server's .env file"></label>
          <div style="margin-top:14px"><button class="btn primary lg" type="submit">Sign in</button></div>
          <div class="err" id="err" role="alert"></div>
        </form>
      </section></div>`;
    $("pw").focus();
    $("login-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        const r = await api("/api/auth/login", { method: "POST", body: { username: S.role, password: $("pw").value } });
        S.token = r.token; S.user = { username: r.username, role: r.role };
        sessionStorage.setItem("vyuh_token", r.token); sessionStorage.setItem("vyuh_user", JSON.stringify(S.user));
        location.hash = "#/live"; render();
      } catch (e) { set("err", esc(e.message)); }
    });
  }
  function logout() {
    sessionStorage.removeItem("vyuh_token"); sessionStorage.removeItem("vyuh_user");
    S.token = null; S.user = null; S.status = null; S.board = null;
    clearInterval(S.statusTimer); S.statusTimer = null;
    location.hash = "#/login"; render();
  }

  // ---------------------------------------------------------------- live demo
  function storySteps() {
    const st = S.status || {}, b = S.board || { cases: [], overflow: [] };
    const iv = st.interventions || {};
    const all = [...(b.cases || []), ...(b.overflow || [])];
    const done = [
      !!(st.running || st.events_processed),
      !!(st.injected || []).length,
      (iv.R1 || 0) + (iv.R2 || 0) + (iv.R3 || 0) > 0,
      (st.cases_open || 0) > 0,
      all.some((c) => c.status === "confirmed"),
      !!st.adversary,
    ];
    const steps = [
      ["Start the bank", "Press Play. Thousands of normal payments start flowing."],
      ["Launch a scam", "Pick a scam type below and press Launch scam."],
      ["Customer is protected", "Watch the phone: a warning in English or हिंदी, a pause, and ‘Talk to my bank’."],
      ["One case opens", "Vyuh links the victims into one case. Use +1 hour to speed up."],
      ["A person decides", "Open the case, read why, and press Confirm scam."],
      ["Scammers adapt", "Switch on ‘Make scammers smarter’. New victims are still linked."],
    ];
    const now = done.findIndex((d) => !d);
    return `<ol class="story" aria-label="Demo steps">${steps.map(([t, h], i) => `<li class="${done[i] ? "done" : i === now ? "now" : ""}"><span class="n">${done[i] ? "✓" : i + 1}</span><span class="t">${t}</span><div class="h">${h}</div></li>`).join("")}</ol>`;
  }
  function heroHtml() {
    const st = S.status; if (!st) return "";
    const alerts = st.baseline_alerts_total || 0, cases = st.cases_open || 0;
    const ratio = cases ? Math.round(alerts / cases) : null;
    return `<div class="side old"><div class="label">A rules-based fraud system raised</div><div class="big-num">${num(alerts)}</div><div class="sub">separate payment alerts</div></div>
      <div class="vs"><div class="ratio">${ratio ? num(ratio) + "× fewer" : "vs"}</div><div class="ratio-sub">${ratio ? "items for a person to check" : "same payments, same time"}</div></div>
      <div class="side new"><div class="label">Vyuh asks an investigator to review</div><div class="big-num">${num(cases)}</div><div class="sub">case${cases === 1 ? "" : "s"} – one per scam gang</div></div>`;
  }
  function statsHtml() {
    const st = S.status; if (!st) return "";
    const fr = st.legit_friction || {};
    return `<div class="panel stat safe"><div class="k">Money saved</div><div class="v">${lakh(st.money.protected)}</div><div class="d">${num(st.prevented_payments)} scam payments stopped</div></div>
      <div class="panel stat scam"><div class="k">Already lost</div><div class="v">${lakh(st.money.lost)}</div><div class="d">paid before or despite a warning</div></div>
      <div class="panel stat"><div class="k">Genuine customers disturbed</div><div class="v">${num((fr.paused || 0) + (fr.warned || 0))}</div><div class="d">${num(fr.paused)} paused · ${num(fr.warned)} warned</div></div>
      <div class="panel stat"><div class="k">Time to score a payment</div><div class="v">${st.latency_ms.p95} ms</div><div class="d">slowest 5% · ${num(st.events_processed)} events checked</div></div>`;
  }
  function scamDesc(name) {
    const p = S.playbooks.find((x) => x.name === name); if (!p) return "";
    return `${p.known ? "" : '<span class="tag emerging">Never seen by the models</span> '}${esc(SCAM_INFO[p.name] || "")}
      <div class="warn"><b>The customer will be told:</b> ${esc(p.warning)}</div>`;
  }
  function phoneHtml(p) {
    const L = PH[S.lang] || PH.en, hi = S.lang === "hi";
    const bar = (sub) => `<div class="appbar"><div><b>DemoBank UPI</b><span>${sub}</span></div><button class="lang" data-act="lang" aria-label="Change language">${L.lang}</button></div>`;
    if (!p || !p.featured) return `<div class="screen">${bar(esc(L.none))}<div class="phone-empty">${esc(L.empty)}</div></div>`;
    const last = (p.interventions || [])[0];
    const fb = (p.feedback || {}).choice;
    let card = "";
    if (p.locked) {
      card = `<div class="saved locked"><h3>${esc(L.lockOn)}</h3><p>${esc(L.lockBody)}</p></div>`;
    } else if (p.pending) {
      const d = p.pending, msg = hi ? (d.message_hi || d.message) : d.message;
      if (p.pending_kind === "trusted") {
        card = `<div class="pause trusted" role="alert"><h3>${esc(L.trust)}</h3>
          <div class="amt">${inr(d.amount)}</div><p class="muted small">${esc(L.toNew)}</p>
          <p>${esc(L.trustBody)}</p><p class="small">${esc(msg)}</p>
          ${WORK() ? `<div class="tc-box"><div class="small muted">${esc(L.tcSim)}</div><div class="btn-row"><button class="btn danger" data-act="phone" data-c="decline">${esc(L.decline)}</button><button class="btn" data-act="phone" data-c="approve">${esc(L.approve)}</button></div></div>` : ""}
          <p class="small muted" style="margin:8px 0 0">${esc(L.help)}</p></div>`;
      } else {
        const why = hi ? "" : (d.reasons || []).slice(0, 3).map((r) => `<li>${esc(customerText(r.text))}</li>`).join("");
        const hold = p.hold_seconds_left || 0;
        card = `<div class="pause" role="alert"><h3>${esc(L.pause)}</h3>
          <div class="amt">${inr(d.amount)}</div><p class="muted small">${esc(L.toNew)}</p>
          <p>${esc(msg)}</p>${why ? `<ul class="why">${why}</ul>` : ""}
          ${WORK() ? `<div class="btn-row"><button class="btn danger" data-act="phone" data-c="cancel">${esc(L.cancel)}</button><button class="btn primary" data-act="phone" data-c="talk">${esc(L.talk)}</button></div>
          <div class="btn-row" style="margin-top:8px"><button class="btn" data-act="phone" data-c="genuine"${hold ? " disabled" : ""}>${esc(L.genuine)}</button><button class="btn" data-act="phone" data-c="continue"${hold ? " disabled" : ""}>${esc(L.cont)}</button></div>` : ""}
          ${hold ? `<p class="hold" aria-live="polite">${esc(L.unlock(hold))}</p>` : ""}
          <p class="small muted" style="margin:8px 0 0">${esc(L.help)}</p></div>`;
      }
    } else if (p.stopped) {
      const [h, b] = fb === "talk_to_bank" ? [L.called, L.calledBody] : fb === "trusted_contact_declined" ? [L.declined, L.declinedBody] : [L.saved, L.savedBody];
      card = `<div class="saved"><h3>${esc(h)}</h3><p>${esc(b)}</p></div>`;
    } else if (last) {
      const msg = hi ? ((p.last_warning || {}).hi || last.message) : last.message;
      card = `<div class="warned"><h3>${esc(L.careful)}</h3><p>${esc(msg)}</p></div>`;
    }
    const txs = (p.transactions || []).slice(0, p.pending ? 3 : 5).map((t) => `<div class="tx${t.stopped ? " stopped" : ""}"><span>${t.stopped ? esc(L.stopped) : t.kind === "credit" ? esc(L.got) : t.kind === "debit" ? esc(L.paid) : t.kind === "loan" ? esc(L.loan) : t.kind === "fd_break" ? esc(L.fd) : esc(t.kind)} ${esc(short(t.counterparty))}<br><span class="muted small">${fmtT(t.t)}</span></span>
      <span class="${t.kind === "credit" || t.kind === "loan" || t.kind === "fd_break" ? "amt-in" : "amt-out"}">${t.stopped ? "" : t.kind === "debit" ? "−" : "+"}${inr(t.amount)}</span></div>`).join("");
    const who = `Customer ${esc(short(p.featured))}${p.age_band ? " · " + esc(p.age_band) : ""}${p.trusted_contact ? " · trusted contact on" : ""}`;
    const lock = WORK() ? `<div class="lockrow"><button role="switch" aria-checked="${!!p.locked}" data-act="lock" aria-label="${esc(L.lock)}"></button><span>${esc(L.lock)}</span></div>` : "";
    return `<div class="screen">${bar(who)}${card}<div class="txs">${txs}</div>${lock}</div>`;
  }
  function caseCard(c) {
    const cls = c.emerging ? "is-emerging" : c.status === "confirmed" ? "is-confirmed" : c.status === "new" ? "is-new" : "";
    return `<button class="ccard ${cls}" data-go="#/case/${esc(c.case_id)}">
      <div><div class="top"><span class="id">${esc(c.case_id)}</span>${statusTag(c.status)}${c.emerging ? '<span class="tag emerging">Emerging</span>' : ""}${c.kind === "lone" ? '<span class="tag lone">Single victim</span>' : ""}</div>
        <div class="title">${esc(caseTitle(c))}</div>
        <div class="facts"><span><b>${num(c.victims)}</b> victims</span><span><b>${pct(c.confidence)}</b> confidence</span><span><b>${num(c.collectors)}</b> receiving accounts</span>${c.growth >= 1.5 ? "<span><b>growing fast</b></span>" : ""}</div></div>
      <div class="save"><div class="v">${lakh(c.saveable)}</div><div class="k">still saveable</div></div></button>`;
  }
  async function viewLive() {
    await loadPlaybooks();
    const opts = S.playbooks.map((p) => `<option value="${esc(p.name)}"${p.name === "task_scam" ? " selected" : ""}>${esc(p.title)}${p.known ? "" : " (new)"}</option>`).join("");
    const launch = WORK() ? `<section class="panel"><h2>Launch a test scam</h2>
        <p class="muted small" style="margin-top:-6px">Plant a scam gang that targets some synthetic customers.</p>
        <div class="grid" style="grid-template-columns:1fr 110px;gap:10px">
          <label class="f">Scam type<select id="pb">${opts}</select></label>
          <label class="f">Victims<input type="number" id="nv" value="8" min="3" max="60"></label></div>
        <div class="scam-desc" id="pbdesc">${scamDesc("task_scam")}</div>
        <label class="check"><input type="checkbox" id="senior"><span>First victim is a senior citizen who set up a <b>trusted contact</b> (best with digital arrest)</span></label>
        <button class="btn danger lg" data-act="inject">Launch scam</button>
        <div class="switch"><button role="switch" aria-checked="false" id="advsw" data-act="adversary" aria-label="Make scammers smarter"></button>
          <div><b>Make scammers smarter</b><div class="muted small">New receiving account for every payment, random amounts and timing, no tell-tale words. The models are not retrained.</div></div></div>
      </section>` : `<section class="panel"><h2>Read-only view</h2><p class="muted">You are signed in as auditor. The demo controls are hidden; everything else updates live.</p></section>`;
    app.innerHTML = shell("live", `
      <div id="story">${storySteps()}</div>
      <section class="panel hero" id="hero" aria-live="polite">${heroHtml()}</section>
      <div class="grid cols-3">
        ${launch}
        <div class="phone-col"><div class="phone" id="phone">${WORK() ? phoneHtml(null) : '<div class="screen"><div class="appbar"><b>DemoBank UPI</b><span>Customer view</span></div><div class="phone-empty">The customer\'s phone is shown to investigators and supervisors only.</div></div>'}</div><p class="phone-note">What the victim sees in their banking app</p></div>
        <section class="panel"><h2>Cases for the investigator</h2><div class="case-list compact" id="live-cases"><div class="empty">Loading…</div></div>
          <p style="margin:12px 0 0"><a href="#/cases">See all cases</a></p></section>
      </div>
      <div class="grid cols-4" style="margin-top:18px" id="stats">${statsHtml()}</div>
      ${WORK() ? '<p class="keys" style="margin-top:14px">Shortcuts: <kbd>Space</kbd> play or pause · <kbd>J</kbd> jump one hour</p>' : ""}`);
    S.onStatus = () => {
      set("hero", heroHtml()); set("stats", statsHtml()); set("story", storySteps());
      const sw = $("advsw"); if (sw) sw.setAttribute("aria-checked", String(!!S.status.adversary));
    };
    if (S.status) S.onStatus();
    if (WORK()) every(1500, async () => set("phone", phoneHtml(await api("/api/phone"))));
    every(2500, async () => {
      S.board = await api("/api/board");
      const list = [...S.board.cases, ...S.board.overflow].slice(0, 4);
      set("live-cases", list.map(caseCard).join("") || `<div class="empty"><b>No cases yet</b>${S.status && S.status.injected.length ? (WORK() ? "Victims are being linked. Press +1 hour to move faster." : "Victims are being linked. A case appears once there is enough evidence.") : "Launch a scam and a case appears here within a few simulated hours."}</div>`);
      set("story", storySteps());
    });
  }

  // ---------------------------------------------------------------- cases
  async function viewCases() {
    await loadPlaybooks();
    const F = [["all", "All open"], ["new", "Needs review"], ["under_review", "Under review"], ["confirmed", "Confirmed"], ["emerging", "New scam types"]];
    app.innerHTML = shell("cases", `<h1>Cases</h1>
      <p class="lead">One card per suspected scam gang, most urgent first. Urgency = money still saveable × confidence × how fast it is growing. The list never grows beyond what the team can review.</p>
      <div class="chips" role="tablist">${F.map(([k, l]) => `<button class="chip ${S.filter === k ? "on" : ""}" role="tab" aria-selected="${S.filter === k}" data-act="filter" data-f="${k}">${l}</button>`).join("")}</div>
      <div id="cases-body"><div class="panel empty">Loading cases…</div></div>`);
    S.onStatus = null;
    every(2500, async () => {
      const b = S.board = await api("/api/board");
      const keep = (c) => S.filter === "all" || (S.filter === "emerging" ? c.emerging : c.status === S.filter);
      const cases = b.cases.filter(keep), over = b.overflow.filter(keep);
      set("cases-body", `<div class="case-list">${cases.map(caseCard).join("") || `<div class="panel empty"><b>${S.filter === "all" ? "No open cases" : "No cases match this filter"}</b>Launch a scam on the Live demo tab, then give it a few simulated hours.</div>`}</div>
        ${over.length ? `<h2 style="margin-top:24px">Over capacity (${over.length})</h2><p class="muted small">Lower urgency than the top ${b.capacity}. Listed so nothing is silently dropped.</p><div class="case-list">${over.map(caseCard).join("")}</div>` : ""}
        <details class="more panel" style="margin-top:18px"><summary>Watching quietly (${b.monitoring.length}) – linked customers without enough evidence to bother a person</summary>
          <div class="scroll" style="margin-top:10px"><table><tr><th>Case</th><th class="r">Customers</th><th class="r">Confidence</th><th>Evidence so far</th><th class="r">Saveable</th></tr>
          ${b.monitoring.map((m) => `<tr data-go="#/case/${esc(m.case_id)}" style="cursor:pointer"><td>${esc(m.case_id)}</td><td class="r">${m.victims}</td><td class="r">${pct(m.confidence)}</td><td>${esc(m.evidence.join(", ") || "–")}</td><td class="r">${inr(m.saveable)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">Nothing being watched.</td></tr>'}</table></div></details>`);
    });
  }

  // ---------------------------------------------------------------- case detail
  async function viewCase(id) {
    await loadPlaybooks();
    S.onStatus = null; S.caseData = null; S.caseKey = "";
    const sup = can("supervisor");
    const decide = WORK() ? `<section class="panel decide" aria-label="Decision">
        <div class="row1"><span class="lbl">Your decision</span>
          <button class="btn ok" data-act="decide" data-d="confirm" id="d-confirm">Confirm scam</button>
          <button class="btn" data-act="decide" data-d="monitor">Keep under review</button>
          <select id="reason" aria-label="Reason to reject"><option value="weak_evidence">Reason: weak evidence</option><option value="legitimate_business">Reason: legitimate business</option><option value="duplicate">Reason: duplicate case</option><option value="other">Reason: other</option></select>
          <button class="btn danger" data-act="decide" data-d="reject">Reject</button>
          <button class="btn" data-act="decide" data-d="close">Close</button>
          <span class="state" id="d-state"></span></div>
        <div class="row2"><span class="lbl" style="font-weight:700">Actions</span>
          <button class="btn primary" data-act="action" data-a="callback_live_victims">Call back customers (R3)</button>
          <button class="btn" data-act="action" data-a="collector_review" data-gate="1">Send receiving accounts for review (R4)</button>
          <button class="btn" data-act="action" data-a="draft_1930_report" data-gate="1">Draft 1930 report (R4)</button>
          <button class="btn" data-act="report" data-fmt="json" data-gate="1">Download 1930 report</button>
          <button class="btn" data-act="report" data-fmt="csv" data-gate="1">Money trail (CSV)</button>
          ${sup ? `<button class="btn" data-act="action" data-a="share_with_partners" data-gate="1">Share with partner banks (R5)</button>
          <button class="btn" data-act="export" data-gate="1">Download case package</button>
          <button class="btn" data-act="approvepb" id="d-approve">Add as new scam type</button>
          <button class="btn" data-act="truth">Show answer key</button>` : ""}
          <span class="gate" id="d-gate">R4 and R5 unlock after you confirm.</span></div>
        <div id="c-answer"></div>
      </section>` : `<section class="panel" style="margin-bottom:18px"><p class="muted" style="margin:0">You are signed in as auditor: you can read this case but not change it.</p></section>`;
    app.innerHTML = shell("cases", `<div class="crumb"><a href="#/cases">Cases</a> / ${esc(id)}</div>
      <div class="case-head" id="c-head"><h1>${esc(id)}</h1></div>
      ${decide}
      <div id="c-notes"></div>
      <div class="grid cols-4" id="c-kpis"></div>
      <section class="panel" style="margin-top:18px"><h2>Why Vyuh flagged this</h2><ul class="why-list" id="c-why"></ul></section>
      <section class="panel" style="margin-top:18px"><div class="btn-row" style="justify-content:space-between"><h2 style="margin:0">Victim journeys</h2>
          <button class="btn" data-act="align" id="c-align">Show in real time</button></div>
        <p class="muted small" style="margin:6px 0 0">One row per victim, one dot per scam step. When strangers show the same pattern of dots, it is the same script – the same gang.</p>
        <div class="legend"><span><i style="background:var(--s3)"></i>Trust (small credit, refund)</span><span><i style="background:var(--s4)"></i>Control (call, screen-share)</span><span><i style="background:var(--s5)"></i>Rising payments</span><span><i style="background:var(--s7)"></i>‘Fee to withdraw’</span><span>hollow = money received · ✓ = protected</span></div>
        <div id="c-tl"></div></section>
      <section class="panel" style="margin-top:18px"><h2>Where the money went</h2><p class="muted small" style="margin:-6px 0 8px">Thicker line = more money. Many strangers paying a few new accounts that pass the money on within minutes is the cash-out pattern.</p><div id="c-flow"></div></section>
      <div class="grid cols-2" style="margin-top:18px">
        <section class="panel"><h2>Receiving accounts</h2><p class="muted small" style="margin-top:-6px">Many holders are recruited victims themselves. Act only through bank procedure, after confirmation.</p><div class="scroll" id="c-coll"></div></section>
        <section class="panel"><h2>Victims</h2><div class="scroll" id="c-vict"></div></section>
      </div>
      <details class="more panel" style="margin-top:18px"><summary>Case history</summary><div id="c-hist" style="margin-top:8px"></div></details>`);
    await loadCase(id);
    every(4000, async () => {           // watch the board (no audit entry) and reload the case only when it changed
      const b = await api("/api/board");
      const c = [...b.cases, ...b.overflow, ...b.monitoring].find((x) => x.case_id === id);
      if (!c) return;
      const key = [c.victims, c.status, c.updated_t, Math.round(c.saveable)].join("|");
      if (S.caseKey && key !== S.caseKey) await loadCase(id);
      S.caseKey = key;
    });
  }
  async function loadCase(id) {
    let d;
    try { d = await api("/api/cases/" + encodeURIComponent(id)); } catch (e) { set("c-kpis", `<div class="panel empty" style="grid-column:1/-1"><b>Could not load this case</b>${esc(e.message)}</div>`); return; }
    S.caseData = d;
    S.caseKey = [d.victims, d.status, d.updated_t, Math.round(d.saveable)].join("|");
    set("c-head", `<h1>${esc(caseTitle(d))}</h1>${statusTag(d.status)}${d.emerging ? '<span class="tag emerging">Emerging</span>' : ""}<span class="muted">${esc(d.case_id)}</span>`);
    const nStates = Object.keys(d.states || {}).length;
    const notes = [];
    if (d.status === "merged" && d.merged_into) notes.push(`<div class="note-bar">This case was merged into <a href="#/case/${esc(d.merged_into)}">${esc(d.merged_into)}</a> because the victims paid the same cash-out accounts.</div>`);
    if (d.registry_listed) notes.push(`<div class="note-bar warn">${num(d.registry_listed)} receiving account(s) are on the suspect list (Suspect-Registry style feed from 1930 complaints).</div>`);
    if (d.complaints) notes.push(`<div class="note-bar">${num(d.complaints)} victim(s) of this case have complained on 1930.</div>`);
    const talk = (d.customer_feedback || []).filter((f) => f === "talk_to_bank").length;
    if (talk) notes.push(`<div class="note-bar ok">${num(talk)} customer(s) pressed ‘Talk to my bank’ – call them back first.</div>`);
    if (d.weak_linked) notes.push(`<div class="note-bar muted">${num(d.weak_linked)} more customer(s) look similar but are only weakly linked, so they are not counted here.</div>`);
    set("c-notes", notes.join(""));
    set("c-kpis", `<div class="panel stat"><div class="k">Victims</div><div class="v">${num(d.victims)}</div><div class="d">${num(d.live_victims)} still paying${nStates > 1 ? ` · ${nStates} states` : ""}</div></div>
      <div class="panel stat safe"><div class="k">Still saveable</div><div class="v">${lakh(d.saveable)}</div><div class="d">if someone acts now</div></div>
      <div class="panel stat scam"><div class="k">Already paid</div><div class="v">${lakh(d.paid)}</div><div class="d">sent to the scammers</div></div>
      <div class="panel stat"><div class="k">Confidence</div><div class="v">${pct(d.confidence)}</div><div class="d">growth ${d.growth}× in 6 h · ${esc(d.evidence.join(", ") || "no")} evidence</div></div>`);
    set("c-why", d.why.map((w) => `<li>${esc(w)}</li>`).join(""));
    set("c-flow", C.moneyFlow(d.graph));
    paintTimeline();
    set("c-coll", `<table><tr><th>Account</th><th class="r">Score</th><th class="r">Age (days)</th><th class="r">Payers 24 h</th><th class="r">Forwards in (min)</th><th class="r">At risk</th></tr>
      ${d.collectors.map((c) => `<tr><td>${esc(short(c.account))}${c.business ? ' <span class="tag lone">business</span>' : ""}${c.listed ? ' <span class="tag listed">on suspect list</span>' : ""}</td><td class="r">${c.score.toFixed(2)}</td><td class="r">${c.age_days ?? "–"}</td><td class="r">${c.payers_24h}</td><td class="r">${c.median_forward_min ?? "–"}</td><td class="r">${inr(c.balance_7d)}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">None identified yet.</td></tr>'}</table>`);
    set("c-vict", `<table><tr><th>Customer</th><th>State</th><th class="r">Risk</th><th class="r">Paid</th><th>Protection</th><th>Now</th></tr>
      ${d.members.map((m) => `<tr><td>${esc(short(m.token))}${m.complained_t ? ' <span class="tag listed" title="Complained on 1930">1930</span>' : ""}</td><td class="small">${esc(m.state || "–")}</td><td class="r">${(m.score ?? 0).toFixed(2)}</td><td class="r">${inr(m.paid)}</td><td>${esc(LEVEL[m.level] || m.level || "–")}${m.feedback ? `<div class="small muted">customer ${esc(FEEDBACK[m.feedback.choice] || m.feedback.choice)}</div>` : ""}</td><td>${m.stopped ? "protected" : m.live ? "still paying" : "inactive"}</td></tr>`).join("")}</table>`);
    const hist = [...d.history.map((h) => ({ t: h.t, text: h.text, sim: true })), ...d.decisions.map((x) => ({ real: x.ts, text: `${x.actor}: ${x.decision}${x.reason ? " (" + x.reason.replace("_", " ") + ")" : ""}` })), ...d.actions.map((a) => ({ real: a.ts, text: `${a.actor}: ${a.detail || a.action}` }))];
    set("c-hist", hist.map((h) => `<div style="padding:6px 0;border-bottom:1px solid var(--line)">${esc(h.text)} <span class="muted small">· ${h.sim ? fmtT(h.t) + " simulated" : new Date(h.real * 1000).toLocaleTimeString()}</span></div>`).join("") || '<p class="muted">No history yet.</p>');
    // decision bar: only states change, the controls themselves are never redrawn (keeps the chosen reason)
    const conf = d.status === "confirmed", open = ["new", "under_review", "confirmed", "monitoring"].includes(d.status);
    document.querySelectorAll('[data-gate="1"]').forEach((b) => (b.disabled = !conf));
    document.querySelectorAll('[data-act="decide"]').forEach((b) => (b.disabled = !open || (b.dataset.d === "confirm" && conf)));
    const ap = $("d-approve"); if (ap) { ap.disabled = !d.emerging; ap.title = d.emerging ? "" : "Only for Emerging cases"; }
    set("d-gate", conf ? "Confirmed – all actions unlocked. Each one is written to the audit log with your name." : "R4 and R5 unlock after you confirm.");
    set("d-state", open ? "" : `This case is ${esc((STATUS[d.status] || [d.status])[0].toLowerCase())}.`);
  }
  function paintTimeline() {
    if (!S.caseData) return;
    set("c-tl", C.timelines(S.caseData.members, S.aligned));
    const b = $("c-align"); if (b) b.textContent = S.aligned ? "Show in real time" : "Line up at first step";
  }

  // ---------------------------------------------------------------- results
  const POLICY_NAMES = {
    rbi_blanket: ["RBI proposal", "Every transfer of ₹10,000+ to a person waits 1 hour"],
    rbi_whitelist: ["RBI proposal + whitelist", "Only new payees wait (known payees whitelisted)"],
    hybrid: ["Hybrid", "₹10,000+ to a new person waits only if Vyuh sees risk, plus Vyuh’s own pauses"],
    vyuh: ["Vyuh targeted", "Only the payments Vyuh pauses wait; others may get a warning"],
  };
  function policyHtml(pol) {
    if (!pol || !pol.policies) return '<p class="muted">Re-run the evaluation to see the policy simulator.</p>';
    const i = Math.min(S.cancelIdx, pol.cancel_rates.length - 1), c = pol.cancel_rates[i];
    const keys = ["rbi_blanket", "rbi_whitelist", "hybrid", "vyuh"].filter((k) => pol.policies[k]);
    const maxHeld = Math.max(...keys.map((k) => pol.policies[k].genuine_payments_held_per_day), 1);
    const pc = (v, dp = 1) => (v * 100).toFixed(dp) + "%";
    const rows = keys.map((k) => {
      const p = pol.policies[k], n = POLICY_NAMES[k];
      return `<tr class="${k === "vyuh" ? "vy" : ""}"><td><b>${esc(n[0])}</b><div class="small muted">${esc(n[1])}</div></td>
        <td>${C.bars([{ label: "", value: p.money_protected_pct[i], color: "var(--safe)", text: pc(p.money_protected_pct[i]) }], 1)}</td>
        <td>${C.bars([{ label: "", value: p.genuine_payments_held_per_day, color: "var(--scam)", text: num(Math.round(p.genuine_payments_held_per_day * 10) / 10) }], maxHeld)}</td>
        <td class="r">${num(Math.round(p.genuine_payments_held_per_lakh_customers_per_day))}</td>
        <td class="r">${pc(p.genuine_share_held, 3)}</td></tr>`;
    }).join("");
    const cal = pol.calibration || {};
    return `<label class="slider"><span>If <b>${Math.round(c * 100)}%</b> of victims stop when their payment is held</span>
        <input type="range" id="cr" min="0" max="${pol.cancel_rates.length - 1}" step="1" value="${i}" aria-label="Share of victims who stop when a payment is held"></label>
      <div class="scroll"><table class="policy"><tr><th>Policy</th><th style="min-width:170px">Scam money stopped</th><th style="min-width:170px">Genuine payments held / day</th><th class="r">… per 1 lakh customers / day</th><th class="r">Share of genuine payments held</th></tr>${rows}</table></div>
      <p class="muted small" style="margin:10px 0 0">The same assumption is used for every policy, so the only difference is <b>which</b> payments are held. Nobody knows the real cancel rate yet, hence the slider. In our synthetic bank, scam payments of ₹10,000+ are ${pc(cal.scam_payments_ge_10k_share_of_count || 0, 0)} of scam payments and ${pc(cal.scam_payments_ge_10k_share_of_value || 0, 0)} of scam value (RBI Annual Report 2025-26: about 45% of fraud cases and 98.5% of fraud value). A simulation to inform policy, not a criticism of RBI’s proposal.</p>`;
  }
  async function viewResults() {
    S.onStatus = null;
    let r;
    try { r = await api("/api/report"); } catch (e) {
      app.innerHTML = shell("results", `<h1>Results</h1><div class="panel empty"><b>No evaluation report yet</b>Run <code>python -m scripts.prepare</code> on the server, then reload this page.</div>`); return;
    }
    S.report = r;
    const s = r.systems || {}, b0 = s.B0 || {}, b2m = s.B2m || s.B1m || {}, vy = s.Vyuh || {}, cp = r.campaigns || {}, rb = r.robustness || {};
    const em = cp.emerging_playbook_hours_to_flag || {}, emH = Object.values(em).filter((x) => x !== null);
    const f1 = (v) => (v === null || v === undefined ? "–" : (+v).toLocaleString("en-IN", { maximumFractionDigits: 1 }));
    const pc = (v, dp = 1) => (v === null || v === undefined ? "–" : (v * 100).toFixed(dp) + "%");
    const rows = [["Items to review per day", "items_per_day", f1, "lower"], ["Share of reviewed items that are real scams", "precision_at_budget", pc, "higher"],
      ["Scam money stopped (if warned customers listen)", "money_protected_pct", pc, "higher"], ["Genuine payments interrupted", "friction_rate", (v) => pc(v, 3), "lower"],
      ["Victims protected at some point", "victim_recall", pc, "higher"],
      ["Held payments that are scams – at a realistic scam rate", "hold_precision_reweighted", (v) => pc(v, 2), "higher"]];
    const order = ["B0", "B1", "B2", "B1m", "B2m", "Vyuh"].filter((k) => s[k]);
    const NAMES = { B0: "Rules engine", B1: "Per-payment AI", B2: "Per-payment AI + mule score", B1m: "Per-payment AI, at Vyuh’s friction", B2m: "AI + mule score, at Vyuh’s friction", Vyuh: "Vyuh" };
    const bc = r.budget_curve;
    const curve = bc ? C.lineChart([
      { name: "Rules engine", values: bc.B0, color: "#c41230" }, { name: "Per-payment AI", values: bc.B1, color: "#d07a00", dash: true },
      { name: "AI + mule score", values: bc.B2, color: "#6a3fbf", dash: true }, { name: "Vyuh", values: bc.Vyuh, color: "#1f3c88", bold: true }],
      bc.budgets.map(String), { xlabel: "items an analyst team reviews per day", label: "Precision at different review budgets" }) : "";
    const fair = (r.fairness || {}).age_band || [];
    const rw = r.reweighting || {};
    const camps = (r.per_campaign || []).filter((c) => c.detected);
    app.innerHTML = shell("results", `<h1>Results</h1>
      <p class="lead">Thirty days of test data the models never trained on, including a scam type they never saw. The same payments go through a typical rules engine, per-payment AI models (one of them also knows mule accounts – today’s best practice), and Vyuh.</p>
      <div class="result-hero">
        <div class="panel rh"><div class="q">Items a person must review each day</div><div class="pair"><span class="was">${num(Math.round(b0.items_per_day || 0))}</span><span class="now">${f1(vy.items_per_day)}</span></div><div class="note">rules engine → Vyuh</div></div>
        <div class="panel rh"><div class="q">Of those, real scams</div><div class="pair"><span class="was">${pc(b0.precision_at_budget, 0)}</span><span class="now">${pc(vy.precision_at_budget, 0)}</span></div><div class="note">rules engine → Vyuh</div></div>
        <div class="panel rh"><div class="q">Scam money stopped at the same low friction</div><div class="pair"><span class="was">${pc(b2m.money_protected_pct, 0)}</span><span class="now">${pc(vy.money_protected_pct, 0)}</span></div><div class="note">best per-payment AI (with mule scores) → Vyuh, both disturbing ~1 in ${vy.friction_rate ? num(Math.round(1 / vy.friction_rate)) : "–"} genuine payments</div></div>
        <div class="panel rh"><div class="q">Campaigns flagged before the first 1930 complaint</div><div class="pair"><span class="now">${cp.campaigns_flagged_before_any_complaint ?? "–"}/${cp.detected ?? "–"}</span></div><div class="note">median ${f1(cp.median_hours_before_first_1930_complaint)} h earlier · unseen scam type flagged in ${emH.length ? Math.min(...emH) + "–" + Math.max(...emH) + " h" : "–"}</div></div>
      </div>
      <section class="panel"><h2>Same payments, six ways</h2><div class="scroll"><table>
        <tr><th>Measure</th>${order.map((k) => `<th class="r">${esc(NAMES[k])}</th>`).join("")}<th>Better</th></tr>
        ${rows.map(([l, k, f, better]) => `<tr><td>${l}</td>${order.map((sys) => `<td class="r" ${sys === "Vyuh" ? 'style="background:var(--navy-soft);font-weight:700"' : ""}>${f(s[sys][k])}</td>`).join("")}<td class="muted small">${better}</td></tr>`).join("")}
      </table></div>
      <p class="muted small" style="margin:12px 0 0">The rules engine and the per-payment models protect a similar share of money only by interrupting about ${pc(b0.friction_rate, 0)} of genuine payments. Forced down to Vyuh’s friction, even the model that knows mule accounts stops ${pc(b2m.money_protected_pct)}. ${vy.warning_named_right !== undefined ? `Vyuh’s warnings named the right scam ${pc(vy.warning_named_right, 0)} of the time, a wrong one ${pc(vy.warning_named_wrong, 0)}, and gave the generic warning otherwise (it names a scam only when the match is clear). ` : ""}The last row re-weights genuine payments to an assumed real scam rate of ${rw.assumed_real_scam_rate ? (1 / rw.assumed_real_scam_rate).toLocaleString("en-IN") : "–"} payments per scam payment (our simulation has many more scams, which flatters every system).</p></section>
      <section class="panel" style="margin-top:18px"><h2>Blanket delay or targeted pause? <span class="tag review">RBI discussion paper, April 2026</span></h2>
        <p class="muted small" style="margin-top:-6px">RBI has proposed a one-hour delay on account-to-account transfers of ₹10,000 and above. We ran the proposal, Vyuh and a hybrid on the same test month.</p>
        <div id="policy">${policyHtml(r.policies)}</div></section>
      <div class="grid cols-2" style="margin-top:18px">
        <section class="panel"><h2>Precision at any review budget</h2><p class="muted small" style="margin-top:-6px">Share of items that are real scams when a team reviews the top N items per day.</p>${curve}</section>
        <section class="panel"><h2>Fair to every age group?</h2><p class="muted small" style="margin-top:-6px">Genuine payments held, by the payer’s age band. Age is never a model input; trusted-contact approval is a setting the customer chooses.</p>
          <div class="scroll"><table><tr><th>Age band</th><th class="r">Genuine payments</th><th class="r">Rules engine held</th><th class="r">AI + mule, at Vyuh’s friction</th><th class="r">Vyuh paused</th><th class="r">Trusted contact asked</th><th class="r">Victims’ money saved</th></tr>
          ${fair.map((g) => `<tr><td>${esc(g.group)}</td><td class="r">${num(g.genuine_payments)}</td><td class="r">${pc(g.b0_hold_rate, 2)}</td><td class="r">${pc(g.b2m_hold_rate, 3)}</td><td class="r">${pc(g.vyuh_hold_rate, 3)}</td><td class="r">${g.vyuh_trusted_contact_rate !== undefined ? pc(g.vyuh_trusted_contact_rate, 3) : "–"}</td><td class="r">${g.vyuh_victim_money_protected !== undefined ? pc(g.vyuh_victim_money_protected, 0) : "–"}</td></tr>`).join("") || '<tr><td colspan="7" class="muted">Re-run the evaluation.</td></tr>'}</table></div>
          <p class="muted small" style="margin:8px 0 0">Trusted-contact approval only happens for customers who switched it on (in the simulation, about half of customers aged 60+).</p></section>
      </div>
      <div class="grid cols-2" style="margin-top:18px">
        <section class="panel"><h2>Campaigns</h2><table>
          <tr><td>Scam campaigns in the test month</td><td class="r">${cp.ground_truth_campaigns ?? "–"}</td></tr>
          <tr><td>Detected</td><td class="r">${cp.detected ?? "–"}</td></tr>
          <tr><td>Median victim number when the case opened</td><td class="r">${f1(cp.median_victim_number_at_detection)}</td></tr>
          <tr><td>Cases shown to investigators</td><td class="r">${cp.investigator_cases ?? "–"}</td></tr>
          <tr><td>Members of a case who are real victims</td><td class="r">${pc(cp.mean_case_purity, 0)}</td></tr>
          <tr><td>Victims in a case who belong to its main campaign</td><td class="r">${pc(cp.mean_case_homogeneity, 0)}</td></tr>
          <tr><td>Fewer items than the rules engine</td><td class="r">${cp.alert_reduction_vs_B0 ? num(Math.round(cp.alert_reduction_vs_B0)) + "×" : "–"}</td></tr>
          <tr><td>Hours to flag the unseen scam type</td><td class="r">${Object.entries(em).map(([k, v]) => `${esc(k)}: ${v ?? "–"} h`).join("<br>") || "–"}</td></tr></table>
          ${camps.length ? `<details class="more" style="margin-top:10px"><summary>Each campaign</summary><div class="scroll"><table><tr><th>Campaign</th><th>Scam type</th><th class="r">Victims</th><th class="r">Victim no. at case</th><th class="r">Hours before 1st 1930 complaint</th></tr>
            ${camps.map((c) => `<tr><td>${esc(c.campaign)}</td><td>${esc(pbTitle(c.playbook))}</td><td class="r">${c.victims}</td><td class="r">${c.victim_number_at_detection ?? "–"}</td><td class="r">${c.hours_before_first_1930_complaint ?? "–"}</td></tr>`).join("")}</table></div></details>` : ""}</section>
        <section class="panel"><h2>When scammers adapt</h2><p class="muted small" style="margin-top:-6px">All seven attack modes switched on, no retraining.</p><table>
          <tr><td>Vyuh – money stopped, normal scams</td><td class="r">${pc(rb.vyuh_money_protected_clean)}</td></tr>
          <tr><td>Vyuh – money stopped, scammers adapting</td><td class="r">${pc(rb.vyuh_money_protected_adversarial)}</td></tr>
          <tr><td>AI + mule score at Vyuh’s friction, adapting</td><td class="r">${pc(rb.b2_at_vyuh_friction_money_protected_adversarial ?? rb.b1_at_vyuh_friction_money_protected_adversarial)}</td></tr>
          <tr><td>Campaigns detected, adapting</td><td class="r">${esc(rb.campaigns_detected_adversarial ?? "–")}</td></tr>
          <tr><td>Time to score one payment (typical / slowest 5%)</td><td class="r">${rb.latency_p50_ms ?? "–"} / ${rb.latency_p95_ms ?? "–"} ms</td></tr></table></section>
      </div>
      <p class="honest" style="margin-top:18px"><b>Read these honestly.</b> All data is synthetic and cleaner than reality, so absolute numbers are an upper bound. The meaningful evidence is the comparison on the same payments, the unseen scam type, the adaptive-scammer run and the policy comparison. Report generated ${esc(r.generated_at || "")}${r.settings ? ` · ${num(r.settings.customers)} customers, seed ${r.settings.seed}` : ""}.</p>`);
  }

  // ---------------------------------------------------------------- safety
  async function viewSafety() {
    S.onStatus = null;
    const audit = can("supervisor", "auditor");
    app.innerHTML = shell("safety", `<h1>Safety and privacy</h1>
      <p class="lead">Vyuh produces risk signals for people, never accusations. Nothing stronger than a pause happens without a named person, and every step is recorded.</p>
      <div class="grid cols-2">
        <section class="panel"><h2>Who can do what</h2><ol class="ladder">
          <li class="auto"><span class="lv">R0</span><span>Log and keep watching</span><span class="who-t">automatic</span></li>
          <li class="auto"><span class="lv">R1</span><span>Warning that names the scam trick</span><span class="who-t">automatic</span></li>
          <li class="auto"><span class="lv">R2</span><span>30-minute pause on a payment to a new payee, with ‘Talk to my bank’ – the customer can still continue (after 30 s if on a call)</span><span class="who-t">automatic</span></li>
          <li class="auto"><span class="lv">R2T</span><span>For customers who chose it (e.g. seniors): a family member approves risky payments of ₹50,000+</span><span class="who-t">customer’s own setting</span></li>
          <li class="human"><span class="lv">R3</span><span>Bank calls the customer back</span><span class="who-t">system asks, a person calls</span></li>
          <li class="human"><span class="lv">R4</span><span>Receiving accounts reviewed, 1930 report drafted</span><span class="who-t">investigator, after confirming</span></li>
          <li class="human"><span class="lv">R5</span><span>Pseudonymised package shared with partner banks</span><span class="who-t">supervisor only</span></li></ol></section>
        <section class="panel"><h2>Privacy built in</h2><ul style="margin:0;padding-left:20px">
          <li>Every account number is replaced by a keyed code (HMAC-SHA256) the moment it arrives.</li>
          <li>Payment notes are cut to one word; message text is never read or stored.</li>
          <li>Call and screen-share signals are yes/no flags, not recordings.</li>
          <li>Customers never see model scores, so scammers cannot use the app to probe the system.</li>
          <li>Rejecting a case as ‘legitimate business’ needs a second person to approve.</li>
          <li>No language model makes any decision; every reason traces to a number.</li>
          <li>Warning and pause thresholds are jittered a little for every payment, so test payments cannot find the exact boundary.</li>
          <li>Age is never a model input. Results show friction by age band, and labels younger than 7 days never go into retraining.</li>
          <li>Customers can stop all their own online payments with one switch (kill switch).</li></ul>
          <div id="s-model" style="margin-top:16px"></div></section>
      </div>
      <section class="panel" style="margin-top:18px"><h2>Rollout mode</h2><p class="muted small" style="margin-top:-6px">How a bank would switch Vyuh on: first in shadow, measure, then step up. ${can("supervisor") ? "Supervisors can change it." : "Only a supervisor can change it."}</p><div id="s-modes"></div></section>
      <div class="grid cols-2" style="margin-top:18px">
        <section class="panel"><h2>Model health</h2><div id="s-health"><p class="muted">Loading…</p></div></section>
        <section class="panel"><h2>Drift since training</h2><p class="muted small" style="margin-top:-6px">Population Stability Index of live journey features vs training. Below 0.1 = stable; above 0.25 = investigate. Alarms go to the ML owner, not investigators. Injected test scams are unusual on purpose, and our synthetic bank has only 60 days of history (a real bank has years), so features that count new payees and scam steps creep upward – expect “watch” on some of them.</p><div id="s-drift"><p class="muted">Loading…</p></div></section>
      </div>
      <section class="panel" style="margin-top:18px"><h2>Two-person approvals</h2><div id="s-supp"><p class="muted">Loading…</p></div></section>
      <section class="panel" style="margin-top:18px"><div class="btn-row" style="justify-content:space-between"><h2 style="margin:0">Audit log</h2>
        ${audit ? `<label class="small"><input type="checkbox" id="hideviews" ${S.hideViews ? "checked" : ""}> Hide routine entries (opened case, clock)</label>` : ""}</div>
        <div id="s-audit" style="margin-top:12px">${audit ? '<p class="muted">Loading…</p>' : '<p class="muted">Only supervisors and auditors can read the audit log.</p>'}</div></section>`);
    try {
      const m = await api("/api/model");
      const ok = Object.values(m.hashes_valid || {}).every(Boolean);
      set("s-model", `<div class="ok-banner ${ok ? "good" : "bad"}">${ok ? "✓ Model files match the approved versions" : "✕ A model file does not match – investigate"}</div><p class="muted small" style="margin:0">Model version ${esc(m.metadata.version || "–")}, trained ${esc((m.metadata.trained_at || "").replace("T", " "))}. ${esc(m.metadata.note || "")}</p>`);
    } catch (e) { set("s-model", ""); }
    paintModes();
    await Promise.all([paintSupp(), paintHealth(), paintDrift()]);
    if (audit) await paintAudit();
  }
  function paintModes() {
    const cur = (S.status || {}).mode || "full", sup = can("supervisor");
    set("s-modes", `<div class="modes">${MODES.map(([k, t, d]) => `<label class="mode ${cur === k ? "on" : ""}"><input type="radio" name="mode" value="${k}" ${cur === k ? "checked" : ""} ${sup ? "" : "disabled"} data-mode="${k}"><b>${esc(t)}</b><span>${esc(d)}</span></label>`).join("")}</div>`);
  }
  async function paintHealth() {
    try {
      const m = await api("/api/model"), md = m.metadata || {}, mt = md.metrics || {};
      const row = (n, x) => x ? `<tr><td>${n}</td><td class="r">${x.auc ?? "–"}</td><td class="r">${x.pr_auc ?? "–"}</td><td class="r">${num(x.positives)}</td></tr>` : "";
      let lab = "";
      if (can("supervisor")) { try { const L = await api("/api/labels/training"); lab = `<p class="small muted" style="margin:10px 0 0">Investigator labels ready for retraining: ${num(L.eligible)} · held back (younger than ${L.min_age_days} days): ${num(L.held_back)}.</p>`; } catch (e) { /* optional */ } }
      set("s-health", `<table><tr><th>Model (validation days, never used for training)</th><th class="r">AUC</th><th class="r">PR-AUC</th><th class="r">Positives</th></tr>
        ${row("Journey (victim side)", mt.journey)}${row("Receiving account (mule)", mt.collector)}${row("Fused risk", mt.fused_risk)}${row("Baseline B1 per payment", mt.txn_baseline_b1)}${row("Baseline B2 + mule score", mt.txn_mule_baseline_b2)}</table>
        <p class="small muted" style="margin:10px 0 0">Campaign linking: ${esc(md.community_method || "louvain")} · LightGBM ${esc(md.lightgbm_version || "")} · version ${esc(md.version || "–")}</p>${lab}`);
    } catch (e) { set("s-health", `<p class="muted">${esc(e.message)}</p>`); }
  }
  async function paintDrift() {
    try {
      const d = await api("/api/monitoring/drift");
      if (d.status !== "ok") { set("s-drift", `<p class="muted">Collecting live payments (${num(d.samples)} so far, 1,000 needed). Press Play for a while.</p>`); return; }
      const top = d.features.filter((f) => f.psi !== null).sort((a, b) => b.psi - a.psi).slice(0, 6);
      set("s-drift", `<div class="ok-banner ${d.overall === "stable" ? "good" : "bad"}">${d.overall === "stable" ? "✓ Stable" : d.overall === "watch" ? "Watch" : "Investigate"} – worst PSI ${d.worst_psi} over ${num(d.samples)} live payments</div>
        <table><tr><th>Feature</th><th class="r">PSI</th><th>State</th></tr>${top.map((f) => `<tr><td>${esc(f.feature.replaceAll("_", " "))}</td><td class="r">${f.psi}</td><td>${esc(f.state)}</td></tr>`).join("")}</table>`);
    } catch (e) { set("s-drift", `<p class="muted">${esc(e.message)}</p>`); }
  }
  async function paintSupp() {
    try {
      const rows = await api("/api/suppressions");
      set("s-supp", rows.length ? `<table><tr><th>Account</th><th>Case</th><th>Asked by</th><th>Status</th><th>Ends (simulated)</th><th></th></tr>${rows.map((r) => `<tr><td>${esc(short(r.account))}</td><td><a href="#/case/${esc(r.case_id)}">${esc(r.case_id)}</a></td><td>${esc(r.requested_by)}</td><td>${esc(r.status)}${r.approved_by ? " by " + esc(r.approved_by) : ""}</td><td class="small">${r.status === "approved" ? fmtT(r.until_sim) : "–"}</td>
        <td class="r">${r.status === "pending" && can("supervisor") ? `<button class="btn ok" data-act="supp" data-id="${r.id}" data-ok="1">Approve</button> <button class="btn" data-act="supp" data-id="${r.id}" data-ok="0">Decline</button>` : r.status === "approved" && can("supervisor") ? `<button class="btn" data-act="revoke" data-id="${r.id}">Lift now</button>` : ""}</td></tr>`).join("")}</table>
        <p class="muted small" style="margin:10px 0 0">The person who asked cannot approve their own request. Approved suppressions end automatically after 30 simulated days, or a supervisor can lift them early.</p>`
        : '<p class="muted" style="margin:0">No requests. They appear when someone rejects a case as ‘legitimate business’.</p>');
    } catch (e) { set("s-supp", `<p class="muted">${esc(e.message)}</p>`); }
  }
  async function paintAudit() {
    try {
      const a = await api("/api/audit");
      const ents = a.entries.filter((e) => !(S.hideViews && (e.action === "case_viewed" || e.action.startsWith("replay_")))).slice(0, 60);
      set("s-audit", `<div class="ok-banner ${a.verify.valid ? "good" : "bad"}">${a.verify.valid ? `✓ Chain verified – all ${num(a.verify.entries)} entries intact, nothing edited or removed` : `✕ Chain broken at entry ${a.verify.broken_at} – someone may have edited the log`}</div>
        <div class="scroll"><table><tr><th>When</th><th>Who</th><th>What</th><th>On</th></tr>${ents.map((e) => `<tr><td class="small">${new Date(e.ts * 1000).toLocaleString("en-IN")}</td><td>${esc(e.actor)} <span class="muted small">${esc(e.role)}</span></td><td>${esc(ACTION_WORDS[e.action] || e.action.replaceAll("_", " "))}</td><td>${esc(e.target)}</td></tr>`).join("")}</table></div>`);
    } catch (e) { set("s-audit", `<p class="muted">${esc(e.message)}</p>`); }
  }

  // ---------------------------------------------------------------- actions (one delegated listener)
  async function replay(action, extra = {}) {
    const speed = +($("tb-speed")?.value || 300);
    await api("/api/replay", { method: "POST", body: { action, speed, minutes: 60, ...extra } });
    await refreshStatus();
  }
  document.addEventListener("click", async (ev) => {
    const go = ev.target.closest("[data-go]");
    if (go) { location.hash = go.dataset.go; return; }
    const el = ev.target.closest("[data-act]"); if (!el) return;
    const act = el.dataset.act;
    if (el.tagName === "A") ev.preventDefault();
    try {
      if (act === "logout") logout();
      else if (act === "role") { S.role = el.dataset.r; document.querySelectorAll(".role").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.r === S.role))); $("pw").focus(); }
      else if (act === "big") { S.big = !S.big; pref.set("vyuh_big", S.big); document.documentElement.classList.toggle("big", S.big); el.textContent = S.big ? "A" : "A+"; el.setAttribute("aria-pressed", String(S.big)); }
      else if (act === "play") { const run = !(S.status && S.status.running); await replay(run ? "start" : "pause"); toast(run ? "Clock running." : "Clock paused."); }
      else if (act === "step") { await replay("step"); toast("Jumped one simulated hour."); }
      else if (act === "reset") {
        if (!confirm("Reset the demo? This run's cases and protections are cleared. The audit log is kept.")) return;
        await replay("reset"); S.board = null; toast("Demo reset. Press Play to start again."); render();
      } else if (act === "inject") {
        const r = await api("/api/simulate/inject", { method: "POST", body: { playbook: $("pb").value, victims: Math.max(3, Math.min(60, +$("nv").value || 8)), senior_first: !!($("senior") && $("senior").checked) } });
        toast(`${r.campaign_id} launched against ${r.victims} customers.${S.status && !S.status.running ? " Press Play – the clock is paused." : ""}`);
        refreshStatus();
      } else if (act === "adversary") {
        const on = !(S.status && S.status.adversary);
        await api("/api/simulate/adversary", { method: "POST", body: { on } });
        toast(on ? "Scammers now rotate accounts, randomise amounts and drop tell-tale words." : "Scammers back to normal.");
        refreshStatus();
      } else if (act === "phone") {
        await api("/api/phone/respond", { method: "POST", body: { choice: el.dataset.c } });
        toast({ cancel: "Payment cancelled – money saved.", talk: "Payment stopped. The case now shows ‘customer asked for a call-back’.", genuine: "Paid. This payee is now trusted for this customer; the case is marked for review.", continue: "The customer chose to continue.", approve: "The trusted contact approved – payment sent.", decline: "The trusted contact declined – money saved." }[el.dataset.c]);
        set("phone", phoneHtml(await api("/api/phone")));
      } else if (act === "lang") {
        S.lang = S.lang === "hi" ? "en" : "hi"; pref.set("vyuh_lang", S.lang);
        if ($("phone") && WORK()) set("phone", phoneHtml(await api("/api/phone")));
      } else if (act === "lock") {
        const on = el.getAttribute("aria-checked") !== "true";
        await api("/api/phone/killswitch", { method: "POST", body: { on } });
        toast(on ? "Kill switch on: every online payment from this customer is blocked." : "Kill switch off.");
        set("phone", phoneHtml(await api("/api/phone")));
      } else if (act === "filter") {
        S.filter = el.dataset.f;
        render();
      } else if (act === "decide") {
        const id = S.caseData.case_id, d = el.dataset.d;
        const reason = d === "reject" ? $("reason").value : "";
        await api(`/api/cases/${encodeURIComponent(id)}/decision`, { method: "POST", body: { decision: d, reason } });
        toast({ confirm: "Scam confirmed. All actions are unlocked.", monitor: "Kept under review.", reject: reason === "legitimate_business" ? "Rejected. A second person must approve the suppression on the Safety tab." : "Rejected.", close: "Closed." }[d]);
        await loadCase(id);
      } else if (act === "action") {
        const r = await api(`/api/cases/${encodeURIComponent(S.caseData.case_id)}/actions`, { method: "POST", body: { action: el.dataset.a } });
        toast(r.detail); await loadCase(S.caseData.case_id);
      } else if (act === "report") {
        const id = S.caseData.case_id, fmt = el.dataset.fmt;
        const res = await fetch(`/api/cases/${encodeURIComponent(id)}/report?format=${fmt}`, { headers: { authorization: "Bearer " + S.token } });
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || "Could not create the report"); }
        const blob = fmt === "csv" ? await res.blob() : new Blob([JSON.stringify(await res.json(), null, 2)], { type: "application/json" });
        const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${id}-1930-report.${fmt}`; a.click();
        toast(fmt === "csv" ? "Money trail downloaded (CSV)." : "1930 report downloaded: victims, payments, receiving accounts and the hold list.");
      } else if (act === "export") {
        const id = S.caseData.case_id, r = await api(`/api/cases/${encodeURIComponent(id)}/export`);
        const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(r, null, 2)], { type: "application/json" })); a.download = `${id}-package.json`; a.click();
        toast("Case package downloaded.");
      } else if (act === "approvepb") {
        const r = await api(`/api/playbooks/${encodeURIComponent(S.caseData.case_id)}/approve`, { method: "POST" });
        S.playbooks = []; toast(`Added to the scam library as ${r.name}. Future victims get a named warning.`);
      } else if (act === "truth") {
        const r = await api(`/api/cases/${encodeURIComponent(S.caseData.case_id)}/truth`);
        const real = Object.entries(r.true_victims_by_campaign).map(([k, v]) => `${v} real victims of ${esc(k)}`).join(", ") || "no real victims";
        set("c-answer", `<div class="answer"><b>Simulator answer key</b> (the detector never sees this): ${real}; ${r.members_not_victims} linked customer(s) are not victims; ${r.collectors_confirmed} of ${r.collectors_total} receiving accounts are real mule accounts.</div>`);
      } else if (act === "align") { S.aligned = !S.aligned; paintTimeline(); }
      else if (act === "revoke") {
        await api(`/api/suppressions/${el.dataset.id}/revoke`, { method: "POST" });
        toast("Suppression lifted. The account is scored normally again."); await paintSupp();
      } else if (act === "supp") {
        await api(`/api/suppressions/${el.dataset.id}`, { method: "POST", body: { approve: el.dataset.ok === "1" } });
        toast(el.dataset.ok === "1" ? "Approved." : "Declined."); await paintSupp();
      }
    } catch (e) { toast(e.message); }
  });
  document.addEventListener("change", async (ev) => {
    const id = ev.target.id;
    try {
      if (id === "tb-speed") { await api("/api/replay", { method: "POST", body: { action: S.status && S.status.running ? "start" : "pause", speed: +ev.target.value } }); toast("Speed changed."); }
      else if (id === "pb") set("pbdesc", scamDesc(ev.target.value));
      else if (id === "hideviews") { S.hideViews = ev.target.checked; paintAudit(); }
      else if (ev.target.dataset && ev.target.dataset.mode) { await api("/api/mode", { method: "POST", body: { mode: ev.target.value } }); toast("Rollout mode: " + (MODES.find((m) => m[0] === ev.target.value) || [0, ev.target.value])[1]); await refreshStatus(); paintModes(); }
    } catch (e) { toast(e.message); }
  });
  document.addEventListener("input", (ev) => {
    if (ev.target.id === "cr" && S.report) { S.cancelIdx = +ev.target.value; set("policy", policyHtml(S.report.policies)); const el = $("cr"); if (el) el.focus(); }
  });
  document.addEventListener("keydown", async (ev) => {
    if (!S.token || !WORK() || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    if (ev.target.closest("input, select, textarea, button")) return;
    try {
      if (ev.code === "Space") { ev.preventDefault(); const run = !(S.status && S.status.running); await replay(run ? "start" : "pause"); toast(run ? "Clock running." : "Clock paused."); }
      else if (ev.key === "j" || ev.key === "J") { await replay("step"); toast("Jumped one simulated hour."); }
    } catch (e) { toast(e.message); }
  });

  // ---------------------------------------------------------------- router
  async function render() {
    clearTimers(); S.onStatus = null;
    if (!S.token) { viewLogin(); return; }
    if (!S.statusTimer) S.statusTimer = setInterval(refreshStatus, 1500);
    if (!S.status) await refreshStatus();
    const [route, arg] = location.hash.replace(/^#\//, "").split("/");
    try {
      switch (route || "live") {
        case "login": case "live": await viewLive(); break;
        case "cases": await viewCases(); break;
        case "case": await viewCase(decodeURIComponent(arg || "")); break;
        case "results": await viewResults(); break;
        case "safety": await viewSafety(); break;
        default: location.hash = "#/live"; return;
      }
      paintTopbar();
      window.scrollTo(0, 0);
    } catch (e) { app.innerHTML = shell(route, `<div class="panel empty"><b>This page could not load</b>${esc(e.message)}</div>`); }
  }
  window.addEventListener("hashchange", render);
  render();
})();
