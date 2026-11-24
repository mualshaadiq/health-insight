/* Health Insight dashboard - renders the server's analysis (overview + daily page). */
"use strict";

// ---------- utils ----------
const $ = (s, r = document) => r.querySelector(s);
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const mean = (a) => { const x = a.filter(isNum); return x.length ? x.reduce((s, v) => s + v, 0) / x.length : null; };
const round = (v, d = 0) => (isNum(v) ? +v.toFixed(d) : null);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtHM = (sec) => { if (!isNum(sec)) return "–"; const m = Math.round(sec / 60); return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`; };
const fmtInt = (v) => (isNum(v) ? Math.round(v).toLocaleString() : "–");
const isoLocal = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const shortDate = (s) => parseDay(s).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const longDate = (s) => parseDay(s).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
const addDays = (s, n) => { const d = parseDay(s); d.setDate(d.getDate() + n); return isoLocal(d); };
const signed = (v, d = 0, unit = "") => (isNum(v) ? `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toFixed(d)}${unit}` : "–");
const hhmm = (m) => { const x = ((Math.round(m) % 1440) + 1440) % 1440; return `${String(Math.floor(x / 60)).padStart(2, "0")}:${String(x % 60).padStart(2, "0")}`; };
const pace = (a) => (a.family === "run" && a.dist > 0 ? (() => { const s = Math.round(a.dur / (a.dist / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}/km`; })() : "–");
const paceFmt = (sec) => (isNum(sec) && sec > 0 ? `${Math.floor(Math.round(sec) / 60)}:${String(Math.round(sec) % 60).padStart(2, "0")}` : "–");
const durFmt = (sec) => { if (!isNum(sec)) return "–"; const s = Math.round(sec), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${m}:${String(s % 60).padStart(2, "0")}`; };
const KIND = { easy: "Easy", long: "Long", tempo: "Tempo", intervals: "Intervals", race: "Race" };
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const TZ = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; } })();
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};
const FAMILY = {
  run: { label: "Running", slot: "--s1" }, ride: { label: "Cycling", slot: "--s2" }, strength: { label: "Strength", slot: "--s3" },
  walk: { label: "Walk & hike", slot: "--s4" }, swim: { label: "Swimming", slot: "--s5" }, other: { label: "Other", slot: "--muted" },
};
const prettyType = (t) => String(t || "activity").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

// ---------- password ----------
const pw = {
  get() { try { return sessionStorage.getItem("hi.pw") || localStorage.getItem("hi.pw") || ""; } catch { return ""; } },
  set(v, remember) { try { (remember ? localStorage : sessionStorage).setItem("hi.pw", v); } catch { /* storage unavailable */ } },
  clear() { try { sessionStorage.removeItem("hi.pw"); localStorage.removeItem("hi.pw"); } catch { /* storage unavailable */ } },
};
class AuthError extends Error {}
async function api(path) {
  const headers = pw.get() ? { Authorization: `Bearer ${pw.get()}` } : {};
  const sep = path.includes("?") ? "&" : "?";
  const res = await fetch(`${path}${sep}tz=${encodeURIComponent(TZ)}`, { headers, cache: "no-store" });
  const body = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (res.status === 401 && body.auth) throw new AuthError(pw.get() ? "Wrong password." : "");
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

// ---------- charts ----------
const charts = {};
function baseOpts(extra = {}) {
  const ink2 = css("--ink-2"), muted = css("--muted"), grid = css("--grid");
  const o = {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false, position: "top", align: "end", labels: { color: ink2, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "rectRounded", font: { size: 12 } } },
      tooltip: { backgroundColor: css("--surface"), titleColor: css("--ink"), bodyColor: ink2, borderColor: css("--axis"), borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true },
    },
    scales: {
      x: { grid: { display: false }, border: { color: css("--axis") }, ticks: { color: muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 8, font: { size: 11 } } },
      y: { grid: { color: grid }, border: { display: false }, ticks: { color: muted, maxTicksLimit: 5, font: { size: 11 } } },
    },
  };
  // shallow-merge the nested bits callers commonly override
  for (const [k, v] of Object.entries(extra)) {
    if (k === "plugins" || k === "scales") {
      o[k] = { ...o[k] };
      for (const [k2, v2] of Object.entries(v)) o[k][k2] = { ...(o[k][k2] || {}), ...v2 };
    } else o[k] = v;
  }
  return o;
}
const legendOn = { legend: { display: true } };
function draw(id, config) {
  const canvas = document.getElementById(id);
  if (!canvas) return;
  charts[id]?.destroy();
  delete charts[id];
  const wrap = canvas.parentElement;
  wrap.querySelector(".empty")?.remove();
  canvas.hidden = !config;
  if (!config) { wrap.insertAdjacentHTML("beforeend", `<div class="empty">No data from Garmin for this period.</div>`); return; }
  charts[id] = new Chart(canvas, config);
}
const has = (arr) => arr.some(isNum);
const line = (label, data, color, extra = {}) => ({ label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, pointHoverBorderWidth: 2, pointHoverBorderColor: css("--surface"), tension: 0.3, spanGaps: true, ...extra });
const bars = (label, data, color, extra = {}) => ({ label, data, backgroundColor: color, borderRadius: 3, borderSkipped: "start", borderColor: css("--surface"), borderWidth: 0, categoryPercentage: 0.85, barPercentage: 0.9, ...extra });
const rolling = (arr, n) => arr.map((_, i) => (i + 1 < Math.min(n, 7) ? null : mean(arr.slice(Math.max(0, i - n + 1), i + 1))));
const series = (days, k) => days.map((d) => (isNum(d[k]) ? d[k] : null));

// ---------- shared bits ----------
const ICON = { good: "✓", warning: "!", serious: "!", critical: "!", info: "i" };
const insightCard = (x) => `<div class="card insight"><span class="ico ${x.level}" role="img" aria-label="${x.level}">${ICON[x.level] || "i"}</span><div><h3>${esc(x.title)}<span class="tag">${esc(x.tag)}</span></h3><p>${esc(x.text)}</p>${x.action ? `<p class="action">→ ${esc(x.action)}</p>` : ""}</div></div>`;
const insightList = (el, list, empty = "Not enough data yet.") => { el.innerHTML = list?.length ? list.map(insightCard).join("") : `<div class="card muted">${empty}</div>`; };
const tile = (label, value, unit, delta, cls = "") => `<div class="card tile ${cls}"><div class="label">${label}</div><div class="value">${value}${unit ? `<small>${unit}</small>` : ""}</div><div class="delta">${delta || "&nbsp;"}</div></div>`;
const actRow = (a, timeCol) => `<tr>
  <td>${timeCol ? (isNum(a.hour) ? `${String(a.hour).padStart(2, "0")}:00` : "–") : shortDate(a.date)}</td>
  <td><span class="dot" style="background:${css(FAMILY[a.family].slot)}"></span>${esc(prettyType(a.type))}</td>
  <td>${esc(a.name)}</td>
  <td class="n">${fmtHM(a.dur)}</td>
  <td class="n">${isNum(a.dist) && a.dist > 0 ? (a.dist / 1000).toFixed(2) + " km" : "–"}</td>
  <td class="n">${pace(a)}</td>
  <td class="n">${isNum(a.avgHR) ? Math.round(a.avgHR) : "–"}</td>
  <td class="n">${fmtInt(a.load)}</td>
  <td class="n">${isNum(a.te) ? a.te.toFixed(1) : "–"}</td></tr>`;
const sessionHTML = (s, heading = "Today's run", heat = null) => !s ? "" : `
  <div class="session-head"><span class="ico ${s.level}">${ICON[s.level] || "i"}</span><div><div class="label">${heading}</div><h3>${esc(s.title)}</h3></div>${heat?.hot ? `<span class="heat-chip" title="Median watch temperature on recent runs">Hot · ${heat.forecast?.maxFeelsC ? `feels up to ${heat.forecast.maxFeelsC}°C today` : `recent runs ${heat.recentC}°C`}</span>` : ""}</div>
  <p>${esc(s.detail)}</p>${s.why?.length ? `<p class="why">Based on ${s.why.map(esc).join(" · ")}.</p>` : ""}`;
const timeAgo = (iso) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const SOURCE = { scheduled: "morning schedule", visit: "your visit", refresh: "manual refresh" };

// ================================================================ OVERVIEW
let STATE = null;

function renderToday(an, briefing) {
  const rd = an.readiness, el = $("#readiness");
  if (!rd) el.innerHTML = `<div class="label">Readiness</div><div class="value">–</div><div class="why">Not enough recent sleep, HRV or heart-rate data.</div>`;
  else el.innerHTML = `<div class="label">Readiness <span class="tag">estimate</span></div>
      <div class="value">${rd.score}<small>/100</small></div>
      <div class="meter"><span style="width:${rd.score}%;background:var(--${rd.level})"></span></div>
      <div class="verdict"><span class="ico ${rd.level}">${ICON[rd.level]}</span>${esc(rd.verdict)}</div>
      <div class="why">From ${rd.parts.map((p) => esc(p.note)).join(" · ")}.</div>`;
  const days = an.days, last = days.length - 1;
  const latestOf = (k) => { for (let i = last; i >= 0; i--) if (isNum(days[i][k])) return { v: days[i][k], i, date: days[i].date }; return null; };
  const base = (k, i) => mean(days.slice(Math.max(0, i - 28), i).map((d) => d[k]));
  const ago = (x) => (x && x.i !== last ? ` · ${shortDate(x.date)}` : "");
  const sl = latestOf("sleepSec"), h = latestOf("hrv"), r = latestOf("rhr"), bb = latestOf("bbHigh"), st = latestOf("steps"), sr = latestOf("stress");
  $("#tiles").innerHTML = [
    tile("Sleep last night", sl ? fmtHM(sl.v) : "–", "", sl ? `${isNum(days[sl.i].sleepScore) ? `score ${days[sl.i].sleepScore}` : ""}${ago(sl)}` : ""),
    tile("HRV overnight", h ? h.v : "–", h ? " ms" : "", h ? `${signed(h.v - base("hrv", h.i), 0, " ms")} vs 28-day avg${ago(h)}` : ""),
    tile("Resting HR", r ? r.v : "–", r ? " bpm" : "", r ? `${signed(r.v - base("rhr", r.i), 1)} vs 28-day avg${ago(r)}` : ""),
    tile("Body Battery", bb ? bb.v : "–", bb ? " peak" : "", bb && isNum(days[bb.i].bbLow) ? `low ${days[bb.i].bbLow}${ago(bb)}` : ""),
    tile("Steps", st ? fmtInt(st.v) : "–", "", st ? `7-day avg ${fmtInt(mean(days.slice(-8, -1).map((d) => d.steps)))}${ago(st)}` : ""),
    tile("Avg stress", sr ? Math.round(sr.v) : "–", sr ? " /100" : "", sr ? `28-day avg ${fmtInt(base("stress", sr.i))}${ago(sr)}` : ""),
  ].join("");
  const s = an.running?.today;
  $("#session").hidden = !s;
  $("#session").innerHTML = sessionHTML(s, "Today's run", an.running?.heat);
  $("#brief-meta").textContent = briefing ? `Daily briefing saved ${timeAgo(briefing.generatedAt)} (${SOURCE[briefing.source] || briefing.source})${briefing.hasLastNight ? "" : " · waiting for last night's sync"}` : "";
}

function renderRunning(an) {
  const r = an.running;
  $("#running-sec").hidden = !r;
  if (!r) return;
  const bp = r.base;
  $("#run-base").hidden = !(bp && bp.active);
  if (bp && bp.active) {
    const ladder = ["2:1", "3:1", "5:1", "continuous"];
    const step = ladder.indexOf(bp.ratio);
    $("#run-base").innerHTML = `
      <div class="base-head">
        <div><div class="label">Base-building phase</div><h3>Easy cap ${bp.cap} bpm <span class="unit">walk at ${bp.walkAbove}, jog again under ${bp.resumeBelow}</span></h3>
          <p class="foot" style="margin:2px 0 0">Cap = ${esc(bp.method)}.</p></div>
        <div class="base-ratio"><span class="unit">Run-walk now</span><strong>${bp.ratio === "continuous" ? "Continuous" : bp.ratio}</strong><span class="unit">${esc(bp.pattern)}</span></div>
      </div>
      <ol class="ladder">${ladder.map((x, i) => `<li class="${i < step ? "done" : i === step ? "now" : ""}">${x === "continuous" ? "Continuous" : x}</li>`).join("")}</ol>
      <p class="base-status">${bp.aboveCapPct != null ? `<span class="ico ${bp.aboveCapPct < 15 ? "good" : bp.aboveCapPct < 30 ? "warning" : "serious"} sm">${bp.aboveCapPct < 15 ? "✓" : "!"}</span>On your last ${bp.basedOnRuns} run(s), <strong>${bp.aboveCapPct}%</strong> of moving time was above ${bp.walkAbove} bpm. ` : ""}${bp.next ? `Get that under ${bp.ratio === "2:1" ? "30" : bp.ratio === "3:1" ? "15" : "5"}% and the plan moves you to <strong>${bp.next}</strong>.` : "You're running continuously under the cap. Keep it there."} Easy runs ${bp.easyMin} min, long run ${bp.longMin} min.${bp.qualityAllowed ? " One faster session a week is now unlocked." : ""}</p>
      <ul class="rules">${bp.rules.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  }
  const ct = r.capTrend || [];
  const capHr = bp?.cap || STATE?.analysis?.hrProfile?.easyCeiling;
  $("#captrend-title").firstChild.textContent = `Pace at ${capHr || "your easy"} bpm `;
  $("#cap-col").textContent = capHr ? `Pace @ ${capHr}` : "Pace @ cap";
  draw("c-captrend", ct.length ? { type: "line", data: { labels: ct.map((x) => shortDate(x.date)), datasets: [line(`Pace at ${capHr} bpm`, ct.map((x) => x.paceSec), css("--s3"), { pointRadius: 4, tension: 0.2 })] },
    options: baseOpts({ plugins: { tooltip: { callbacks: { label: (c) => ` ${paceFmt(c.parsed.y)}/km (${ct[c.dataIndex].minutes} min near ${capHr} bpm)` } } }, scales: { y: { reverse: true, ticks: { callback: (v) => paceFmt(v) } } } }) } : null);
  const f = r.fitness;
  $("#run-fitness").innerHTML = `<h3 class="card-h">Race predictions</h3>${f ? `
    <div class="kv"><span>Running fitness (VDOT)</span><strong>${f.vdot}</strong></div>
    ${r.predictions.map((p) => `<div class="kv"><span>${p.race}</span><strong class="mono">${p.time}</strong></div>`).join("")}
    <p class="foot">Estimated from ${esc(f.source)}. Predictions assume race-specific training.</p>` : `<p class="muted">Log a run of 3 km or more to estimate your fitness.</p>`}
    ${r.bests?.length ? `<h3 class="card-h sub-h">Fastest in the last 90 days</h3>${r.bests.map((b) => `<div class="kv"><span>${b.label} <span class="unit">${shortDate(b.date)}</span></span><strong class="mono">${b.time} <span class="unit">${b.pace}</span></strong></div>`).join("")}` : ""}
    <div class="kv sep"><span>Last 28 days</span><strong>${r.summary.runs28} runs · ${r.summary.km28} km</strong></div>
    <div class="kv"><span>Longest run</span><strong>${r.summary.longest28} km</strong></div>`;
  const cov = r.detailCoverage, pend = STATE?.meta?.runDetails;
  $("#run-coverage").textContent = cov ? `Detailed analysis for ${cov.withDetail} of ${cov.runs} runs in the last 90 days${pend?.pending ? ` · ${pend.pending} more queued (picked up on your next visits and each morning)` : ""}${pend?.failed ? ` · ${pend.failed} file(s) couldn't be read` : ""}` : "";
  const noteRank = { serious: 0, warning: 1, good: 2, info: 3 };
  $("#runs-table tbody").innerHTML = (r.runs || []).map((x) => {
    const top = [...(x.notes || [])].sort((a, b) => noteRank[a.level] - noteRank[b.level])[0];
    return `<tr data-date="${x.date}">
      <td>${shortDate(x.date)}</td><td><span class="kind ${x.kind}">${KIND[x.kind] || x.kind}</span></td>
      <td class="n">${x.distKm.toFixed(1)} km</td><td class="n">${paceFmt(x.paceSec)}</td>
      <td class="n">${paceFmt(x.capPaceSec)}</td>
      <td class="n">${isNum(x.avgHR) ? Math.round(x.avgHR) : "–"}</td>
      <td class="n">${isNum(x.hr5) ? x.hr5 : "–"}</td>
      <td class="n">${isNum(x.decoupling) ? x.decoupling.toFixed(1) + "%" : "–"}</td>
      <td class="n">${isNum(x.tempC) ? x.tempC + "°" : "–"}</td>
      <td class="note-cell">${top ? `<span class="ico ${top.level} sm">${ICON[top.level]}</span>${esc(top.text)}` : x.hasDetail ? "" : `<span class="muted">Summary only</span>`}</td></tr>`;
  }).join("") || `<tr><td colspan="10">No recent runs.</td></tr>`;
  $("#run-paces").innerHTML = `<h3 class="card-h">Training paces</h3>${bp && bp.active ? `<p class="base-note">During the base phase, run every easy session <strong>by heart rate (≤ ${bp.cap} bpm)</strong>, not by the paces below. They're for later, once easy running stays under the cap.</p>` : ""}${r.paces ? `<table class="paces"><tbody>${r.paces.map((p) => `<tr><th>${p.zone}</th><td class="mono">${p.pace}</td><td class="muted">${esc(p.hr || p.use)}</td></tr>`).join("")}</tbody></table>
    <p class="foot">Easy runs should feel conversational. Pace targets assume flat ground and mild weather; go by effort on hills and hot days.</p>` : `<p class="muted">Not enough running data yet.</p>`}`;
  const nw = r.nextWeek;
  $("#run-week").innerHTML = `<h3 class="card-h">Next 7 days</h3>
    <div class="kv"><span>Suggested distance</span><strong>${nw.kmLow}–${nw.kmHigh} km</strong></div>
    <p class="foot" style="margin-top:0">${esc(nw.reason[0].toUpperCase() + nw.reason.slice(1))} Recent average: ${nw.recentAvgKm} km/week.</p>
    <ul class="plan" aria-label="Suggested week, about ${r.planTotalKm} km">${r.plan.map((p) => `<li class="${p.type}"><span>${p.day}</span>${esc(p.session)}</li>`).join("")}</ul>
    <p class="foot">≈ ${r.planTotalKm} km in total. Shift days to fit your week; keep hard days apart and the long run at the end of the week.</p>`;
  const wk = r.weekly;
  const thisWeek = wk.length - 1;
  const labels = [...wk.map((w, i) => (i === thisWeek ? "This week" : shortDate(w.week))), "Next week"];
  draw("c-runweekly", wk.some((w) => w.km > 0) ? { type: "bar", data: { labels, datasets: [
    bars("Distance", [...wk.map((w) => w.km), null], [...wk.map((_, i) => (i === thisWeek ? css("--z1") : css("--s1"))), "transparent"], { grouped: false }),
    bars("Suggested range", [...wk.map(() => null), [nw.kmLow, nw.kmHigh]], css("--band"), { grouped: false, borderSkipped: false, borderWidth: 1.5, borderColor: css("--s1"), borderDash: [4, 3] }),
  ] }, options: baseOpts({ plugins: { tooltip: { filter: (i) => i.raw != null, callbacks: {
    title: (i) => (i[0].dataIndex === thisWeek ? "This week (so far)" : i[0].dataIndex > thisWeek ? "Next week" : `Week of ${i[0].label}`),
    label: (c) => (Array.isArray(c.raw) ? ` Suggested: ${c.raw[0]}–${c.raw[1]} km` : ` ${c.raw} km`),
    afterBody: (i) => { const w = wk[i[0].dataIndex]; return w ? [`${w.runs} runs · longest ${w.longKm} km`] : []; } } } } }) } : null);
  insightList($("#ins-running"), r.insights, "No running flags right now: keep it up.");
}

