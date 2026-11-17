/* Health Insight dashboard - parses Garmin Personal API JSON and renders charts + insights. */
"use strict";

// ---------- small utils ----------
const $ = (s, r = document) => r.querySelector(s);
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const mean = (a) => { const x = a.filter(isNum); return x.length ? x.reduce((s, v) => s + v, 0) / x.length : null; };
const sd = (a) => { const x = a.filter(isNum), m = mean(x); return x.length > 1 ? Math.sqrt(x.reduce((s, v) => s + (v - m) ** 2, 0) / (x.length - 1)) : null; };
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const last = (a, n) => a.slice(Math.max(0, a.length - n));
const round = (v, d = 0) => (isNum(v) ? +v.toFixed(d) : null);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtHM = (sec) => { if (!isNum(sec)) return "–"; const m = Math.round(sec / 60); return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`; };
const fmtInt = (v) => (isNum(v) ? Math.round(v).toLocaleString() : "–");
const isoLocal = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const shortDate = (s) => parseDay(s).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const signed = (v, d = 0, unit = "") => (isNum(v) ? `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toFixed(d)}${unit}` : "–");
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};

// ---------- tolerant JSON field lookup ----------
// Garmin's response shapes vary by endpoint/version, so look fields up by a list
// of candidate names (dotted paths first, then a breadth-first search).
function dig(o, path) { for (const p of path.split(".")) { if (o == null || typeof o !== "object") return undefined; o = o[p]; } return o; }
function find(rec, keys) {
  for (const k of keys) if (k.includes(".")) { const v = dig(rec, k); if (isNum(v)) return v; }
  const want = new Set(keys.filter((k) => !k.includes(".")).map((k) => k.toLowerCase()));
  const q = [rec];
  while (q.length) {
    const o = q.shift();
    for (const [k, v] of Object.entries(o)) {
      if (want.has(k.toLowerCase()) && isNum(v)) return v;
      if (v && typeof v === "object" && !Array.isArray(v)) q.push(v);
    }
  }
  return null;
}
const DATE_KEYS = ["calendarDate", "summaryDate", "date", "calendarDay", "day", "startDate"];
function dateOf(o) {
  for (const k of DATE_KEYS) {
    const v = o[k];
    if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v)) return v.slice(0, 10);
    if (isNum(v) && v > 1e11) return isoLocal(new Date(v));
  }
  return null;
}
function dayRecords(json, out = []) {
  if (Array.isArray(json)) json.forEach((x) => dayRecords(x, out));
  else if (json && typeof json === "object") {
    const d = dateOf(json);
    if (d) out.push([d, json]);
    else Object.values(json).forEach((v) => { if (v && typeof v === "object") dayRecords(v, out); });
  }
  return out;
}

const METRICS = {
  sleepSec: [["sleep"], ["sleepTimeSeconds", "totalSleepSeconds", "sleepTimeInSeconds", "sleepDurationInSeconds"]],
  deepSec: [["sleep"], ["deepSleepSeconds", "deepSleepDurationInSeconds"]],
  lightSec: [["sleep"], ["lightSleepSeconds", "lightSleepDurationInSeconds"]],
  remSec: [["sleep"], ["remSleepSeconds", "remSleepDurationInSeconds"]],
  awakeSec: [["sleep"], ["awakeSleepSeconds", "awakeDurationInSeconds"]],
  sleepScore: [["sleep"], ["sleepScores.overall.value", "overallSleepScore.value", "sleepScore", "overallScore", "sleepScoreValue"]],
  hrv: [["hrv", "sleep"], ["lastNightAvg", "lastNightAverage", "avgOvernightHrv", "hrvValue"]],
  hrvLow: [["hrv"], ["baseline.balancedLow", "balancedLow"]],
  hrvHigh: [["hrv"], ["baseline.balancedUpper", "balancedUpper"]],
  rhr: [["dailySummaries", "sleep"], ["restingHeartRate", "restingHeartRateValue", "restingHr", "currentDayRestingHeartRate"]],
  steps: [["steps", "dailySummaries"], ["totalSteps", "steps"]],
  stepGoal: [["steps", "dailySummaries"], ["stepGoal", "dailyStepGoal", "totalStepGoal"]],
  stress: [["stress", "dailySummaries"], ["overallStressLevel", "averageStressLevel", "avgStressLevel"]],
  bbHigh: [["bodyBattery", "dailySummaries"], ["bodyBatteryHighestValue", "highest", "bodyBatteryHigh", "maxBodyBattery"]],
  bbLow: [["bodyBattery", "dailySummaries"], ["bodyBatteryLowestValue", "lowest", "bodyBatteryLow", "minBodyBattery"]],
  vo2: [["vo2max"], ["vo2MaxPreciseValue", "vo2MaxValue", "vo2Max"]],
  weight: [["weight"], ["weight", "latestWeight.weight"]],
};

function buildDays(payload) {
  const { start, end } = payload.meta;
  const dates = [];
  for (let d = parseDay(start); isoLocal(d) <= end; d.setDate(d.getDate() + 1)) dates.push(isoLocal(d));
  const byDate = Object.fromEntries(dates.map((d) => [d, { date: d }]));
  const sets = payload.sets;
  for (const [metric, [setNames, keys]] of Object.entries(METRICS)) {
    for (const name of setNames) {
      const s = sets[name];
      if (!s?.ok || s.data == null) continue;
      for (const [d, rec] of dayRecords(s.data)) {
        const day = byDate[d];
        if (!day || isNum(day[metric])) continue;
        let v = find(rec, keys);
        if (v == null && (metric === "bbHigh" || metric === "bbLow") && Array.isArray(rec.bodyBatteryValuesArray)) {
          const vals = rec.bodyBatteryValuesArray.map((p) => (Array.isArray(p) ? p[p.length - 1] : p?.value ?? p?.bodyBatteryLevel)).filter(isNum);
          if (vals.length) v = metric === "bbHigh" ? Math.max(...vals) : Math.min(...vals);
        }
        if (metric === "weight" && isNum(v) && v > 1000) v /= 1000; // grams -> kg
        if (isNum(v)) day[metric] = v;
      }
    }
  }
  // treat 0 sleep / 0 hrv as missing (watch not worn)
  for (const d of Object.values(byDate)) for (const k of ["sleepSec", "hrv", "rhr", "sleepScore"]) if (d[k] === 0) delete d[k];
  return dates.map((d) => byDate[d]);
}

// ---------- activities ----------
const FAMILIES = [
  { key: "run", label: "Running", slot: "--s1", test: /run|track|treadmill/ },
  { key: "ride", label: "Cycling", slot: "--s2", test: /cycl|bik|ride|spin/ },
  { key: "strength", label: "Strength", slot: "--s3", test: /strength|weight|hiit|crossfit|bouldering|climb/ },
  { key: "walk", label: "Walk & hike", slot: "--s4", test: /walk|hik/ },
  { key: "swim", label: "Swimming", slot: "--s5", test: /swim/ },
  { key: "other", label: "Other", slot: "--muted", test: /.*/ },
];
const familyOf = (typeKey) => FAMILIES.find((f) => f.test.test(String(typeKey || "").toLowerCase()));
const prettyType = (t) => String(t || "activity").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

function buildActivities(payload) {
  const raw = payload.sets.activities?.ok ? payload.sets.activities.data : [];
  const list = Array.isArray(raw) ? raw : Array.isArray(raw?.activityList) ? raw.activityList : [];
  return list.map((a) => {
    const type = a.activityType?.typeKey ?? a.activityTypeDTO?.typeKey ?? a.activityType ?? "other";
    const zones = [1, 2, 3, 4, 5].map((i) => a[`hrTimeInZone_${i}`]);
    const dur = a.duration ?? a.movingDuration ?? a.elapsedDuration ?? 0;
    let load = a.activityTrainingLoad;
    if (!isNum(load) && zones.some(isNum)) load = zones.reduce((s, z, i) => s + (isNum(z) ? (i + 1) * z / 60 : 0), 0) * 0.9;
    if (!isNum(load)) load = (dur / 60) * (isNum(a.aerobicTrainingEffect) ? a.aerobicTrainingEffect * 0.6 : 1.5);
    return {
      id: a.activityId, name: a.activityName || prettyType(type), type, family: familyOf(type),
      date: String(a.startTimeLocal ?? a.startTimeGMT ?? "").slice(0, 10),
      dur, dist: a.distance, avgHR: a.averageHR ?? a.averageHeartRate, maxHR: a.maxHR,
      load, te: a.aerobicTrainingEffect, zones, kcal: a.calories,
    };
  }).filter((a) => /^\d{4}-\d{2}-\d{2}$/.test(a.date)).sort((a, b) => (a.date < b.date ? 1 : -1));
}

// ---------- analysis ----------
function series(days, k) { return days.map((d) => (isNum(d[k]) ? d[k] : null)); }
function latest(days, k) { for (let i = days.length - 1; i >= 0; i--) if (isNum(days[i][k])) return { v: days[i][k], date: days[i].date, i }; return null; }
function baseline(days, k, upto = days.length, n = 28) { return mean(days.slice(Math.max(0, upto - n), upto).map((d) => d[k])); }

function dailyLoad(days, acts) {
  const m = Object.fromEntries(days.map((d) => [d.date, 0]));
  for (const a of acts) if (a.date in m) m[a.date] += a.load;
  return days.map((d) => m[d.date]);
}
function rolling(arr, n) { return arr.map((_, i) => (i + 1 < Math.min(n, 7) ? null : mean(arr.slice(Math.max(0, i - n + 1), i + 1)))); }

function pearson(x, y) {
  const p = x.map((v, i) => [v, y[i]]).filter(([a, b]) => isNum(a) && isNum(b));
  if (p.length < 12) return null;
  const mx = mean(p.map((q) => q[0])), my = mean(p.map((q) => q[1]));
  let n = 0, dx = 0, dy = 0;
  for (const [a, b] of p) { n += (a - mx) * (b - my); dx += (a - mx) ** 2; dy += (b - my) ** 2; }
  return dx && dy ? { r: n / Math.sqrt(dx * dy), n: p.length } : null;
}

function readiness(days, load) {
  const parts = [];
  const h = latest(days, "hrv");
  if (h && h.i >= days.length - 2) {
    const b = baseline(days, "hrv", h.i), s = sd(days.slice(Math.max(0, h.i - 28), h.i).map((d) => d.hrv));
    if (b && s) parts.push({ name: "HRV", score: clamp(70 + ((h.v - b) / s) * 15, 0, 100), w: 0.3, note: `HRV ${h.v} ms vs ${Math.round(b)} avg` });
  }
  const r = latest(days, "rhr");
  if (r && r.i >= days.length - 2) {
    const b = baseline(days, "rhr", r.i);
    if (b) parts.push({ name: "RHR", score: clamp(75 - (r.v - b) * 7, 0, 100), w: 0.2, note: `RHR ${r.v} vs ${b.toFixed(0)} avg` });
  }
  const sc = latest(days, "sleepScore"), sl = latest(days, "sleepSec");
  if (sc && sc.i >= days.length - 2) parts.push({ name: "Sleep", score: sc.v, w: 0.25, note: `sleep score ${sc.v}` });
  else if (sl && sl.i >= days.length - 2) parts.push({ name: "Sleep", score: clamp((sl.v / 3600 / 8) * 100, 0, 100), w: 0.25, note: `slept ${fmtHM(sl.v)}` });
  const bb = latest(days, "bbHigh");
  if (bb && bb.i >= days.length - 2) parts.push({ name: "Body Battery", score: bb.v, w: 0.15, note: `Body Battery peak ${bb.v}` });
  const ac = mean(last(load, 7)), ch = mean(last(load, 28));
  if (ch > 0) { const acwr = ac / ch; parts.push({ name: "Load", score: clamp(100 - Math.max(0, acwr - 1) * 120, 0, 100), w: 0.1, note: `load ratio ${acwr.toFixed(2)}` }); }
  if (!parts.length) return null;
  const w = parts.reduce((s, p) => s + p.w, 0);
  return { score: Math.round(parts.reduce((s, p) => s + p.score * p.w, 0) / w), parts };
}

function insights(days, acts, load) {
  const out = [];
  const add = (level, title, text, tag) => out.push({ level, title, text, tag });
  const n7 = last(days, 7), n28 = last(days, 28);

  // Sleep duration + consistency
  const s7 = mean(n7.map((d) => d.sleepSec)), s28 = mean(n28.map((d) => d.sleepSec));
  if (s7) {
    const h = s7 / 3600, varH = sd(n7.map((d) => (isNum(d.sleepSec) ? d.sleepSec / 3600 : null)));
    const debt = n7.reduce((s, d) => s + (isNum(d.sleepSec) ? Math.max(0, 7.5 - d.sleepSec / 3600) : 0), 0);
    const lvl = h >= 7 ? "good" : h >= 6.3 ? "warning" : "serious";
    let t = `You averaged ${fmtHM(s7)} a night this week${s28 ? ` (28-day: ${fmtHM(s28)})` : ""}.`;
    if (debt >= 2) t += ` That's about ${debt.toFixed(1)} h short of 7.5 h/night across the week.`;
    if (varH && varH > 0.9) t += ` Duration swings a lot night to night (±${varH.toFixed(1)} h); a steadier schedule usually helps recovery.`;
    add(lvl, "Sleep duration", t, "sleep");
  }
  const deep = mean(n28.map((d) => (isNum(d.deepSec) && d.sleepSec ? d.deepSec / d.sleepSec : null)));
  const rem = mean(n28.map((d) => (isNum(d.remSec) && d.sleepSec ? d.remSec / d.sleepSec : null)));
  if (deep && rem) add(deep < 0.12 ? "warning" : "good", "Sleep quality", `Over 28 days, ${Math.round(deep * 100)}% of your sleep was deep and ${Math.round(rem * 100)}% REM${deep < 0.12 ? "; deep sleep is on the low side. Late intense sessions, alcohol and late meals are common culprits." : ", a healthy mix."}`, "sleep");

  // HRV trend
  const h7 = mean(n7.map((d) => d.hrv)), h28 = baseline(days, "hrv", days.length - 7, 28);
  if (h7 && h28) {
    const pct = (h7 / h28 - 1) * 100;
    const lvl = pct <= -10 ? "serious" : pct <= -5 ? "warning" : "good";
    const lowB = latest(days, "hrvLow"), hiB = latest(days, "hrvHigh"), hl = latest(days, "hrv");
    let t = `7-day HRV ${Math.round(h7)} ms is ${signed(pct, 0, "%")} vs your prior 4-week average (${Math.round(h28)} ms).`;
    if (hl && lowB && hiB) t += hl.v < lowB.v ? ` Last night (${hl.v} ms) was below your balanced range of ${lowB.v}–${hiB.v}.` : hl.v > hiB.v ? ` Last night (${hl.v} ms) was above your balanced range.` : ` Last night (${hl.v} ms) sat inside your balanced range.`;
    if (lvl !== "good") t += " A sustained drop often means accumulated fatigue, illness or poor sleep, so consider an easier day.";
    add(lvl, "Heart rate variability", t, "recovery");
  }

  // RHR trend
  const r7 = mean(n7.map((d) => d.rhr)), r28 = baseline(days, "rhr", days.length - 7, 28);
  if (r7 && r28) {
    const diff = r7 - r28;
    add(diff >= 4 ? "serious" : diff >= 2 ? "warning" : "good", "Resting heart rate",
      `This week's resting HR averages ${r7.toFixed(0)} bpm (${signed(diff, 1, " bpm")} vs the previous 4 weeks).${diff >= 2 ? " An elevated RHR alongside lower HRV is a classic sign to back off." : diff <= -2 ? " Trending down: usually a sign of improving aerobic fitness." : " Stable."}`, "recovery");
  }

  // Training load ACWR
  const ac = mean(last(load, 7)), ch = mean(last(load, 28));
  if (ch > 0) {
    const r = ac / ch;
    const lvl = r > 1.5 ? "serious" : r > 1.3 ? "warning" : r < 0.8 ? "info" : "good";
    const msg = r > 1.5 ? "a sharp spike; injury risk rises when load jumps this fast. Keep the next few days easy."
      : r > 1.3 ? "building quickly. Fine for a short block, but schedule a lighter week soon."
        : r < 0.8 ? "lighter than usual: good for a recovery week, but fitness slips if it lasts more than 2–3 weeks."
          : "in the productive range (0.8–1.3).";
    add(lvl, "Training load balance", `Acute (7-day) vs chronic (28-day) load ratio is ${r.toFixed(2)}, ${msg}`, "training");
  }

  // Consistency
  if (acts.length) {
    const d28 = new Set(acts.filter((a) => a.date >= n28[0].date).map((a) => a.date)).size;
    const hrsRecent = acts.filter((a) => a.date >= n28[0].date).reduce((s, a) => s + a.dur, 0) / 3600;
    const prevStart = days[Math.max(0, days.length - 56)]?.date;
    const hrsPrev = days.length >= 56 ? acts.filter((a) => a.date >= prevStart && a.date < n28[0].date).reduce((s, a) => s + a.dur, 0) / 3600 : null;
    let t = `You trained on ${d28} of the last 28 days (${(d28 / 4).toFixed(1)} days/week), ${hrsRecent.toFixed(1)} h total.`;
    if (hrsPrev) t += ` That's ${signed((hrsRecent / hrsPrev - 1) * 100, 0, "%")} volume vs the 4 weeks before.`;
    add(d28 >= 12 ? "good" : d28 >= 8 ? "info" : "warning", "Consistency", t, "training");
  }

  // Intensity distribution
  const z = [0, 0, 0, 0, 0];
  acts.forEach((a) => a.zones.forEach((v, i) => { if (isNum(v)) z[i] += v; }));
  const zt = z.reduce((s, v) => s + v, 0);
  if (zt > 3600) {
    const easy = (z[0] + z[1]) / zt, hard = (z[3] + z[4]) / zt;
    add(easy >= 0.7 ? "good" : "warning", "Intensity distribution",
      `${Math.round(easy * 100)}% of your training time was in zones 1–2 and ${Math.round(hard * 100)}% in zones 4–5.${easy < 0.7 ? " Most endurance plans aim for ~80% easy; more low-intensity time tends to build fitness with less fatigue." : " A solid easy/hard balance."}`, "training");
  }

  // Training vs next-night HRV
  const nextHrv = days.map((_, i) => days[i + 1]?.hrv ?? null);
  const loads = load.filter((v) => v > 0).sort((a, b) => a - b);
  if (loads.length >= 8) {
    const q = loads[Math.floor(loads.length * 0.75)];
    const after = mean(load.map((v, i) => (v >= q ? nextHrv[i] : null))), other = mean(load.map((v, i) => (v < q ? nextHrv[i] : null)));
    if (after && other) {
      const pct = (after / other - 1) * 100;
      add(pct < -5 ? "info" : "good", "How hard days affect you", `After your hardest training days (top 25% by load), overnight HRV averaged ${Math.round(after)} ms vs ${Math.round(other)} ms otherwise (${signed(pct, 0, "%")}).${pct < -8 ? " Your body takes a noticeable hit, so plan easy days after big sessions." : " You're absorbing hard days well."}`, "pattern");
    }
  }
  // Sleep -> HRV relationship
  const c = pearson(days.map((d) => (isNum(d.sleepSec) ? d.sleepSec / 3600 : null)), series(days, "hrv"));
  if (c && Math.abs(c.r) >= 0.3) add("info", "Sleep drives your HRV", `Across ${c.n} nights, longer sleep and higher HRV move together (r = ${c.r.toFixed(2)}). Protecting sleep is one of your strongest recovery levers.`, "pattern");

  // Steps
  const st7 = mean(n7.slice(0, -1).map((d) => d.steps));
  if (st7) {
    const goal = latest(days, "stepGoal")?.v || 10000;
    const hit = n28.slice(0, -1).filter((d) => isNum(d.steps) && d.steps >= goal).length;
    add(st7 >= goal ? "good" : st7 >= goal * 0.7 ? "info" : "warning", "Daily movement", `${fmtInt(st7)} steps/day over the last 7 full days; you hit your ${fmtInt(goal)} goal on ${hit} of the last ${Math.min(27, n28.length - 1)} days.`, "activity");
  }
  // Stress
  const t7 = mean(n7.map((d) => d.stress)), t28 = baseline(days, "stress", days.length - 7, 28);
  if (t7) add(t7 > 50 ? "serious" : t7 > 35 ? "warning" : "good", "Stress", `Average all-day stress is ${Math.round(t7)}${t28 ? ` (prior 4 weeks: ${Math.round(t28)})` : ""}. ${t7 > 35 ? "Garmin rates 26–50 as medium; short breathing breaks and lighter evenings can bring it down." : "Mostly in the low/rest range."}`, "recovery");
  // VO2 max
  const vs = days.map((d) => d.vo2).filter(isNum);
  if (vs.length >= 2) {
    const dv = vs[vs.length - 1] - vs[0];
    add(dv >= 0 ? "good" : "info", "Cardio fitness", `VO₂ max is ${vs[vs.length - 1].toFixed(1)} (${signed(dv, 1)} over this range).`, "fitness");
  }
  const order = { serious: 0, critical: 0, warning: 1, info: 2, good: 3 };
  return out.sort((a, b) => order[a.level] - order[b.level]);
}

