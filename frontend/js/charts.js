/* Vyuh charts: hand-written SVG, no libraries (works offline, CSP-safe). */
(function () {
  "use strict";
  const COLOR = { S3: "var(--s3)", S4: "var(--s4)", S5: "var(--s5)", S6: "var(--s6)", S7: "var(--s7)" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const short = (tok) => (tok || "").replace("acct_", "").slice(0, 6);
  const inr = (v) => "₹" + Math.round(v || 0).toLocaleString("en-IN");

  function stageOf(ev) {
    const tags = ev.stages || [];
    for (const s of ["S7", "S5", "S4", "S3", "S6"]) if (tags.some((t) => t.startsWith(s))) return s;
    return ev.to_collector ? "S5" : null;
  }

  /** One row per victim, each dot = one scam step, coloured by stage. */
  function timelines(members, aligned) {
    const rows = members.slice(0, 14).map((m) => {
      const evs = (m.timeline || []).filter((e) => stageOf(e));
      return { m, evs, t0: evs.length ? evs[0].t : m.joined_t };
    });
    const all = rows.flatMap((r) => r.evs.map((e) => (aligned ? e.t - r.t0 : e.t)));
    if (!all.length) return '<div class="empty">No scam steps recorded for these customers yet.</div>';
    const gMin = aligned ? 0 : Math.min(...all);
    const gMax = Math.max(...all, gMin + 3600);
    const W = 820, left = 92, right = 24, rowH = 34, top = 18, H = top + rows.length * rowH + 34;
    const X = (t) => left + ((t - gMin) / (gMax - gMin)) * (W - left - right);
    let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Victim journeys, one row per customer">`;
    const span = (gMax - gMin) / 3600;
    const step = span > 96 ? 24 : span > 24 ? 6 : span > 6 ? 1 : 0.5;
    for (let h = 0; h <= span + 0.001; h += step) {
      const x = X(gMin + h * 3600);
      s += `<line class="axis" x1="${x}" x2="${x}" y1="${top - 4}" y2="${H - 26}"/>`;
      const lab = aligned ? `+${h % 1 ? h.toFixed(1) : h} h` : new Date((gMin + h * 3600) * 1000).toLocaleString("en-IN", { day: "2-digit", hour: "2-digit", minute: "2-digit" });
      s += `<text class="muted-t" x="${x}" y="${H - 8}" text-anchor="middle">${esc(lab)}</text>`;
    }
    rows.forEach((r, i) => {
      const y = top + i * rowH + rowH / 2;
      s += `<text x="8" y="${y + 4}" font-size="13">${esc(short(r.m.token))}${r.m.stopped ? " ✓" : ""}</text>`;
      s += `<line class="axis" x1="${left}" x2="${W - right}" y1="${y}" y2="${y}"/>`;
      for (const e of r.evs) {
        const st = stageOf(e), x = X(aligned ? e.t - r.t0 : e.t);
        const rad = Math.max(4, Math.min(12, Math.log10(e.amount + 10) * 2.2));
        s += `<circle cx="${x}" cy="${y}" r="${rad}" fill="${e.kind === "credit" ? "#fff" : COLOR[st]}" stroke="${COLOR[st]}" stroke-width="2.2">` +
          `<title>${esc(e.kind === "credit" ? "Received" : "Paid")} ${inr(e.amount)} · ${esc((e.stages || []).join(", ") || "to a receiving account")}</title></circle>`;
      }
    });
    return s + "</svg>";
  }

  /** Victims → receiving accounts → where the money went. */
  function moneyFlow(graph) {
    const nodes = graph.nodes || [], edges = graph.edges || [];
    if (!nodes.length) return '<div class="empty">No money movement recorded yet.</div>';
    const cols = { victim: [], collector: [], downstream: [] };
    nodes.forEach((n) => (cols[n.role] || cols.downstream).push(n));
    const W = 820, rowH = 34, top = 38;
    const maxN = Math.max(cols.victim.length, cols.collector.length, cols.downstream.length, 1);
    const H = top + maxN * rowH + 16;
    const xs = { victim: 120, collector: 410, downstream: 700 };
    const pos = {};
    for (const role of Object.keys(cols)) {
      const off = top + ((maxN - cols[role].length) * rowH) / 2;
      cols[role].forEach((n, i) => (pos[n.id] = { x: xs[role], y: off + i * rowH + rowH / 2, role }));
    }
    const maxAmt = Math.max(...edges.map((e) => e.amount), 1);
    let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Money flow from victims to receiving accounts to onward accounts">`;
    s += `<text class="muted-t" x="${xs.victim}" y="16" text-anchor="middle" font-size="13">Customers who paid</text>`;
    s += `<text class="muted-t" x="${xs.collector}" y="16" text-anchor="middle" font-size="13">Receiving accounts</text>`;
    s += `<text class="muted-t" x="${xs.downstream}" y="16" text-anchor="middle" font-size="13">Money moved on to</text>`;
    for (const e of edges) {
      const a = pos[e.source], b = pos[e.target];
      if (!a || !b) continue;
      const w = 1.5 + 8 * Math.sqrt(e.amount / maxAmt), x1 = a.x + 62, x2 = b.x - 62, mx = (x1 + x2) / 2;
      s += `<path d="M${x1} ${a.y} C${mx} ${a.y} ${mx} ${b.y} ${x2} ${b.y}" fill="none" stroke="${a.role === "victim" ? "var(--s5)" : "var(--s6)"}" stroke-opacity=".4" stroke-width="${w.toFixed(1)}"><title>${inr(e.amount)}</title></path>`;
    }
    for (const n of nodes) {
      const p = pos[n.id];
      if (!p) continue;
      const fill = p.role === "victim" ? "var(--scam-soft)" : p.role === "collector" ? "var(--saffron-soft)" : "var(--emerging-soft)";
      s += `<rect x="${p.x - 62}" y="${p.y - 13}" width="124" height="26" rx="7" fill="${fill}" stroke="var(--line)"/>`;
      s += `<text x="${p.x}" y="${p.y + 4}" text-anchor="middle" font-size="14">${esc(short(n.label))}</text>`;
    }
    return s + "</svg>";
  }

  /** Simple line chart: series = [{name, values, color, dash}], xs = labels (evenly spaced). */
  function lineChart(series, xs, opts = {}) {
    const W = 560, H = 240, l = 44, r = 110, t = 14, b = 34;
    const X = (i) => l + (i / Math.max(xs.length - 1, 1)) * (W - l - r);
    const Y = (v) => t + (1 - v) * (H - t - b);
    let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(opts.label || "chart")}">`;
    for (const g of [0, 0.25, 0.5, 0.75, 1]) {
      s += `<line class="axis" x1="${l}" x2="${W - r}" y1="${Y(g)}" y2="${Y(g)}"/><text class="muted-t" x="${l - 6}" y="${Y(g) + 4}" text-anchor="end">${Math.round(g * 100)}%</text>`;
    }
    xs.forEach((x, i) => { s += `<text class="muted-t" x="${X(i)}" y="${H - 12}" text-anchor="middle">${esc(x)}</text>`; });
    if (opts.xlabel) s += `<text class="muted-t" x="${(l + W - r) / 2}" y="${H}" text-anchor="middle">${esc(opts.xlabel)}</text>`;
    // end labels: keep at least 14 px apart so they never overlap
    const ends = series.map((se, k) => ({ k, y: Y(se.values[se.values.length - 1] || 0) })).sort((a, b) => a.y - b.y);
    for (let j = 1; j < ends.length; j++) if (ends[j].y - ends[j - 1].y < 14) ends[j].y = ends[j - 1].y + 14;
    const labelY = {}; ends.forEach((e) => (labelY[e.k] = e.y));
    series.forEach((se, k) => {
      const pts = se.values.map((v, i) => `${X(i).toFixed(1)},${Y(v || 0).toFixed(1)}`).join(" ");
      s += `<polyline fill="none" stroke="${se.color}" stroke-width="${se.bold ? 3.5 : 2.2}" ${se.dash ? 'stroke-dasharray="5 4"' : ""} points="${pts}"/>`;
      se.values.forEach((v, i) => { s += `<circle cx="${X(i)}" cy="${Y(v || 0)}" r="${se.bold ? 4 : 3}" fill="${se.color}"><title>${esc(se.name)}: ${Math.round((v || 0) * 100)}% at ${esc(xs[i])}</title></circle>`; });
      s += `<text x="${W - r + 8}" y="${labelY[k] + 4}" font-size="12" fill="${se.color}" font-weight="${se.bold ? 700 : 400}">${esc(se.name)}</text>`;
    });
    return s + "</svg>";
  }

  /** Horizontal bars: rows = [{label, value, color, text}], max = scale. */
  function bars(rows, max) {
    return `<div class="hbars">${rows.map((r) => `<div class="hb"><span class="hb-l">${esc(r.label)}</span><span class="hb-t"><i style="width:${Math.max(0.5, Math.min(100, (r.value / (max || 1)) * 100)).toFixed(1)}%;background:${r.color}"></i></span><span class="hb-v">${esc(r.text)}</span></div>`).join("")}</div>`;
  }

  window.VyuhCharts = { timelines, moneyFlow, lineChart, bars, esc, short, inr };
})();