function renderTrainingCharts(days, acts, load) {
  const weeks = Math.max(1, days.length / 7);
  const totH = acts.reduce((s, a) => s + a.dur, 0) / 3600;
  const totKm = acts.reduce((s, a) => s + (isNum(a.dist) ? a.dist : 0), 0) / 1000;
  $("#wtiles").innerHTML = [
    tile("Sessions", fmtInt(acts.length), "", `${(acts.length / weeks).toFixed(1)} / week`),
    tile("Training time", totH.toFixed(1), " h", `${(totH / weeks).toFixed(1)} h / week`),
    tile("Distance", fmtInt(totKm), " km", `${(totKm / weeks).toFixed(0)} km / week`),
    tile("Calories", fmtInt(acts.reduce((s, a) => s + (a.kcal || 0), 0)), " kcal", "from workouts"),
  ].join("");
  const weekStart = (iso) => { const d = parseDay(iso); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return isoLocal(d); };
  const wk = [...new Set(days.map((d) => weekStart(d.date)))];
  const used = Object.keys(FAMILY).filter((f) => acts.some((a) => a.family === f));
  draw("c-volume", acts.length ? { type: "bar", data: { labels: wk.map(shortDate), datasets: used.map((f) => bars(FAMILY[f].label, wk.map((w) => round(acts.filter((a) => a.family === f && weekStart(a.date) === w).reduce((s, a) => s + a.dur, 0) / 3600, 2)), css(FAMILY[f].slot), { stack: "v", borderWidth: { top: 2 } })) },
    options: baseOpts({ plugins: { ...legendOn, tooltip: { callbacks: { title: (i) => `Week of ${i[0].label}`, label: (c) => (c.parsed.y ? ` ${c.dataset.label}: ${fmtHM(c.parsed.y * 3600)}` : null), footer: (items) => `Total ${fmtHM(items.reduce((s, i) => s + (i.parsed.y || 0), 0) * 3600)}` } } }, scales: { x: { stacked: true }, y: { stacked: true } } }) } : null);
  const labels = days.map((d) => shortDate(d.date));
  draw("c-load", load.some((v) => v > 0) ? { type: "bar", data: { labels, datasets: [
    line("Acute (7d)", rolling(load, 7).map((v) => round(v)), css("--s1"), { type: "line", order: 0 }),
    line("Chronic (28d)", rolling(load, 28).map((v) => round(v)), css("--s2"), { type: "line", order: 0 }),
    bars("Daily load", load.map((v) => round(v)), css("--grid"), { order: 1 }),
  ] }, options: baseOpts({ plugins: legendOn }) } : null);
  const z = [0, 0, 0, 0, 0];
  acts.forEach((a) => a.zones.forEach((v, i) => { if (isNum(v)) z[i] += v / 3600; }));
  const zt = z.reduce((s, v) => s + v, 0);
  const hbar = (tip) => baseOpts({ indexAxis: "y", interaction: { mode: "nearest", intersect: true, axis: "y" }, plugins: { tooltip: { callbacks: { label: tip } } }, scales: { x: { grid: { color: css("--grid"), display: true }, border: { display: false } }, y: { grid: { display: false } } } });
  draw("c-zones", zt > 0 ? { type: "bar", data: { labels: ["Zone 1", "Zone 2", "Zone 3", "Zone 4", "Zone 5"], datasets: [bars("Time", z.map((v) => round(v, 2)), [1, 2, 3, 4, 5].map((i) => css(`--z${i}`)))] }, options: hbar((c) => ` ${fmtHM(c.parsed.x * 3600)} (${Math.round((c.parsed.x / zt) * 100)}%)`) } : null);
  const mix = used.map((f) => [f, acts.filter((a) => a.family === f).reduce((s, a) => s + a.dur, 0) / 3600]).sort((a, b) => b[1] - a[1]);
  draw("c-sports", mix.length ? { type: "bar", data: { labels: mix.map(([f]) => FAMILY[f].label), datasets: [bars("Hours", mix.map(([, v]) => round(v, 1)), mix.map(([f]) => css(FAMILY[f].slot)))] }, options: hbar((c) => ` ${fmtHM(c.parsed.x * 3600)} · ${acts.filter((a) => a.family === mix[c.dataIndex][0]).length} sessions`) } : null);
  $("#acts tbody").innerHTML = acts.slice(0, 25).map((a) => actRow(a, false)).join("") || `<tr><td colspan="9">No activities in this period.</td></tr>`;
}