// ---------- rendering ----------
const charts = {};
let STATE = null;

function baseOpts(extra = {}) {
  const ink2 = css("--ink-2"), muted = css("--muted"), grid = css("--grid");
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false, labels: { color: ink2, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "rectRounded", font: { size: 12 } } },
      tooltip: { backgroundColor: css("--surface"), titleColor: css("--ink"), bodyColor: ink2, borderColor: css("--axis"), borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true },
    },
    scales: {
      x: { grid: { display: false }, border: { color: css("--axis") }, ticks: { color: muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 8, font: { size: 11 } } },
      y: { grid: { color: grid }, border: { display: false }, ticks: { color: muted, maxTicksLimit: 5, font: { size: 11 } } },
    },
    ...extra,
  };
}
function draw(id, config) {
  const canvas = document.getElementById(id);
  charts[id]?.destroy();
  const wrap = canvas.parentElement;
  wrap.querySelector(".empty")?.remove();
  canvas.hidden = false;
  if (!config) {
    canvas.hidden = true;
    wrap.insertAdjacentHTML("beforeend", `<div class="empty">No data from Garmin for this range.</div>`);
    return;
  }
  charts[id] = new Chart(canvas, config);
}
const has = (arr) => arr.some(isNum);
const line = (label, data, color, extra = {}) => ({ label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, pointHoverBorderWidth: 2, pointHoverBorderColor: css("--surface"), tension: 0.3, spanGaps: true, ...extra });
const bars = (label, data, color, extra = {}) => ({ label, data, backgroundColor: color, borderRadius: 3, borderSkipped: "start", borderColor: css("--surface"), borderWidth: 0, categoryPercentage: 0.85, barPercentage: 0.9, ...extra });

