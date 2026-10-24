/* Vyuh Test Lab: run Vyuh on input someone else chose (a judge's file, a scenario
   they build, or one payment they type in). Separate from the live demo.
   CSP-safe: no inline handlers; clicks are read from data-lab attributes. */
(function () {
  "use strict";
  const C = window.VyuhCharts;
  const { esc, inr } = C;
  const num = (v) => Number(v || 0).toLocaleString("en-IN");
  const pct = (v, dp = 1) => (v === null || v === undefined ? "–" : (v * 100).toFixed(dp) + "%");
  const $ = (id) => document.getElementById(id);
  const set = (id, html) => { const el = $(id); if (el) el.innerHTML = html; };
  let X = null;                  // context from app.js: { app, shell, api, toast, WORK, token }
  const L = { res: null, filter: "flagged", page: 0, lastCsv: "", lastName: "" };

  const SCEN = [
    ["task_scam", "Part-time task scam"], ["digital_arrest", "Digital arrest (fake police on video call)"],
    ["investment", "Fake investment group"], ["kyc_remote", "Fake KYC + remote-access app"],
    ["genuine_new_payee", "GENUINE customer: pays a new landlord (no scam)"],
  ];
  const LEVEL_CLASS = { R0: "", R1: "warn", R2: "stop", R2T: "stop", R3: "stop" };

  // ---------------------------------------------------------------- page
  function view(ctx) {
    X = ctx;
    const work = X.WORK();
    X.app.innerHTML = X.shell("lab", `<h1>Test Lab</h1>
      <p class="lead">Try Vyuh on <b>your own input</b>: upload a file of payments, build an attack scenario, or type in one payment.
        It runs in a separate, fresh copy of the detector – it knows nothing about the demo bank's customers and changes nothing in the live demo.
        Add a <code>label</code> column (scam / genuine) and Vyuh reports what it caught, what it missed and every genuine payment it disturbed.</p>
      ${work ? "" : '<div class="note-bar">You are signed in as auditor: you can read this page but not run tests.</div>'}
      <div class="grid cols-3 lab-inputs">
        <section class="panel"><h2>1 · Upload a file</h2>
          <p class="muted small">CSV, one row per payment. Needs <b>timestamp, payer, payee, amount</b>; all other columns are optional.</p>
          <input type="file" id="lab-file" accept=".csv,text/csv" aria-label="CSV file of payments" ${work ? "" : "disabled"}>
          <label class="small lab-row">Treat the first
            <select id="lab-hist" aria-label="History days"><option value="">auto</option><option value="0">0</option><option value="3">3</option><option value="7">7</option><option value="10">10</option></select>
            days as normal history (not scored for accuracy)</label>
          <div class="btn-row" style="margin-top:10px">
            <button class="btn primary" data-lab="run-file" ${work ? "" : "disabled"}>Run file</button>
            <button class="btn" data-lab="template">Download template</button></div>
          <div id="lab-samples" class="small" style="margin-top:12px"></div></section>
        <section class="panel"><h2>2 · Build an attack</h2>
          <p class="muted small">Vyuh writes 10 days of normal life for each customer, then the attack you choose.</p>
          <label class="lab-f">Scenario<select id="sc-kind">${SCEN.map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select></label>
          <div class="lab-2">
            <label class="lab-f">Customers<input id="sc-victims" type="number" min="1" max="30" value="6"></label>
            <label class="lab-f">Payments each<input id="sc-steps" type="number" min="1" max="6" value="4"></label>
            <label class="lab-f">First amount ₹<input id="sc-amount" type="number" min="100" step="100" value="2000"></label>
            <label class="lab-f">Each next ×<input id="sc-growth" type="number" min="1" max="5" step="0.5" value="2"></label></div>
          <div class="lab-checks small">
            <label><input type="checkbox" id="sc-call"> Victim is on a call while paying</label>
            <label><input type="checkbox" id="sc-screen"> Screen sharing on</label>
            <label><input type="checkbox" id="sc-rotate"> Attacker: new receiving account for every payment</label>
            <label><input type="checkbox" id="sc-hide"> Attacker: no tell-tale words in payment notes</label>
            <label><input type="checkbox" id="sc-senior"> First customer is 60+ with a trusted contact</label></div>
          <button class="btn primary" data-lab="run-scenario" ${work ? "" : "disabled"}>Run scenario</button></section>
        <section class="panel"><h2>3 · Score one payment</h2>
          <p class="muted small">A dry run: shows what Vyuh would do, changes nothing. If the payer appears in the last file or scenario, their history is used.</p>
          <div class="lab-2">
            <label class="lab-f">Payer<input id="p-payer" value="customer_01" maxlength="64"></label>
            <label class="lab-f">Payee<input id="p-payee" value="unknown_upi_77" maxlength="64"></label>
            <label class="lab-f">Amount ₹<input id="p-amount" type="number" min="1" value="25000"></label>
            <label class="lab-f">Payment note<input id="p-remark" value="verification" maxlength="60"></label>
            <label class="lab-f">Payee is<select id="p-ptype"><option value="individual">a person</option><option value="business">a business</option></select></label>
            <label class="lab-f">Payee account age (days)<input id="p-page" type="number" min="0" value="5"></label></div>
          <div class="lab-checks small">
            <label><input type="checkbox" id="p-call" checked> On a call</label>
            <label><input type="checkbox" id="p-screen"> Screen sharing</label>
            <label><input type="checkbox" id="p-remote"> Remote-access app open</label>
            <label><input type="checkbox" id="p-paste"> Payee pasted, not picked from contacts</label></div>
          <button class="btn primary" data-lab="score" ${work ? "" : "disabled"}>Score payment</button>
          <div id="p-out" style="margin-top:12px"></div></section>
      </div>
      <div id="lab-out" style="margin-top:18px">${L.res ? "" : '<div class="panel empty"><b>No test run yet</b>Upload a file, run a sample, or build an attack above.</div>'}</div>`);
    if (L.res) paint();
    paintSamples();
  }

  async function paintSamples() {
    try {
      const list = await X.api("/api/lab/samples");
      if (!list.length) { set("lab-samples", '<span class="muted">No sample files on the server (run <code>python -m scripts.make_lab_samples</code>).</span>'); return; }
      set("lab-samples", `<b>Ready-made test files</b> (synthetic test month, never used for training):<ul class="lab-samples">${list.map((s) =>
        `<li><span>${esc(s.name.replace(/^sample_\d+_/, "").replace(/_/g, " ").replace(/\.csv$/, ""))}</span>
          <button class="btn sm" data-lab="sample-run" data-name="${esc(s.name)}" ${X.WORK() ? "" : "disabled"}>Run</button>
          <button class="btn sm" data-lab="sample-get" data-name="${esc(s.name)}">Download</button></li>`).join("")}</ul>`);
    } catch (e) { set("lab-samples", ""); }
  }

  // ---------------------------------------------------------------- results
  function paint() {
    const r = L.res, s = r.summary, m = r.metrics;
    const probs = (r.problems || []).length ? `<div class="note-bar warn">${r.problems.length} row problem(s): ${r.problems.slice(0, 4).map(esc).join(" · ")}${r.problems.length > 4 ? " …" : ""}</div>` : "";
    const head = `<div class="btn-row" style="justify-content:space-between;margin-bottom:10px"><h2 style="margin:0">Result: ${esc(L.lastName)}</h2>
      ${r.csv ? '<button class="btn" data-lab="dl-scenario">Download this test as CSV</button>' : ""}</div>${probs}
      <p class="muted small" style="margin-top:0">${num(s.payments)} payments by ${num(s.customers)} customers, ${esc(s.from)} → ${esc(s.to)}.
        ${s.history_days ? `First ${s.history_days} day(s) = normal history (${num(s.history_rows)} rows, not counted in accuracy).` : "No history days: every payee is new to its payer."}</p>`;
    const tiles = `<div class="grid cols-4">
      <div class="panel stat"><div class="k">Vyuh flagged</div><div class="v">${num(s.flagged)}</div><div class="d">payments warned or paused</div></div>
      <div class="panel stat scam"><div class="k">Rules engine alerts</div><div class="v">${num(s.rules_engine_alerts)}</div><div class="d">same payments, typical rules</div></div>
      <div class="panel stat"><div class="k">Cases for an investigator</div><div class="v">${num(r.cases.length)}</div><div class="d">one per linked scam network or lone victim</div></div>
      <div class="panel stat safe"><div class="k">Paused (30 min)</div><div class="v">${num((s.levels.R2 || 0) + (s.levels.R2T || 0) + (s.levels.R3 || 0))}</div><div class="d">${num(s.levels.R1 || 0)} warning(s) only</div></div></div>`;
    X.app.querySelector("#lab-out").innerHTML = head + tiles + (m ? metricsHtml(m) : `<div class="note-bar" style="margin-top:14px">No <code>label</code> column, so accuracy cannot be measured. Add scam / genuine labels to see what Vyuh got right and wrong.</div>`)
      + casesHtml(r.cases) + rowsHtml() + privacyHtml(r.privacy);
  }

  function metricsHtml(m) {
    const row = (name, x, cls) => `<tr class="${cls || ""}"><td><b>${name}</b></td><td class="r">${num(x.tp)} / ${num(x.tp + x.fn)}</td><td class="r">${pct(x.recall)}</td>
      <td class="r">${num(x.fp)} / ${num(x.fp + x.tn)}</td><td class="r">${pct(x.false_positive_rate, 3)}</td><td class="r">${pct(x.precision)}</td></tr>`;
    const miss = m.missed.map((x) => `<tr><td class="small">${esc(x.ts)}</td><td>${esc(x.payer)}</td><td>${esc(x.payee)}</td><td class="r">${inr(x.amount)}</td><td class="r">${x.risk.toFixed(2)}</td><td class="small">${esc(x.why_missed || "")}</td></tr>`).join("");
    const fps = m.false_positives.map((x) => `<tr><td class="small">${esc(x.ts)}</td><td>${esc(x.payer)}</td><td>${esc(x.payee)}</td><td class="r">${inr(x.amount)}</td><td class="r">${x.risk.toFixed(2)}</td><td>${esc(x.level_text)}</td><td class="small">${esc((x.reasons || []).slice(0, 2).join("; "))}</td></tr>`).join("");
    return `<section class="panel" style="margin-top:18px"><h2>Accuracy on your labels</h2>
      <p class="muted small" style="margin-top:-6px">${num(m.labelled_payments)} labelled payments: ${num(m.scam_payments)} scam, ${num(m.genuine_payments)} genuine. Counted after the history days.</p>
      <div class="scroll"><table><tr><th>System</th><th class="r">Scam payments caught</th><th class="r">Catch rate</th><th class="r">Genuine payments disturbed</th><th class="r">False-positive rate</th><th class="r">Flagged that were scams</th></tr>
        ${row("Vyuh – warning or pause", m.vyuh_flag, "vy")}${row("Vyuh – paused only", m.vyuh_pause)}${row("Rules engine (B0)", m.rules_engine)}</table></div>
      <div class="grid cols-3" style="margin-top:12px">
        <div class="panel stat safe"><div class="k">Victims protected at least once</div><div class="v">${num(m.victims_flagged)} / ${num(m.victims)}</div></div>
        <div class="panel stat"><div class="k">Genuine customers disturbed</div><div class="v">${num(m.genuine_customers_disturbed)} / ${num(m.genuine_customers)}</div></div>
        <div class="panel stat"><div class="k">Scam money in flagged payments</div><div class="v">${m.scam_money ? pct(m.scam_money_flagged / m.scam_money, 0) : "–"}</div><div class="d">${inr(m.scam_money_flagged)} of ${inr(m.scam_money)}</div></div></div>
      <h3 style="margin-top:18px">Where Vyuh failed</h3>
      <details class="more" ${m.missed_total ? "open" : ""}><summary>Missed scam payments (${num(m.missed_total)})${m.missed_total > 50 ? " – first 50" : ""}</summary>
        ${m.missed_total ? `<div class="scroll"><table><tr><th>Time</th><th>Payer</th><th>Payee</th><th class="r">Amount</th><th class="r">Risk</th><th>Why it was missed</th></tr>${miss}</table></div>` : '<p class="muted">None.</p>'}</details>
      <details class="more" ${m.false_positives_total ? "open" : ""}><summary>Genuine payments flagged by mistake (${num(m.false_positives_total)})${m.false_positives_total > 50 ? " – first 50" : ""}</summary>
        ${m.false_positives_total ? `<div class="scroll"><table><tr><th>Time</th><th>Payer</th><th>Payee</th><th class="r">Amount</th><th class="r">Risk</th><th>Action</th><th>Reasons shown</th></tr>${fps}</table></div>
        <p class="muted small">A genuine customer who is paused can tap ‘This is me – it’s genuine’: the payment goes through at once and the payee is never paused again.</p>` : '<p class="muted">None.</p>'}</details>
    </section>`;
  }

  function casesHtml(cases) {
    if (!cases.length) return `<section class="panel" style="margin-top:18px"><h2>Cases</h2><p class="muted" style="margin:0">No case formed. A case needs linked victims (same receiving or cash-out account, or the same journey shape) or one very high-risk victim.</p></section>`;
    return `<section class="panel" style="margin-top:18px"><h2>Cases an investigator would get (${cases.length})</h2>
      <p class="muted small" style="margin-top:-6px">The scam-type name is the closest script in Vyuh’s library; on hand-made data it can be approximate.</p>
      <div class="grid cols-2">${cases.map((c) => `<div class="panel lab-case">
        <div><b>${esc(c.case_id)}</b> ${c.emerging ? '<span class="tag emerging">Emerging</span>' : ""} ${c.kind === "lone" ? '<span class="tag lone">Single victim</span>' : ""}</div>
        <div class="lab-ct">${esc(c.emerging ? c.title : c.kind === "lone" ? "Single high-risk victim" : "Scam network")}${!c.emerging && c.title ? ` <span class="muted small">· closest script: ${esc(c.title)}</span>` : ""}</div>
        <div class="small">${num(c.victims.length)} victim(s) · ${num(c.receiving_accounts.length)} receiving account(s) · confidence ${pct(c.confidence, 0)} · first raised ${esc(c.first_seen)}</div>
        <ul class="small">${c.why.slice(0, 4).map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
        <div class="small muted">Victims: ${esc(c.victims.slice(0, 8).join(", "))}${c.victims.length > 8 ? " …" : ""}<br>Receiving: ${esc(c.receiving_accounts.slice(0, 6).join(", ") || "–")}</div></div>`).join("")}</div></section>`;
  }

  function rowsHtml() {
    const r = L.res, f = L.filter;
    const pick = {
      flagged: (x) => x.flagged, all: () => true, scam: (x) => x.label === 1, missed: (x) => x.label === 1 && !x.flagged && !x.history,
      fp: (x) => x.label === 0 && x.flagged && !x.history, rules: (x) => x.rules_engine_alert,
    }[f] || (() => true);
    const rows = r.rows.filter((x) => x.is_payment && pick(x));
    const per = 25, pages = Math.max(1, Math.ceil(rows.length / per)), p = Math.min(L.page, pages - 1);
    const lab = (x) => (x.label === 1 ? '<span class="tag scam-t">scam</span>' : x.label === 0 ? '<span class="tag lone">genuine</span>' : "");
    const body = rows.slice(p * per, p * per + per).map((x) => `<tr class="${x.history ? "hist" : ""}"><td class="small">${esc(x.ts)}</td><td>${esc(x.payer)}</td><td>${esc(x.payee)}</td>
      <td class="r">${inr(x.amount)}</td><td>${lab(x)}</td><td class="r">${x.risk.toFixed(2)}</td>
      <td><span class="lvl ${LEVEL_CLASS[x.level] || ""}">${esc(x.level_text)}</span></td><td class="small">${x.rules_engine_alert ? "alert" : ""}</td>
      <td class="small">${esc(x.reasons.slice(0, 2).join("; "))}${x.why_missed ? `<div class="muted">${esc(x.why_missed)}</div>` : ""}</td></tr>`).join("");
    const opts = [["flagged", "Flagged by Vyuh"], ["all", "All payments"], ["scam", "Labelled scam"], ["missed", "Missed scams"], ["fp", "Genuine flagged"], ["rules", "Rules-engine alerts"]];
    return `<section class="panel" style="margin-top:18px"><div class="btn-row" style="justify-content:space-between"><h2 style="margin:0">Every payment</h2>
      <span class="btn-row"><select id="lab-filter" aria-label="Which payments">${opts.map(([k, l]) => `<option value="${k}" ${k === f ? "selected" : ""}>${l}</option>`).join("")}</select>
      <span class="small muted">${num(rows.length)} rows · page ${p + 1}/${pages}</span>
      <button class="btn sm" data-lab="page" data-d="-1" ${p === 0 ? "disabled" : ""}>‹</button><button class="btn sm" data-lab="page" data-d="1" ${p >= pages - 1 ? "disabled" : ""}>›</button></span></div>
      <div class="scroll" style="margin-top:10px"><table class="lab-rows"><tr><th>Time</th><th>Payer</th><th>Payee</th><th class="r">Amount</th><th>Label</th><th class="r">Risk</th><th>Vyuh action</th><th>Rules</th><th>Reasons</th></tr>
      ${body || '<tr><td colspan="9" class="muted">No payments in this view.</td></tr>'}</table></div>
      <p class="muted small">Grey rows are history days. Risk 0.60+ = warning, 0.85+ to a new payee of ₹5,000+ = 30-minute pause.</p></section>`;
  }

  function privacyHtml(pv) {
    if (!pv) return "";
    return `<section class="panel" style="margin-top:18px"><h2>What Vyuh actually stored</h2>
      <div class="scroll"><table><tr><th></th><th>In your file</th><th>What the detector sees</th></tr>
      <tr><td>Account id</td><td><code>${esc(pv.example_raw)}</code></td><td><code>${esc(pv.example_stored)}</code> (keyed hash)</td></tr>
      ${pv.remark_raw ? `<tr><td>Payment note</td><td><code>${esc(pv.remark_raw)}</code></td><td><code>${esc(pv.remark_stored)}</code> (first word only)</td></tr>` : ""}</table></div>
      <p class="muted small">${esc(pv.note)}</p></section>`;
  }

  function oneHtml(o) {
    const cls = LEVEL_CLASS[o.level] || "";
    const bar = Math.round(o.risk * 100);
    return `<div class="lab-one ${cls}"><div class="btn-row" style="justify-content:space-between"><b class="lvl ${cls}">${esc(o.level_text)}</b><span class="small muted">${o.latency_ms} ms</span></div>
      <div class="lab-bar" role="img" aria-label="Risk ${bar} percent"><i style="width:${bar}%"></i><s style="left:${o.thresholds.warn * 100}%"></s><s style="left:${o.thresholds.pause * 100}%"></s></div>
      <div class="small">Risk <b>${o.risk.toFixed(2)}</b> (warn ≥ ${o.thresholds.warn}, pause ≥ ${o.thresholds.pause} for a new payee and ₹${num(o.thresholds.pause_min_amount)}+) · journey ${o.journey_score.toFixed(2)} · payee ${o.payee_score.toFixed(2)}</div>
      ${o.reasons.length ? `<ul class="small">${o.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${o.message ? `<div class="small"><b>Customer sees:</b> ${esc(o.message)}${o.message_hi ? `<br><span lang="hi">${esc(o.message_hi)}</span>` : ""}</div>` : ""}
      <div class="small muted">${o.history_used ? "Used this payer's history from the last test." : "No history for this payer: Vyuh sees a single payment, so it relies on the payment itself and the payee. Scams are clearer as a journey – try ‘Build an attack’."} Rules engine: ${o.rules_engine_alert ? "would alert" : "no alert"}.</div></div>`;
  }

  // ---------------------------------------------------------------- actions
  function download(name, text, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: type || "text/csv" }));
    a.download = name; a.click();
  }
  async function getText(path) {
    const res = await fetch(path, { headers: { authorization: "Bearer " + X.token() } });
    if (!res.ok) throw new Error("Download failed");
    return res.text();
  }
  function busy(on, msg) {
    document.querySelectorAll("[data-lab^='run'],[data-lab='sample-run'],[data-lab='score']").forEach((b) => (b.disabled = on || !X.WORK()));
    if (on) set("lab-out", `<div class="panel empty"><b>${esc(msg || "Running…")}</b>Every payment is scored in time order and the linker runs every 15 simulated minutes.</div>`);
  }
  async function runCsv(text, name) {
    const hv = $("lab-hist") ? $("lab-hist").value : "";
    busy(true, `Running ${name}…`);
    try {
      L.res = await X.api("/api/lab/run", { method: "POST", body: { csv: text, filename: name, history_days: hv === "" ? null : +hv } });
      L.lastName = name; L.page = 0; L.filter = L.res.metrics ? "flagged" : "flagged";
      paint(); X.toast(`${name}: ${num(L.res.summary.payments)} payments scored.`);
      $("lab-out").scrollIntoView({ behavior: "smooth" });
    } catch (e) { set("lab-out", `<div class="panel empty"><b>Could not run this file</b>${esc(e.message)}</div>`); }
    finally { busy(false); }
  }
  const chk = (id) => !!($(id) && $(id).checked);

  document.addEventListener("click", async (ev) => {
    const el = ev.target.closest("[data-lab]"); if (!el || !X) return;
    const a = el.dataset.lab;
    try {
      if (a === "template") download("vyuh_test_template.csv", await getText("/api/lab/template"));
      else if (a === "sample-get") download(el.dataset.name, await getText("/api/lab/samples/" + encodeURIComponent(el.dataset.name)));
      else if (a === "sample-run") await runCsv(await getText("/api/lab/samples/" + encodeURIComponent(el.dataset.name)), el.dataset.name);
      else if (a === "run-file") {
        const f = $("lab-file").files[0];
        if (!f) { X.toast("Choose a CSV file first."); return; }
        if (f.size > 8 * 1024 * 1024) { X.toast("File is larger than 8 MB."); return; }
        await runCsv(await f.text(), f.name);
      } else if (a === "run-scenario") {
        const body = {
          kind: $("sc-kind").value, victims: +$("sc-victims").value || 6, steps: +$("sc-steps").value || 4,
          first_amount: +$("sc-amount").value || 2000, growth: +$("sc-growth").value || 2,
          on_call: chk("sc-call"), screen_share: chk("sc-screen"), new_account_each_payment: chk("sc-rotate"),
          hide_words: chk("sc-hide"), senior_with_trusted_contact: chk("sc-senior"),
        };
        busy(true, "Building and running the scenario…");
        try {
          L.res = await X.api("/api/lab/scenario", { method: "POST", body });
          L.lastName = L.res.scenario.title; L.page = 0; L.filter = "all";
          paint(); X.toast("Scenario scored.");
          $("lab-out").scrollIntoView({ behavior: "smooth" });
        } finally { busy(false); }
      } else if (a === "dl-scenario" && L.res && L.res.csv) download("vyuh_scenario.csv", L.res.csv);
      else if (a === "score") {
        const body = {
          payer: $("p-payer").value.trim(), payee: $("p-payee").value.trim(), amount: +$("p-amount").value,
          remark: $("p-remark").value, call_active: chk("p-call") ? 1 : 0, screen_share: chk("p-screen") ? 1 : 0,
          remote_app: chk("p-remote") ? 1 : 0, paste_payee: chk("p-paste") ? 1 : 0,
          payee_type: $("p-ptype").value, payee_age_days: +$("p-page").value || 0,
        };
        set("p-out", oneHtml(await X.api("/api/lab/score", { method: "POST", body })));
      } else if (a === "page") { L.page = Math.max(0, L.page + +el.dataset.d); paint(); }
    } catch (e) { X.toast(e.message); }
  });
  document.addEventListener("change", (ev) => {
    if (ev.target.id === "lab-filter" && L.res) { L.filter = ev.target.value; L.page = 0; paint(); }
    else if (ev.target.id === "sc-kind") {
      const k = ev.target.value, da = k === "digital_arrest", kyc = k === "kyc_remote";
      if ($("sc-call")) $("sc-call").checked = da || kyc;
      if ($("sc-screen")) $("sc-screen").checked = da;
      if ($("sc-amount")) $("sc-amount").value = da ? 20000 : k === "genuine_new_payee" ? 40000 : k === "investment" ? 5000 : kyc ? 8000 : 2000;
      if ($("sc-steps")) $("sc-steps").value = k === "genuine_new_payee" ? 1 : 4;
    }
  });

  window.VyuhLab = { view };
})();