function renderRecovery(days) {
  const labels = days.map((d) => shortDate(d.date));
  const hrs = (k) => days.map((d) => (isNum(d[k]) ? +(d[k] / 3600).toFixed(2) : null));
  const hasStages = has(series(days, "deepSec"));
  const stageDs = [["deepSec", "Deep", "--s1"], ["lightSec", "Light", "--s3"], ["remSec", "REM", "--s7"], ["awakeSec", "Awake", "--s4"]].map(([k, l, c]) => bars(l, hrs(k), css(c), { stack: "s", borderWidth: { top: 1 } }));
  draw("c-sleep", has(series(days, "sleepSec")) ? { type: "bar", data: { labels, datasets: hasStages ? stageDs : [bars("Sleep", hrs("sleepSec"), css("--s1"))] },
    options: baseOpts({ plugins: { legend: { display: hasStages }, tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${fmtHM(c.parsed.y * 3600)}`, footer: (it) => (it.length > 1 ? `Total ${fmtHM(it.reduce((s, i) => s + (i.parsed.y || 0), 0) * 3600)}` : "") } } }, scales: { x: { stacked: true }, y: { stacked: true } } }) } : null);
  const hrv = series(days, "hrv");
  const hrvDs = [];
  if (has(series(days, "hrvLow"))) {
    hrvDs.push(line("Balanced low", series(days, "hrvLow"), "transparent", { tension: 0, pointHoverRadius: 0 }));
    hrvDs.push(line("Balanced high", series(days, "hrvHigh"), "transparent", { tension: 0, fill: "-1", backgroundColor: css("--band"), pointHoverRadius: 0 }));
  }
  hrvDs.push(line("HRV", hrv, css("--s1")), line("7-day avg", rolling(hrv, 7), css("--ink-2"), { borderWidth: 1.5, borderDash: [4, 3] }));
  draw("c-hrv", has(hrv) ? { type: "line", data: { labels, datasets: hrvDs }, options: baseOpts() } : null);
  const rhr = series(days, "rhr");
  draw("c-rhr", has(rhr) ? { type: "line", data: { labels, datasets: [line("Resting HR", rhr, css("--s8")), line("7-day avg", rolling(rhr, 7), css("--ink-2"), { borderWidth: 1.5, borderDash: [4, 3] })] }, options: baseOpts() } : null);
  const bbH = series(days, "bbHigh");
  draw("c-bb", has(bbH) ? { type: "line", data: { labels, datasets: [line("Low", series(days, "bbLow"), css("--s2"), { tension: 0.2 }), line("High", bbH, css("--s1"), { tension: 0.2, fill: "-1", backgroundColor: css("--band") })] }, options: baseOpts({ plugins: legendOn, scales: { y: { min: 0, max: 100 } } }) } : null);
  const stress = series(days, "stress");
  draw("c-stress", has(stress) ? { type: "bar", data: { labels, datasets: [bars("Avg stress", stress, css("--s2"))] }, options: baseOpts({ scales: { y: { min: 0, suggestedMax: 60 } } }) } : null);
  const steps = series(days, "steps"), goal = series(days, "stepGoal");
  const stepDs = [bars("Steps", steps, css("--s3"), { order: 1 })];
  if (has(goal)) stepDs.unshift(line("Goal", goal, css("--ink-2"), { type: "line", order: 0, borderWidth: 1.5, borderDash: [5, 4], tension: 0, pointHoverRadius: 0 }));
  draw("c-steps", has(steps) ? { type: "bar", data: { labels, datasets: stepDs }, options: baseOpts() } : null);
  const vo2 = series(days, "vo2"), wt = series(days, "weight");
  $("#f-vo2").hidden = !has(vo2);
  $("#f-weight").hidden = !has(wt);
  if (has(vo2)) draw("c-vo2", { type: "line", data: { labels, datasets: [line("VO₂ max", vo2, css("--s7"), { pointRadius: 2 })] }, options: baseOpts() });
  if (has(wt)) draw("c-weight", { type: "line", data: { labels, datasets: [line("Weight", wt.map((v) => round(v, 1)), css("--s5"), { pointRadius: 2 })] }, options: baseOpts() });
}

function renderSources(sources) {
  $("#sources").innerHTML = Object.entries(sources).map(([k, s]) => `<details data-set="${esc(k)}"><summary><span class="ico ${s.ok ? "good" : "critical"}">${s.ok ? "✓" : "×"}</span><strong>${esc(k)}</strong>
    <span class="unit">${s.ok ? `${s.count} records` : ""}</span>${s.ok ? "" : `<span class="err">${esc(s.error)}</span>`}</summary>${s.ok ? `<pre>Loading…</pre>` : ""}</details>`).join("");
  document.querySelectorAll("#sources details").forEach((d) => d.addEventListener("toggle", async () => {
    if (!d.open || d.dataset.loaded) return;
    d.dataset.loaded = "1";
    try {
      const s = await api(`/api/data?days=${store.get("hi.days", "90")}&raw=${encodeURIComponent(d.dataset.set)}`);
      const json = JSON.stringify(s.data, null, 1);
      d.querySelector("pre").textContent = json.length > 30000 ? json.slice(0, 30000) + "\n… (truncated)" : json;
    } catch (e) { d.querySelector("pre").textContent = e.message; }
  }));
}

function renderOverview() {
  if (!STATE) return;
  const { meta, analysis: an, briefing } = STATE;
  const days = an.days.filter((d) => d.date >= meta.start);
  const acts = an.activities.filter((a) => a.date >= meta.start);
  const load = an.load.slice(an.days.length - days.length);
  renderToday(an, briefing);
  insightList($("#ins-health"), an.insights.health);
  insightList($("#ins-wellbeing"), an.insights.wellbeing);
  insightList($("#ins-training"), an.insights.training);
  renderRunning(an);
  renderTrainingCharts(days, acts, load);
  renderRecovery(days);
  renderSources(STATE.sources);
}

// ================================================================ DAILY
let DAY = null;

function renderDay(rep) {
  DAY = rep;
  const m = rep.metrics, c = rep.compare;
  $("#d-title").textContent = longDate(rep.date);
  $("#d-date").value = rep.date;
  $("#d-date").min = rep.range.min;
  $("#d-date").max = rep.range.max;
  $("#d-next").disabled = rep.date >= rep.range.max;
  $("#d-prev").disabled = rep.date <= rep.range.min;
  const b = rep.briefing;
  $("#d-brief").innerHTML = b ? `
    <div class="brief-head"><div><div class="label">Morning briefing</div><div class="unit" style="margin:0">Saved ${timeAgo(b.generatedAt)} via ${SOURCE[b.source] || b.source}${b.hasLastNight ? "" : " (before last night's data synced)"}</div></div>
    ${b.readiness ? `<div class="brief-score"><span class="ico ${b.readiness.level}">${ICON[b.readiness.level]}</span><strong>${b.readiness.score}</strong>/100 · ${esc(b.readiness.verdict)}</div>` : ""}</div>
    ${b.today ? `<div class="brief-session">${sessionHTML(b.today, "Recommended run")}</div>` : ""}
    ${(() => { const top = [...(b.insights?.health || []), ...(b.insights?.wellbeing || []), ...(b.runningInsights || [])].filter((x) => x.level === "serious" || x.level === "warning").slice(0, 3); return top.length ? `<div class="brief-flags">${top.map((x) => `<div><span class="ico ${x.level}">${ICON[x.level]}</span><strong>${esc(x.title)}:</strong> ${esc(x.action || x.text)}</div>`).join("")}</div>` : ""; })()}`
    : `<div class="label">Morning briefing</div><p class="muted" style="margin:6px 0 0">No briefing was saved for this day. Briefings are saved each morning from the day you deployed this, and on your first visit each day.</p>`;
  const d = (k, unit, dec = 0, invert = false) => {
    const x = c[k]; if (!x || !isNum(x.delta)) return x && isNum(x.baseline) ? `avg ${round(x.baseline, dec)}${unit}` : "";
    const good = invert ? x.delta <= 0 : x.delta >= 0;
    return `<span class="${good ? "up" : "down"}">${signed(x.delta, dec, unit)}</span> vs 28-day avg`;
  };
  $("#d-tiles").innerHTML = [
    tile("Sleep", fmtHM(m.sleepSec), "", c.sleepSec && isNum(c.sleepSec.delta) ? `<span class="${c.sleepSec.delta >= 0 ? "up" : "down"}">${signed(c.sleepSec.delta / 60, 0, " min")}</span> vs 28-day avg` : ""),
    tile("Sleep score", isNum(m.sleepScore) ? m.sleepScore : "–", "", d("sleepScore", "")),
    tile("HRV", isNum(m.hrv) ? m.hrv : "–", isNum(m.hrv) ? " ms" : "", d("hrv", " ms")),
    tile("Resting HR", isNum(m.rhr) ? m.rhr : "–", isNum(m.rhr) ? " bpm" : "", d("rhr", "", 1, true)),
    tile("Avg stress", isNum(m.stress) ? Math.round(m.stress) : "–", "", d("stress", "", 0, true)),
    tile("Body Battery", isNum(m.bbHigh) ? m.bbHigh : "–", isNum(m.bbHigh) ? " peak" : "", isNum(m.bbLow) ? `low ${m.bbLow}` : ""),
    tile("Steps", fmtInt(m.steps), "", d("steps", "")),
    tile("Breathing", isNum(m.respiration) ? m.respiration.toFixed(1) : "–", isNum(m.respiration) ? " brpm" : "", d("respiration", "", 1, true)),
  ].join("");
  insightList($("#d-notes"), rep.notes, "No data recorded for this day.");

  const it = rep.intraday || {};
  const timeScale = (min, max) => ({ type: "linear", min, max, grid: { display: false }, border: { color: css("--axis") }, ticks: { color: css("--muted"), stepSize: 120, callback: (v) => hhmm(v), maxRotation: 0, font: { size: 11 } } });
  const dayStart = parseDay(rep.date).getTime();
  // sleep hypnogram: x = minutes from local midnight of this date (negative = evening before)
  const lv = it.sleepLevels || [];
  $("#df-sleep").hidden = !lv.length;
  if (lv.length) {
    const toMin = (iso) => (new Date(iso).getTime() - dayStart) / 60000;
    const pts = [];
    lv.forEach((s) => { const y = [0, 1, 2, 3].includes(s.stage) ? s.stage : 1; pts.push({ x: toMin(s.start), y }); pts.push({ x: toMin(s.end), y }); });
    const span = pts[pts.length - 1].x - pts[0].x;
    $("#d-sleep-sum").textContent = `${hhmm(pts[0].x)} → ${hhmm(pts[pts.length - 1].x)} · ${fmtHM(span * 60)} in bed`;
    draw("dc-sleep", { type: "line", data: { datasets: [line("Stage", pts, css("--s7"), { stepped: true, tension: 0, fill: { target: "origin" }, backgroundColor: css("--band"), pointHoverRadius: 0 })] },
      options: baseOpts({ interaction: { mode: "nearest", intersect: false, axis: "x" }, plugins: { tooltip: { callbacks: { title: (i) => hhmm(i[0].parsed.x), label: (i) => ` ${["Deep", "Light", "REM", "Awake"][i.parsed.y]}` } } },
        scales: { x: timeScale(pts[0].x, pts[pts.length - 1].x), y: { min: 0, max: 3, ticks: { stepSize: 1, callback: (v) => ["Deep", "Light", "REM", "Awake"][v] ?? "" } } } }) });
  }
  const hr = it.hr || [];
  $("#df-hr").hidden = !hr.length;
  if (hr.length) {
    const easy = rep.hrProfile?.easyCeiling;
    const ds = [line("Heart rate", hr.map(([x, y]) => ({ x, y })), css("--s8"), { borderWidth: 1.5, tension: 0.2 })];
    if (easy) ds.push(line("Easy ceiling", [{ x: 0, y: easy }, { x: 1440, y: easy }], css("--muted"), { borderDash: [4, 4], borderWidth: 1, pointHoverRadius: 0, tension: 0 }));
    draw("dc-hr", { type: "line", data: { datasets: ds }, options: baseOpts({ plugins: { tooltip: { filter: (i) => i.datasetIndex === 0, callbacks: { title: (i) => hhmm(i[0].parsed.x), label: (i) => ` ${i.parsed.y} bpm` } } }, scales: { x: timeScale(0, 1440) } }) });
  }
  const bb = it.bodyBattery || [];
  $("#df-bb").hidden = !bb.length;
  if (bb.length) draw("dc-bb", { type: "line", data: { datasets: [line("Body Battery", bb.map(([x, y]) => ({ x, y })), css("--s1"), { fill: { target: "origin" }, backgroundColor: css("--band"), tension: 0.3 })] }, options: baseOpts({ plugins: { tooltip: { callbacks: { title: (i) => hhmm(i[0].parsed.x) } } }, scales: { x: timeScale(0, 1440), y: { min: 0, max: 100 } } }) });
  const hv = (it.hrvReadings || []).filter((r) => r.time);
  $("#df-hrv").hidden = !hv.length;
  if (hv.length) {
    const toM = (t) => { const [h, mm] = t.split(":").map(Number); const v = h * 60 + mm; return v > 720 ? v - 1440 : v; };
    const pts = hv.map((r) => ({ x: toM(r.time), y: r.value })).sort((a, b) => a.x - b.x);
    draw("dc-hrv", { type: "line", data: { datasets: [line("HRV", pts, css("--s1"), { borderWidth: 1.5 })] }, options: baseOpts({ plugins: { tooltip: { callbacks: { title: (i) => hhmm(i[0].parsed.x), label: (i) => ` ${i.parsed.y} ms` } } }, scales: { x: timeScale(pts[0].x, pts[pts.length - 1].x) } }) });
  }
  $("#d-acts tbody").innerHTML = rep.activities.map((a) => actRow(a, true)).join("") || `<tr><td colspan="9">No workouts recorded.</td></tr>`;
  renderDayRuns(rep.runs || []);
}

const hasHills = (x) => { const a = (x.series || []).map((p) => p[3]).filter(isNum); return a.length > 1 && Math.max(...a) - Math.min(...a) >= 5; };
function renderDayRuns(runs) {
  $("#d-runs-sec").hidden = !runs.length;
  Object.keys(charts).filter((k) => k.startsWith("rc-")).forEach((k) => { charts[k].destroy(); delete charts[k]; });
  $("#d-runs").innerHTML = runs.map((x, i) => `
    <div class="card run-card">
      <div class="run-head">
        <div><span class="kind ${x.kind}">${KIND[x.kind] || x.kind}</span><h3>${esc(x.name)}</h3></div>
        <div class="run-stats">
          <div><span>Distance</span><strong>${x.distKm.toFixed(2)} km</strong></div>
          <div><span>Time</span><strong>${durFmt(x.movingSec || x.dur)}</strong></div>
          <div><span>Pace</span><strong>${paceFmt(x.paceSec)}/km</strong></div>
          ${x.gapSec ? `<div><span>Hill-adjusted</span><strong>${paceFmt(x.gapSec)}/km</strong></div>` : ""}
          <div><span>Avg HR</span><strong>${isNum(x.avgHR) ? Math.round(x.avgHR) : "–"}</strong></div>
          ${isNum(x.elevGain) ? `<div><span>Climb</span><strong>${x.elevGain} m</strong></div>` : ""}
          ${isNum(x.cadence) ? `<div><span>Cadence</span><strong>${x.cadence} spm</strong></div>` : ""}
        </div>
      </div>
      ${x.hasDetail ? "" : `<p class="muted">The activity file for this run couldn't be analysed yet, so only the summary is shown.</p>`}
      ${(x.notes || []).length ? `<div class="run-notes">${x.notes.map((n) => `<div><span class="ico ${n.level} sm">${ICON[n.level]}</span>${esc(n.text)}</div>`).join("")}</div>` : ""}
      ${x.best && Object.keys(x.best).length ? `<div class="chips">${Object.entries(x.best).map(([k, b]) => `<span class="chip"><b>${({ "1k": "1K", "3k": "3K", "5k": "5K", "10k": "10K", "21.1k": "Half" })[k]}</b> ${durFmt(b.sec)}${b.hr ? ` · ${b.hr} bpm` : ""}</span>`).join("")}</div>` : ""}
      ${x.series?.length ? `<div class="run-charts">
        <figure><figcaption>Pace <span class="unit">min/km, faster is higher</span></figcaption><div class="chart mini"><canvas id="rc-pace-${i}"></canvas></div></figure>
        <figure><figcaption>Heart rate <span class="unit">bpm</span></figcaption><div class="chart mini"><canvas id="rc-hr-${i}"></canvas></div></figure>
        ${hasHills(x) ? `<figure><figcaption>Elevation <span class="unit">m</span></figcaption><div class="chart mini"><canvas id="rc-alt-${i}"></canvas></div></figure>` : ""}
      </div>` : ""}
      ${x.splits?.length ? `<details class="splits"><summary>Splits (${x.splits.length})</summary><div class="table-wrap"><table><thead><tr><th>Km</th><th class="n">Pace</th><th class="n">Hill-adj.</th><th class="n">Avg HR</th><th class="n">Elev.</th></tr></thead><tbody>
        ${x.splits.map((sp) => `<tr><td>${sp.partial ? sp.km.toFixed(2) : sp.km}</td><td class="n">${paceFmt(sp.pace ?? sp.sec)}</td><td class="n">${paceFmt(sp.gapSec)}</td><td class="n">${sp.hr ?? "–"}</td><td class="n">${isNum(sp.elev) ? signed(sp.elev, 0, " m") : "–"}</td></tr>`).join("")}
      </tbody></table></div></details>` : ""}
    </div>`).join("");
  runs.forEach((x, i) => {
    if (!x.series?.length) return;
    const km = x.series.map((p) => p[0]);
    const xScale = { type: "linear", min: 0, max: km[km.length - 1], grid: { display: false }, border: { color: css("--axis") }, ticks: { color: css("--muted"), maxTicksLimit: 6, callback: (v) => `${+v.toFixed(1)} km`, font: { size: 11 } } };
    const mk = (id, label, idx, color, extraY = {}, fmt = (v) => v) => draw(id, { type: "line", data: { datasets: [line(label, x.series.map((p) => ({ x: p[0], y: p[idx] })), color, { borderWidth: 1.5, tension: 0.2 })] },
      options: baseOpts({ interaction: { mode: "nearest", intersect: false, axis: "x" }, plugins: { tooltip: { callbacks: { title: (it) => `${it[0].parsed.x.toFixed(2)} km`, label: (it) => ` ${fmt(it.parsed.y)}` } } }, scales: { x: xScale, y: extraY } }) });
    mk(`rc-pace-${i}`, "Pace", 1, css("--s1"), { reverse: true, ticks: { color: css("--muted"), maxTicksLimit: 4, callback: (v) => paceFmt(v), font: { size: 11 } } }, (v) => `${paceFmt(v)}/km`);
    mk(`rc-hr-${i}`, "Heart rate", 2, css("--s8"), {}, (v) => `${v} bpm`);
    if (hasHills(x)) mk(`rc-alt-${i}`, "Elevation", 3, css("--muted"), {}, (v) => `${v} m`);
  });
}

// ================================================================ routing & loading
function route() {
  const h = location.hash.replace(/^#/, "");
  const m = h.match(/^\/day(?:\/(\d{4}-\d{2}-\d{2}))?/);
  return m ? { page: "day", date: m[1] || null } : { page: "overview" };
}

function showPage(page) {
  document.querySelectorAll(".page").forEach((p) => { p.hidden = p.dataset.page !== page; });
  document.querySelectorAll(".tabs a").forEach((a) => a.setAttribute("aria-current", a.dataset.route === page ? "page" : "false"));
  $("#range").hidden = page !== "overview";
}

function showLogin(message) {
  document.querySelectorAll(".page").forEach((p) => { p.hidden = true; });
  $("#login").hidden = false;
  $("#lock").hidden = true;
  $("#login-err").textContent = message || "";
  $("#status").textContent = "Locked";
  $("#pw").focus();
}

function showError(e) {
  if (e instanceof AuthError) { pw.clear(); return showLogin(e.message); }
  $("#banner").hidden = false;
  const local = ["localhost", "127.0.0.1"].includes(location.hostname);
  $("#banner").innerHTML = `<strong>Couldn't load data.</strong> ${esc(e.message)}${local ? `<br><span class="unit" style="margin:0">Is <code>python3 server.py</code> running and your key in <code>.env</code>? Try <code>python3 server.py --demo</code> to preview.</span>` : ""}`;
  $("#status").textContent = "Error";
}

function statusLine(meta) {
  const failed = Object.values(STATE?.sources || {}).filter((s) => !s.ok).length;
  $("#status").innerHTML = `${meta.demo ? `<span class="pill">DEMO DATA</span>` : ""}Updated ${new Date(meta.fetchedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}${failed ? ` · ${failed} source${failed > 1 ? "s" : ""} failed` : ""}`;
}

async function load(refresh = false) {
  const r = route();
  showPage(r.page);
  $("#login").hidden = true;
  const btn = $("#refresh");
  btn.disabled = true;
  $("#status").textContent = "Fetching from Garmin…";
  try {
    if (r.page === "overview") {
      const days = store.get("hi.days", "90");
      document.querySelectorAll("#range button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.days === days)));
      STATE = await api(`/api/data?days=${days}${refresh ? "&refresh=1" : ""}`);
      renderOverview();
      statusLine(STATE.meta);
    } else {
      if (refresh) await api(`/api/data?days=90&refresh=1`);
      const rep = await api(`/api/day${r.date ? `?date=${r.date}` : ""}`);
      renderDay(rep);
      $("#status").textContent = "Daily view";
    }
    $("#banner").hidden = true;
    $("#lock").hidden = !pw.get();
  } catch (e) {
    showError(e);
  } finally {
    btn.disabled = false;
  }
}

const goDay = (date) => { location.hash = `#/day/${date}`; };
$("#d-prev").addEventListener("click", () => DAY && goDay(addDays(DAY.date, -1)));
$("#d-next").addEventListener("click", () => DAY && goDay(addDays(DAY.date, 1)));
$("#d-today").addEventListener("click", () => { location.hash = "#/day"; });
$("#d-date").addEventListener("change", (e) => e.target.value && goDay(e.target.value));
document.querySelectorAll("#range button").forEach((b) => b.addEventListener("click", () => { store.set("hi.days", b.dataset.days); load(); }));
$("#refresh").addEventListener("click", () => load(true));
$("#login").addEventListener("submit", (e) => { e.preventDefault(); pw.set($("#pw").value, $("#remember").checked); $("#pw").value = ""; load(); });
$("#lock").addEventListener("click", () => { pw.clear(); STATE = null; DAY = null; Object.keys(charts).forEach((k) => { charts[k].destroy(); delete charts[k]; }); showLogin(""); });
window.addEventListener("hashchange", () => load());
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => (route().page === "day" ? DAY && renderDay(DAY) : renderOverview()));
// click a row in the overview's activity table to open that day
$("#acts").addEventListener("click", (e) => {
  const tr = e.target.closest("tbody tr");
  if (!tr || !STATE) return;
  const i = [...tr.parentElement.children].indexOf(tr);
  const a = STATE.analysis.activities.filter((x) => x.date >= STATE.meta.start)[i];
  if (a) goDay(a.date);
});
$("#runs-table").addEventListener("click", (e) => { const tr = e.target.closest("tbody tr[data-date]"); if (tr) goDay(tr.dataset.date); });
load();