function renderToday(days, load) {
  const rd = readiness(days, load);
  const el = $("#readiness");
  if (!rd) { el.innerHTML = `<div class="label">Readiness</div><div class="value">–</div><div class="why">Not enough recent sleep/HRV/heart-rate data.</div>`; }
  else {
    const lvl = rd.score >= 70 ? ["good", "✓", "Ready to train"] : rd.score >= 50 ? ["warning", "!", "Moderate: train easy-to-steady"] : ["serious", "!", "Low: prioritise recovery"];
    el.innerHTML = `<div class="label">Readiness <span class="tag">estimate</span></div>
      <div class="value">${rd.score}<small>/100</small></div>
      <div class="meter"><span style="width:${rd.score}%;background:var(--${lvl[0]})"></span></div>
      <div class="verdict"><span class="ico ${lvl[0]}">${lvl[1]}</span>${lvl[2]}</div>
      <div class="why">From ${rd.parts.map((p) => esc(p.note)).join(" · ")}.</div>`;
  }
  const tiles = [];
  const tile = (label, value, unit, delta) => tiles.push(`<div class="card tile"><div class="label">${label}</div><div class="value">${value}${unit ? `<small>${unit}</small>` : ""}</div><div class="delta">${delta || "&nbsp;"}</div></div>`);
  const ago = (x) => (x && x.date !== days[days.length - 1].date ? ` · ${shortDate(x.date)}` : "");
  const sl = latest(days, "sleepSec"), sc = sl ? days[sl.i].sleepScore : null;
  tile("Sleep last night", sl ? fmtHM(sl.v) : "–", "", sl ? `${isNum(sc) ? `score ${sc}` : ""}${ago(sl)}` : "");
  const h = latest(days, "hrv"), hb = h ? baseline(days, "hrv", h.i) : null;
  tile("HRV overnight", h ? h.v : "–", h ? " ms" : "", h && hb ? `${signed(h.v - hb, 0, " ms")} vs 28-day avg${ago(h)}` : "");
  const r = latest(days, "rhr"), rb = r ? baseline(days, "rhr", r.i) : null;
  tile("Resting HR", r ? r.v : "–", r ? " bpm" : "", r && rb ? `${signed(r.v - rb, 1)} vs 28-day avg${ago(r)}` : "");
  const bh = latest(days, "bbHigh"), bl = bh ? days[bh.i].bbLow : null;
  tile("Body Battery", bh ? bh.v : "–", bh ? " peak" : "", bh && isNum(bl) ? `low ${bl}${ago(bh)}` : "");
  const st = latest(days, "steps");
  tile("Steps", st ? fmtInt(st.v) : "–", "", st ? `7-day avg ${fmtInt(mean(last(days, 8).slice(0, -1).map((d) => d.steps)))}${ago(st)}` : "");
  const sr = latest(days, "stress");
  tile("Avg stress", sr ? Math.round(sr.v) : "–", sr ? " /100" : "", sr ? `28-day avg ${Math.round(baseline(days, "stress", sr.i)) || "–"}${ago(sr)}` : "");
  $("#tiles").innerHTML = tiles.join("");
}

