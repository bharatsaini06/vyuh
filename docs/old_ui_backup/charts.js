/* Vyuh charts: hand-written SVG, no external libraries (works offline). */
(function () {
  "use strict";
  const STAGE_COLOR = { S3: "var(--s3)", S4: "var(--s4)", S5: "var(--s5)", S6: "var(--s6)", S7: "var(--s7)" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const short = (tok) => (tok || "").replace("acct_", "").slice(0, 6);
  const inr = (v) => "₹" + Math.round(v).toLocaleString("en-IN");

  function stageOf(ev) {
    const tags = ev.stages || [];
    const order = ["S7", "S5", "S4", "S3", "S6"];
    for (const s of order) if (tags.some((t) => t.startsWith(s))) return s;
    return ev.to_collector ? "S5" : null;
  }

  /** Aligned journey timelines: one row per victim. */
  function timelines(members, opts = {}) {
    const aligned = opts.aligned !== false;
    const rows = members.slice(0, 16).map((m) => {
      const evs = (m.timeline || []).filter((e) => stageOf(e));
      const t0 = evs.length ? evs[0].t : m.joined_t;
      return { m, evs, t0 };
    });
    const all = rows.flatMap((r) => r.evs.map((e) => (aligned ? e.t - r.t0 : e.t)));
    if (!all.length) return '<div class="empty">No stage-tagged events yet.</div>';
    const gMin = aligned ? 0 : Math.min(...all);
    const gMax = Math.max(...all, gMin + 3600);
    const W = 760, left = 120, right = 20, rowH = 30, top = 28, H = top + rows.length * rowH + 30;
    const X = (t) => left + ((t - gMin) / (gMax - gMin)) * (W - left - right);
    let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Victim journey timelines">`;
    const spanH = (gMax - gMin) / 3600;
    const step = spanH > 96 ? 24 : spanH > 24 ? 6 : spanH > 6 ? 1 : 0.5;
    for (let h = 0; h <= spanH + 0.001; h += step) {
      const x = X(gMin + h * 3600);
      s += `<line class="axis" x1="${x}" x2="${x}" y1="${top - 8}" y2="${H - 24}"/>`;
      const lab = aligned ? `+${h % 1 ? h.toFixed(1) : h}h` : new Date((gMin + h * 3600) * 1000).toLocaleString("en-IN", { day: "2-digit", hour: "2-digit", minute: "2-digit" });
      s += `<text class="muted-text" x="${x}" y="${H - 8}" text-anchor="middle">${esc(lab)}</text>`;
    }
    rows.forEach((r, i) => {
      const y = top + i * rowH + rowH / 2;
      s += `<text x="8" y="${y + 4}">${esc(short(r.m.token))}${r.m.stopped ? " ✓" : ""}</text>`;
      s += `<line class="axis" x1="${left}" x2="${W - right}" y1="${y}" y2="${y}"/>`;
      for (const e of r.evs) {
        const st = stageOf(e);
        const x = X(aligned ? e.t - r.t0 : e.t);
        const rad = Math.max(3, Math.min(11, Math.log10(e.amount + 10) * 2));
        const fill = e.kind === "credit" ? "none" : STAGE_COLOR[st];
        s += `<circle cx="${x}" cy="${y}" r="${rad}" fill="${fill}" stroke="${STAGE_COLOR[st]}" stroke-width="2" opacity=".9">` +
          `<title>${esc(e.kind)} ${inr(e.amount)} · ${esc((e.stages || []).join(", ") || "to collector")}</title></circle>`;
      }
    });
    return s + "</svg>";
  }

  /** Money flow: victims -> collectors -> downstream accounts. */
  function moneyFlow(graph) {
    const nodes = graph.nodes || [], edges = graph.edges || [];
    if (!nodes.length) return '<div class="empty">No money flow yet.</div>';
    const cols = { victim: [], collector: [], downstream: [] };
    nodes.forEach((n) => (cols[n.role] || cols.downstream).push(n));
    const W = 760, rowH = 26, top = 30;
    const maxN = Math.max(cols.victim.length, cols.collector.length, cols.downstream.length, 1);
    const H = top + maxN * rowH + 20;
    const xs = { victim: 110, collector: 380, downstream: 650 };
    const pos = {};
    for (const role of Object.keys(cols)) {
      const list = cols[role];
      const offset = top + ((maxN - list.length) * rowH) / 2;
      list.forEach((n, i) => (pos[n.id] = { x: xs[role], y: offset + i * rowH + rowH / 2, role }));
    }
    const maxAmt = Math.max(...edges.map((e) => e.amount), 1);
    let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Money flow from victims to collectors to downstream accounts">`;
    s += `<text class="muted-text" x="${xs.victim}" y="14" text-anchor="middle">Victims</text>`;
    s += `<text class="muted-text" x="${xs.collector}" y="14" text-anchor="middle">Collector accounts</text>`;
    s += `<text class="muted-text" x="${xs.downstream}" y="14" text-anchor="middle">Forwarded to</text>`;
    for (const e of edges) {
      const a = pos[e.source], b = pos[e.target];
      if (!a || !b) continue;
      const w = 1 + 7 * Math.sqrt(e.amount / maxAmt);
      const x1 = a.x + 50, x2 = b.x - 50, mx = (x1 + x2) / 2;
      const col = a.role === "victim" ? "var(--s5)" : "var(--s6)";
      s += `<path d="M${x1} ${a.y} C${mx} ${a.y} ${mx} ${b.y} ${x2} ${b.y}" fill="none" stroke="${col}" stroke-opacity=".45" stroke-width="${w.toFixed(1)}"><title>${inr(e.amount)}</title></path>`;
    }
    for (const n of nodes) {
      const p = pos[n.id];
      if (!p) continue;
      const fill = p.role === "victim" ? "var(--bad-soft)" : p.role === "collector" ? "var(--warn-soft)" : "var(--purple-soft)";
      s += `<rect x="${p.x - 50}" y="${p.y - 10}" width="100" height="20" rx="6" fill="${fill}" stroke="var(--line)"/>`;
      s += `<text x="${p.x}" y="${p.y + 4}" text-anchor="middle" class="mono">${esc(short(n.label))}</text>`;
    }
    return s + "</svg>";
  }

  /** Horizontal bar list (e.g., today's funnel). Log scale when values span orders of magnitude. */
  function funnel(rows) {
    const max = Math.max(...rows.map((r) => r.value), 1);
    const useLog = max / Math.max(Math.min(...rows.map((r) => r.value || 1)), 1) > 50;
    const f = (v) => (useLog ? Math.log10(v + 1) / Math.log10(max + 1) : v / max);
    return rows.map((r) => `<div style="margin:6px 0"><div class="row small"><span>${esc(r.label)}</span><span class="spacer"></span><b>${Number(r.value).toLocaleString("en-IN")}</b></div>` +
      `<div class="bar"><i style="width:${(f(r.value) * 100).toFixed(1)}%;${r.accent ? "background:var(--accent)" : "background:var(--muted)"}"></i></div></div>`).join("") +
      (useLog ? '<div class="small muted">Bar lengths use a log scale.</div>' : "");
  }

  window.VyuhCharts = { timelines, moneyFlow, funnel, STAGE_COLOR, esc, short, inr };
})();