function renderInsights(list) {
  const icon = { good: "✓", warning: "!", serious: "!", critical: "!", info: "i" };
  $("#insights").innerHTML = list.length ? list.map((x) => `<div class="card insight"><span class="ico ${x.level}" aria-label="${x.level}">${icon[x.level]}</span><div><h3>${esc(x.title)}<span class="tag">${esc(x.tag)}</span></h3><p>${esc(x.text)}</p></div></div>`).join("")
    : `<div class="card">Not enough data yet to generate insights for this range.</div>`;
}

function renderRecovery(days) {
  const labels = days.map((d) => shortDate(d.date));
  const h = (k) => days.map((d) => (isNum(d[k]) ? +(d[k] / 3600).toFixed(2) : null));
  const stageKeys = [["deepSec", "Deep", "--s1"], ["lightSec", "Light", "--s3"], ["remSec", "REM", "--s7"], ["awakeSec", "Awake", "--s4"]];
  const hasStages = has(series(days, "deepSec"));
  const sleepDs = hasStages
    ? stageKeys.map(([k, l, c]) => bars(l, h(k), css(c), { stack: "s", borderWidth: { top: 1 } }))
    : [bars("Sleep", h("sleepSec"), css("--s1"))];
  const hourTip = { callbacks: { label: (c) => ` ${c.dataset.label}: ${fmtHM(c.parsed.y * 3600)}`, footer: (items) => (items.length > 1 ? `Total ${fmtHM(items.reduce((s, i) => s + (i.parsed.y || 0), 0) * 3600)}` : "") } };
  draw("c-sleep", has(series(days, "sleepSec")) ? { type: "bar", data: { labels, datasets: sleepDs }, options: baseOpts({ plugins: { ...baseOpts().plugins, legend: { ...baseOpts().plugins.legend, display: hasStages, position: "top", align: "end" }, tooltip: { ...baseOpts().plugins.tooltip, ...hourTip } }, scales: { ...baseOpts().scales, x: { ...baseOpts().scales.x, stacked: true }, y: { ...baseOpts().scales.y, stacked: true } } }) } : null);

  const hrv = series(days, "hrv");
  const hrvDs = [];
  if (has(series(days, "hrvLow"))) {
    hrvDs.push(line("Balanced low", series(days, "hrvLow"), "transparent", { tension: 0, pointHoverRadius: 0 }));
    hrvDs.push(line("Balanced high", series(days, "hrvHigh"), "transparent", { tension: 0, fill: "-1", backgroundColor: css("--band"), pointHoverRadius: 0 }));
  }
  hrvDs.push(line("HRV", hrv, css("--s1")));
  hrvDs.push(line("7-day avg", rolling(hrv, 7), css("--ink-2"), { borderWidth: 1.5, borderDash: [4, 3] }));
  draw("c-hrv", has(hrv) ? { type: "line", data: { labels, datasets: hrvDs }, options: baseOpts({ plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip, filter: (i) => !i.dataset.label.startsWith("Balanced") || isNum(i.parsed.y) } } }) } : null);

  const rhr = series(days, "rhr");
  draw("c-rhr", has(rhr) ? { type: "line", data: { labels, datasets: [line("Resting HR", rhr, css("--s8")), line("7-day avg", rolling(rhr, 7), css("--ink-2"), { borderWidth: 1.5, borderDash: [4, 3] })] }, options: baseOpts() } : null);

  const bbH = series(days, "bbHigh"), bbL = series(days, "bbLow");
  draw("c-bb", has(bbH) ? { type: "line", data: { labels, datasets: [line("Low", bbL, css("--s2"), { tension: 0.2 }), line("High", bbH, css("--s1"), { tension: 0.2, fill: "-1", backgroundColor: css("--band") })] }, options: baseOpts({ plugins: { ...baseOpts().plugins, legend: { ...baseOpts().plugins.legend, display: true, position: "top", align: "end" } }, scales: { ...baseOpts().scales, y: { ...baseOpts().scales.y, min: 0, max: 100 } } }) } : null);

  const stress = series(days, "stress");
  draw("c-stress", has(stress) ? { type: "bar", data: { labels, datasets: [bars("Avg stress", stress, css("--s2"))] }, options: baseOpts({ scales: { ...baseOpts().scales, y: { ...baseOpts().scales.y, min: 0, suggestedMax: 60 } } }) } : null);

  const steps = series(days, "steps"), goal = days.map((d) => d.stepGoal ?? null);
  const stepDs = [bars("Steps", steps, css("--s3"))];
  if (has(goal)) stepDs.unshift(line("Goal", goal, css("--ink-2"), { type: "line", order: 0, borderWidth: 1.5, borderDash: [5, 4], tension: 0, pointHoverRadius: 0 }));
  draw("c-steps", has(steps) ? { type: "bar", data: { labels, datasets: stepDs }, options: baseOpts() } : null);

  const vo2 = series(days, "vo2"), wt = series(days, "weight");
  $("#f-vo2").hidden = !has(vo2);
  $("#f-weight").hidden = !has(wt);
  if (has(vo2)) draw("c-vo2", { type: "line", data: { labels, datasets: [line("VO₂ max", vo2, css("--s7"), { pointRadius: 2 })] }, options: baseOpts() });
  if (has(wt)) draw("c-weight", { type: "line", data: { labels, datasets: [line("Weight", wt.map((v) => round(v, 1)), css("--s5"), { pointRadius: 2 })] }, options: baseOpts() });
}

function weekStart(iso) { const d = parseDay(iso); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return isoLocal(d); }

function renderWorkouts(days, acts, load) {
  const inRange = acts.filter((a) => a.date >= days[0].date);
  const weeks = Math.max(1, days.length / 7);
  const totH = inRange.reduce((s, a) => s + a.dur, 0) / 3600;
  const totKm = inRange.reduce((s, a) => s + (isNum(a.dist) ? a.dist : 0), 0) / 1000;
  const tiles = [
    ["Sessions", fmtInt(inRange.length), "", `${(inRange.length / weeks).toFixed(1)} / week`],
    ["Training time", totH.toFixed(1), " h", `${(totH / weeks).toFixed(1)} h / week`],
    ["Distance", fmtInt(totKm), " km", `${(totKm / weeks).toFixed(0)} km / week`],
    ["Calories", fmtInt(inRange.reduce((s, a) => s + (a.kcal || 0), 0)), " kcal", "from workouts"],
  ];
  $("#wtiles").innerHTML = tiles.map(([l, v, u, d]) => `<div class="card tile"><div class="label">${l}</div><div class="value">${v}<small>${u}</small></div><div class="delta">${d}</div></div>`).join("");

  // weekly volume by family
  const wk = [...new Set(days.map((d) => weekStart(d.date)))];
  const used = FAMILIES.filter((f) => inRange.some((a) => a.family === f));
  const volDs = used.map((f) => bars(f.label, wk.map((w) => round(inRange.filter((a) => a.family === f && weekStart(a.date) === w).reduce((s, a) => s + a.dur, 0) / 3600, 2)), css(f.slot), { stack: "v", borderWidth: { top: 2 } }));
  draw("c-volume", inRange.length ? { type: "bar", data: { labels: wk.map((w) => shortDate(w)), datasets: volDs }, options: baseOpts({ plugins: { ...baseOpts().plugins, legend: { ...baseOpts().plugins.legend, display: true, position: "top", align: "end" }, tooltip: { ...baseOpts().plugins.tooltip, callbacks: { title: (i) => `Week of ${i[0].label}`, label: (c) => (c.parsed.y ? ` ${c.dataset.label}: ${fmtHM(c.parsed.y * 3600)}` : null), footer: (items) => `Total ${fmtHM(items.reduce((s, i) => s + (i.parsed.y || 0), 0) * 3600)}` } } }, scales: { ...baseOpts().scales, x: { ...baseOpts().scales.x, stacked: true }, y: { ...baseOpts().scales.y, stacked: true } } }) } : null);

  // load
  const labels = days.map((d) => shortDate(d.date));
  draw("c-load", load.some((v) => v > 0) ? { type: "bar", data: { labels, datasets: [
    line("Acute (7d)", rolling(load, 7).map((v) => round(v, 0)), css("--s1"), { type: "line", order: 0 }),
    line("Chronic (28d)", rolling(load, 28).map((v) => round(v, 0)), css("--s2"), { type: "line", order: 0 }),
    bars("Daily load", load.map((v) => round(v, 0)), css("--grid"), { order: 1 }),
  ] }, options: baseOpts({ plugins: { ...baseOpts().plugins, legend: { ...baseOpts().plugins.legend, display: true, position: "top", align: "end" } } }) } : null);

  // zones (ordinal blue ramp)
  const z = [0, 0, 0, 0, 0];
  inRange.forEach((a) => a.zones.forEach((v, i) => { if (isNum(v)) z[i] += v / 3600; }));
  draw("c-zones", z.some((v) => v > 0) ? { type: "bar", data: { labels: ["Zone 1", "Zone 2", "Zone 3", "Zone 4", "Zone 5"], datasets: [bars("Time", z.map((v) => round(v, 2)), [1, 2, 3, 4, 5].map((i) => css(`--z${i}`)))] }, options: baseOpts({ indexAxis: "y", interaction: { mode: "nearest", intersect: true, axis: "y" }, plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip, callbacks: { label: (c) => ` ${fmtHM(c.parsed.x * 3600)} (${Math.round((c.parsed.x / z.reduce((s, v) => s + v, 0)) * 100)}%)` } } }, scales: { x: { ...baseOpts().scales.y }, y: { ...baseOpts().scales.x, grid: { display: false } } } }) } : null);

  // sport mix
  const mix = used.map((f) => [f, inRange.filter((a) => a.family === f).reduce((s, a) => s + a.dur, 0) / 3600]).sort((a, b) => b[1] - a[1]);
  draw("c-sports", mix.length ? { type: "bar", data: { labels: mix.map(([f]) => f.label), datasets: [bars("Hours", mix.map(([, v]) => round(v, 1)), mix.map(([f]) => css(f.slot)))] }, options: baseOpts({ indexAxis: "y", interaction: { mode: "nearest", intersect: true, axis: "y" }, plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip, callbacks: { label: (c) => ` ${fmtHM(c.parsed.x * 3600)} · ${inRange.filter((a) => a.family === mix[c.dataIndex][0]).length} sessions` } } }, scales: { x: { ...baseOpts().scales.y }, y: { ...baseOpts().scales.x, grid: { display: false } } } }) } : null);

  // table
  $("#acts tbody").innerHTML = inRange.slice(0, 25).map((a) => `<tr>
    <td>${shortDate(a.date)}</td>
    <td><span class="dot" style="background:${css(a.family.slot)}"></span>${esc(prettyType(a.type))}</td>
    <td>${esc(a.name)}</td>
    <td class="n">${fmtHM(a.dur)}</td>
    <td class="n">${isNum(a.dist) && a.dist > 0 ? (a.dist / 1000).toFixed(2) + " km" : "–"}</td>
    <td class="n">${isNum(a.avgHR) ? Math.round(a.avgHR) : "–"}</td>
    <td class="n">${fmtInt(a.load)}</td>
    <td class="n">${isNum(a.te) ? a.te.toFixed(1) : "–"}</td></tr>`).join("") || `<tr><td colspan="8">No activities in this range.</td></tr>`;
}

function renderSources(payload) {
  const count = (d) => (Array.isArray(d) ? d.length : d && typeof d === "object" ? dayRecords(d).length || Object.keys(d).length : 0);
  $("#sources").innerHTML = Object.entries(payload.sets).map(([k, s]) => {
    const json = s.ok ? JSON.stringify(s.data, null, 1) : "";
    return `<details><summary><span class="ico ${s.ok ? "good" : "critical"}">${s.ok ? "✓" : "×"}</span><strong>${esc(k)}</strong>
      <span class="unit">${s.ok ? `${count(s.data)} records` : ""}</span>${s.ok ? "" : `<span class="err">${esc(s.error)}</span>`}</summary>
      ${s.ok ? `<pre>${esc(json.length > 20000 ? json.slice(0, 20000) + "\n… (truncated)" : json)}</pre>` : ""}</details>`;
  }).join("");
}

function render() {
  if (!STATE) return;
  const payload = STATE;
  const days = buildDays(payload);
  const acts = buildActivities(payload);
  const load = dailyLoad(days, acts);
  renderToday(days, load);
  renderInsights(insights(days, acts, load));
  renderRecovery(days);
  renderWorkouts(days, acts, load);
  renderSources(payload);
  const m = payload.meta;
  const failed = Object.values(payload.sets).filter((s) => !s.ok).length;
  $("#status").innerHTML = `${m.demo ? `<span class="pill">DEMO DATA</span>` : "Live Garmin data · "}${shortDate(m.start)} – ${shortDate(m.end)} · fetched ${new Date(m.fetchedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}${failed ? ` · ${failed} source${failed > 1 ? "s" : ""} failed` : ""}`;
}

// ---------- password (required when deployed; optional locally) ----------
const pw = {
  get() { try { return sessionStorage.getItem("hi.pw") || localStorage.getItem("hi.pw") || ""; } catch { return ""; } },
  set(v, remember) { try { (remember ? localStorage : sessionStorage).setItem("hi.pw", v); } catch { /* storage unavailable */ } },
  clear() { try { sessionStorage.removeItem("hi.pw"); localStorage.removeItem("hi.pw"); } catch { /* storage unavailable */ } },
};
function showLogin(message) {
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#lock").hidden = true;
  $("#login-err").textContent = message || "";
  $("#status").textContent = "Locked";
  $("#pw").focus();
}

async function load(refresh = false) {
  const days = store.get("hi.days", "90");
  document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.days === days)));
  const btn = $("#refresh");
  btn.disabled = true;
  $("#status").textContent = "Fetching from Garmin…";
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    const headers = pw.get() ? { Authorization: `Bearer ${pw.get()}` } : {};
    const res = await fetch(`/api/data?days=${days}&tz=${encodeURIComponent(tz)}${refresh ? "&refresh=1" : ""}`, { headers, cache: "no-store" });
    const body = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    if (res.status === 401 && body.auth) {
      const had = !!pw.get();
      pw.clear();
      return showLogin(had ? "Wrong password." : "");
    }
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    STATE = body;
    $("#banner").hidden = true;
    $("#login").hidden = true;
    $("#app").hidden = false;
    $("#lock").hidden = !pw.get();
    render();
  } catch (e) {
    $("#banner").hidden = false;
    const local = ["localhost", "127.0.0.1"].includes(location.hostname);
    $("#banner").innerHTML = `<strong>Couldn't load data.</strong> ${esc(e.message)}${local ? `<br><span class="unit" style="margin:0">Is <code>python3 server.py</code> running and your key in <code>.env</code>? Try <code>python3 server.py --demo</code> to preview.</span>` : ""}`;
    $("#status").textContent = "Error";
  } finally {
    btn.disabled = false;
  }
}

$("#login").addEventListener("submit", (e) => {
  e.preventDefault();
  pw.set($("#pw").value, $("#remember").checked);
  $("#pw").value = "";
  load();
});
$("#lock").addEventListener("click", () => { pw.clear(); STATE = null; Object.values(charts).forEach((c) => c.destroy()); showLogin(""); });
document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => { store.set("hi.days", b.dataset.days); load(); }));
$("#refresh").addEventListener("click", () => load(true));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
load();
