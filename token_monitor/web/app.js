"use strict";
/* Token Monitor web dashboard. Vanilla JS, no dependencies, no network except same-origin /api. */

const $ = (id) => document.getElementById(id);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const RANGES = [["24h", "24h", 24], ["72h", "72h", 72], ["7d", "7 days", 7], ["14d", "14 days", 14], ["30d", "30 days", 30]];
const query = new URLSearchParams(location.search);
const TYPES = [["bar", "Bar"], ["line", "Line"], ["dots", "Dots"], ["pie", "Pie"]];
const storedType = (() => { try { return localStorage.getItem("tm-chart-type"); } catch (e) { return null; } })();
const state = { slice: (/^\d{4}-\d\d-\d\dT\d\d/.test(query.get("from") || "") && /^\d{4}-\d\d-\d\dT\d\d/.test(query.get("to") || "")) ? { from: query.get("from").slice(0, 13) + ":00", to: query.get("to").slice(0, 13) + ":00" } : null, type: TYPES.some((t) => t[0] === (query.get("chart") || storedType)) ? (query.get("chart") || storedType) : "bar", payload: null, range: query.get("range") || localStorage.getItem("tm-range") || "14d", by: "cost", settings: null, busy: false };
if (!RANGES.some((r) => r[0] === state.range)) state.range = "14d";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isHourly = (key) => key === "24h" || key === "72h";
const svgNS = "http://www.w3.org/2000/svg";

/* ------------------------------------------------------------------ formatting */
function money(v, cur) {
  v = Number(v) || 0;
  if (/^[A-Z]{3}$/.test(cur || "") && cur !== "CR") {
    try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur }).format(v); } catch (e) { /* fall through */ }
  }
  return `${v.toLocaleString(undefined, { maximumFractionDigits: v >= 100 ? 0 : 1 })} ${cur || ""}`.trim();
}
function tokens(v) {
  for (const [u, s] of [["B", 1e9], ["M", 1e6], ["K", 1e3]]) if (v >= s) return `${(v / s).toFixed(v / s >= 100 ? 0 : 1).replace(/\.0$/, "")}${u}`;
  return String(Math.round(v));
}
const dayLabel = (iso) => `${+iso.slice(8, 10)} ${MONTHS[+iso.slice(5, 7) - 1]}`;
const dayLong = (iso) => { const d = new Date(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)); return `${DAYS[d.getDay()]} ${dayLabel(iso)}`; };
const hourOf = (row) => +row.hour.slice(11, 13);
const hourTitle = (row) => `${dayLong(row.hour)} · ${row.hour.slice(11, 13)}:00–${String((hourOf(row) + 1) % 24).padStart(2, "0")}:00`;
function niceScale(top, steps = 4) {
  if (top <= 0) return [1, steps];
  const raw = top / steps, p = 10 ** Math.floor(Math.log10(raw));
  for (const f of [1, 2, 2.5, 5, 10]) if (f * p * steps >= top * 0.999) return [f * p, f * p * steps];
  return [10 * p, 10 * p * steps];
}
const axisNum = (v) => (v >= 1000 ? tokens(v) : v < 10 && v % 1 ? v.toFixed(2).replace(/0$/, "") : String(Math.round(v)));
function axisMoney(v, cur) {
  if (!/^[A-Z]{3}$/.test(cur || "") || cur === "CR") return axisNum(v);
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur, minimumFractionDigits: 0, maximumFractionDigits: v < 10 && v % 1 ? 2 : 0 }).format(v); } catch (e) { return axisNum(v); }
}
/* time slice: whole local hours, as "YYYY-MM-DDTHH:00" strings (start inclusive, end exclusive) */
const pad2 = (n) => String(n).padStart(2, "0");
const hourStr = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:00`;
const plusHours = (s, h) => hourStr(new Date(new Date(s).getTime() + h * 36e5));
const sliceLabel = (s) => { const d = new Date(s); return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]} ${pad2(d.getHours())}:00`; };
function setSlice(from, to) {
  if (!from || !to || to <= from) return false;
  state.slice = { from, to };
  if (state.payload) render();
  return true;
}
function clearSlice() { state.slice = null; if (state.payload) render(); }

function countdown(iso) {
  const ms = new Date(iso) - Date.now();
  if (!(ms > 0)) return "now";
  const d = Math.floor(ms / 864e5), h = Math.floor(ms / 36e5) % 24, m = Math.floor(ms / 6e4) % 60;
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

/* ------------------------------------------------------------------ update motion
   When a refresh brings new data, only the things that really changed move: numbers count to their new
   value and flash, bars/lines/slices glide to their new size. Skipped on first load, when you change a
   range, chart type or setting (motion.on is only true inside a refresh render), and with reduced motion. */
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
const motion = { on: false };
const DUR = 420;
const ease = (t) => 1 - Math.pow(1 - t, 3);
const lerp = (a, b, e) => a + (b - a) * e;
const canMove = () => motion.on && !REDUCED.matches;
function animate(frame) {
  const t0 = performance.now();
  const step = (now) => { const t = Math.min(1, (now - t0) / DUR); frame(ease(t)); if (t < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
function flash(node) {
  node.classList.remove("flash");
  void node.offsetWidth;
  node.classList.add("flash");
  node.addEventListener("animationend", () => node.classList.remove("flash"), { once: true });
}
const nums = new Map(), numFmt = {}, prevStore = {};
function numSpan(key, raw, fmt) {
  numFmt[key] = fmt;
  return `<span class="v" data-k="${key}" data-raw="${raw}">${esc(fmt(raw))}</span>`;
}
function settleNumbers(root) {
  root.querySelectorAll(".v[data-k]").forEach((node) => {
    const k = node.dataset.k, to = Number(node.dataset.raw), from = nums.get(k), fmt = numFmt[k];
    nums.set(k, to);
    if (!canMove() || from === undefined || fmt(from) === fmt(to)) return;
    flash(node);
    animate((e) => { if (node.isConnected) node.textContent = e >= 1 ? fmt(to) : fmt(lerp(from, to, e)); });
  });
}
/* ranked bars: start from the old width, glide to the new one, flash the numbers that changed */
const rankPrev = {};
function settleRank(list) {
  const old = rankPrev[list.id] || {}, now = {};
  const widths = [];
  list.querySelectorAll("li[data-name]").forEach((li) => {
    const name = li.dataset.name, span = li.querySelector(".bar span"), num = li.querySelector(".num");
    const w = Number(span.dataset.w), p = old[name];
    now[name] = { w, text: num.textContent };
    if (canMove() && p && Math.abs(p.w - w) > 0.2) { span.style.width = p.w + "%"; widths.push([span, w]); } else span.style.width = w + "%";
    if (canMove() && p && p.text !== num.textContent) flash(num);
  });
  rankPrev[list.id] = now;
  if (widths.length) { void list.offsetWidth; widths.forEach(([span, w]) => { span.style.width = w + "%"; }); }
}

/* ------------------------------------------------------------------ data */
async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch (e) { /* not json */ }
  if (!res.ok) throw new Error((body && body.error) || `Request failed (${res.status})`);
  return body;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

async function load(force = false) {
  if (state.busy) return;
  state.busy = true;
  $("refresh").classList.add("spin");
  $("refresh").disabled = true;
  try {
    const had = !!state.payload;
    state.payload = await (force ? post("/api/refresh") : api("/api/data"));
    motion.on = had;
    try { render(); } finally { motion.on = false; }
    if ($("settings").open) loadSettings();
  } catch (e) {
    banners([{ kind: "crit", html: `<b>Can't reach the dashboard server.</b> ${esc(e.message)}` }]);
  } finally {
    state.busy = false;
    $("refresh").classList.remove("spin");
    $("refresh").disabled = false;
  }
}

/* ------------------------------------------------------------------ page */
const WARN_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 3l10 18H2L12 3zm0 7v5m0 3v.01" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function banners(list) {
  $("banners").innerHTML = list.map((b) => `<div class="banner ${b.kind}">${WARN_ICON}<div>${b.html}</div></div>`).join("");
}

function render() {
  const p = state.payload, entries = p.providers;
  const entry = entries.find((e) => e.summary);
  const list = [];
  for (const e of entries) {
    if (e.error) list.push({ kind: "crit", html: `<b>${esc(e.name)} is unavailable.</b> ${esc(e.error)}` });
    else if (e.stale) list.push({ kind: "warn", html: `<b>${esc(e.name)}: showing saved numbers.</b> ${esc(e.why || "")}` });
    if (e.mock || e.sub === "mock data") list.push({ kind: "crit", html: "<b>MOCK DATA \u2014 these are not your numbers.</b> No GitHub token reached the dashboard. Start it with <code>scripts/web-up.sh</code> (after <code>gh auth login</code>) or set <code>TOKEN_MONITOR_GH_TOKEN</code> (see docs/WEB.md)." });
  }
  banners(list);
  $("plan").textContent = entries.length ? entries.map((e) => `${e.name}${e.mock ? " · MOCK DATA" : e.sub ? " · " + e.sub : ""}`).join("  |  ") : "No providers";
  $("updated").textContent = `Updated ${p.updated.slice(11, 19)} · refreshes every 60s`;

  $("empty").hidden = !!entry;
  $("hero").hidden = !entry;
  $("usage").hidden = !entry;
  if (!entry) {
    const only = entries.length && !entries.some((e) => e.summary);
    $("empty-text").textContent = only ? "A provider is set up but has no budget numbers yet." : "No provider is set up, or all of them are hidden. Open Settings to show them again.";
    return;
  }
  hero(entry);
  usage(entry);
  fillLive();
}

/* ------------------------------------------------------------------ pages (Dashboard / How it's calculated) */
function fillLive() {
  const box = $("how-live"), entry = state.payload && state.payload.providers.find((e) => e.summary);
  if (!entry) { box.hidden = true; return; }
  const s = entry.summary, cur = s.currency, cr = s.credits, cal = s.calibration, rows = [];
  const add = (k, v) => rows.push(`<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`);
  if (cr) {
    add("Credits ↔ dollars", `${esc(String(cr.per_usd))} ${esc(cr.unit)} = ${money(1, cur)}`);
    add("Spent / budget", `${Math.round(cr.spent).toLocaleString()} / ${Math.round(cr.budget).toLocaleString()} ${esc(cr.unit)} = ${money(s.spent, cur)} / ${money(s.budget, cur)}`);
  } else add("Spent / budget", `${money(s.spent, cur)} / ${money(s.budget, cur)}`);
  if (s.source_budget && Math.abs(s.source_budget - s.budget) > 0.005) add("Budget override", `${money(s.budget, cur)} (GitHub says ${money(s.source_budget, cur)})`);
  add("Burn rate", `${money(s.burn, cur)}/day${cr ? ` (about ${Math.round(s.burn * cr.per_usd).toLocaleString()} ${esc(cr.unit)}/day)` : ""}, ${esc(s.burn_basis || "n/a")}`);
  if (s.cost_factor != null) {
    const how = { calibrated: "calibrated", setting: "from TOKEN_MONITOR_COST_FACTOR", default: "not calibrated yet" }[s.factor_basis] || "";
    add("Calibration factor", `×${Number(s.cost_factor).toFixed(2)} (${how})`);
  }
  if (cal) add("Calibration data", `${cal.pairs} snapshot interval${cal.pairs === 1 ? "" : "s"}: portal grew ${money(cal.portal, cur)}, local cost ${money(cal.local, cur)}`);
  $("how-live-list").innerHTML = rows.join("");
  box.hidden = false;
}

function route() {
  const how = location.hash.startsWith("#how"), setup = location.hash.startsWith("#setup");
  $("view-how").hidden = !how;
  $("view-setup").hidden = !setup;
  $("view-dash").hidden = how || setup;
  for (const [id, on] of [["tab-how", how], ["tab-setup", setup], ["tab-dash", !how && !setup]]) { if (on) $(id).setAttribute("aria-current", "page"); else $(id).removeAttribute("aria-current"); }
  if (how) {
    document.title = "How it's calculated · Token Monitor";
    fillLive();
    const target = location.hash.length > 4 ? document.getElementById(location.hash.slice(1)) : null;
    if (target) target.scrollIntoView(); else window.scrollTo(0, 0);
  } else if (setup) {
    document.title = "How to set up · Token Monitor";
    const target = location.hash.length > 6 ? document.getElementById(location.hash.slice(1)) : null;
    if (target) target.scrollIntoView(); else window.scrollTo(0, 0);
  } else {
    document.title = "Token Monitor";
    if (state.payload) render();
  }
}
window.addEventListener("hashchange", route);

function hero(entry) {
  const s = entry.summary, cur = s.currency;
  const pct = Math.min(100, Math.max(0, (s.spent / s.budget) * 100)) || 0;
  const level = pct >= 90 ? "crit" : pct >= 70 ? "warn" : "ok";
  const r = 100, C = 2 * Math.PI * r;
  const g = $("gauge");
  g.setAttribute("aria-label", `${Math.round(pct)} percent of the budget used`);
  const existing = g.querySelector(".arc");
  if (!existing) {
    g.innerHTML = `<svg viewBox="0 0 240 240"><circle class="track" cx="120" cy="120" r="${r}" fill="none" stroke-width="16"/>
      <circle class="arc" cx="120" cy="120" r="${r}" fill="none" stroke-width="16" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C}"/></svg>
      <div class="mid"><div class="pct"></div><div class="of">of budget used</div></div>`;
  }
  const arc = g.querySelector(".arc");
  arc.setAttribute("class", `arc ${level}`);
  requestAnimationFrame(() => arc.setAttribute("stroke-dashoffset", String(C * (1 - pct / 100))));
  g.querySelector(".pct").innerHTML = `${numSpan("h:pct", pct, (v) => (v < 10 ? v.toFixed(1) : String(Math.round(v))))}<small>%</small>`;
  settleNumbers(g);

  const left = Math.max(0, s.budget - s.spent);
  const cr = s.credits;
  let lasts, rclass = "";
  const fmtDay = (d) => `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
  if (s.outlook === "exhausted") { lasts = "Budget used up"; rclass = "crit-text"; }
  else if (s.outlook === "idle") lasts = "No recent spending";
  else if (s.outlook === "ok") { const end = new Date(s.period_end); lasts = `Lasts past the reset (about ${money(Math.max(s.budget - s.projected, 0), cur)} left on ${end.getDate()} ${MONTHS[end.getMonth()]})`; }
  else {
    const out = s.out_date ? new Date(s.out_date) : null, early = out ? Math.max(1, Math.round((new Date(s.period_end) - out) / 864e5)) : 0;
    const inDays = out ? Math.max(1, Math.round((out - Date.now()) / 864e5)) : 0;
    lasts = out ? `At this pace ${cr ? "credits run" : "the budget runs"} out ~${fmtDay(out)} (in ${inDays}d), ${early}d before the reset` : `At this pace ${cr ? "credits run" : "the budget runs"} out before the reset`;
    rclass = "crit-text";
  }
  const reset = s.period_end;
  const stat = (label, value, small = "", cls = "") => `<div class="stat"><dt>${label}</dt><dd class="${cls}">${value}${small ? `<small>${small}</small>` : ""}</dd></div>`;
  $("stats").innerHTML =
    stat("Spent", numSpan("h:spent", s.spent, (v) => money(v, cur)), s.estimate != null ? "estimate; portal is offline" : cr ? `${Math.round(cr.spent).toLocaleString()} ${esc(cr.unit)}` : "") +
    stat("Budget", numSpan("h:budget", s.budget, (v) => money(v, cur)), s.source_budget && Math.abs(s.source_budget - s.budget) > 0.005 ? `your limit; GitHub says ${money(s.source_budget, cur)}` : cr ? `${Math.round(cr.budget).toLocaleString()} ${esc(cr.unit)}` : "") +
    stat("Left", numSpan("h:left", left, (v) => money(v, cur)), `${Math.round((left / s.budget) * 100)}% remaining`, left === 0 ? "crit-text" : "") +
    stat("Spending per day", numSpan("h:burn", s.burn, (v) => money(v, cur)), `${cr ? `about ${Math.round(s.burn * cr.per_usd).toLocaleString()} ${esc(cr.unit)}/day · ` : ""}${esc(s.burn_basis || "")}`) +
    stat("Resets in", countdown(reset), new Date(reset).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })) +
    stat("Calibration", s.factor_basis === "calibrated" ? numSpan("h:cal", Number(s.cost_factor), (v) => `×${v.toFixed(2)}`) : "—", s.factor_basis === "calibrated" ? "local estimate scaled to match the portal" : "local cost is not scaled") +
    `<div class="stat wide"><dt>Lasts until</dt><dd class="${rclass}">${esc(lasts)}</dd></div>`;
  settleNumbers($("stats"));
  const pace = $("stats").querySelector(".wide dd");
  if (canMove() && state.lastPace != null && state.lastPace !== lasts) flash(pace);
  state.lastPace = lasts;
}

/* ------------------------------------------------------------------ usage */
const TYPE_ICONS = {
  bar: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 20V11M12 20V4M18 20v-6" stroke="currentColor" stroke-width="3" stroke-linecap="round" fill="none"/></svg>',
  line: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M3 17l5-6 4 3 8-9" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>',
  dots: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="6" cy="16" r="2.3" fill="currentColor"/><circle cx="12" cy="8" r="2.3" fill="currentColor"/><circle cx="18" cy="13" r="2.3" fill="currentColor"/></svg>',
  pie: '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9h-9z" fill="currentColor" opacity=".9"/><path d="M14.5 2.6A9 9 0 0 1 21.4 9.5h-6.9z" fill="currentColor"/></svg>',
};

function usage(entry) {
  const s = entry.summary, cur = s.currency;
  const usd = "USD";   // hourly cost is always the local OpenCode estimate in dollars: never credits, never the portal currency
  const hourlyAll = entry.hourly || [], dailyAll = entry.daily || [], sl = hourlyAll.length ? state.slice : null;
  const presetH = isHourly(state.range), n = RANGES.find((r) => r[0] === state.range)[2];
  let winH, winD, rows, hourly;
  if (sl) {
    winH = hourlyAll.filter((r) => { const h = r.hour.slice(0, 16); return h >= sl.from && h < sl.to; });
    const lastDay = plusHours(sl.to, -1).slice(0, 10);
    winD = dailyAll.filter((r) => r.day >= sl.from.slice(0, 10) && r.day <= lastDay);
    rows = winH; hourly = true;
  } else {
    winH = hourlyAll.slice(-(presetH ? n : n * 24));
    winD = presetH ? dailyAll.slice(-(Math.ceil(n / 24) + 1)) : dailyAll.slice(-n);
    rows = presetH ? winH : winD; hourly = presetH;
  }
  const checked = (k) => !sl && k === state.range;

  $("ranges").innerHTML = RANGES.map(([k, label], i) => `<button type="button" role="radio" aria-checked="${checked(k)}" data-k="${k}" tabindex="${checked(k) || (sl && i === 0) ? 0 : -1}">${label}</button>`).join("");
  $("ctype").innerHTML = TYPES.map(([k, label]) => `<button type="button" role="radio" aria-checked="${k === state.type}" data-k="${k}" tabindex="${k === state.type ? 0 : -1}" title="${label}" aria-label="${label} charts">${TYPE_ICONS[k]}<span class="lbl">${label}</span></button>`).join("");
  $("by").innerHTML = [["cost", "Cost"], ["tokens", "Tokens"]].map(([k, l]) => `<button type="button" role="radio" aria-checked="${k === state.by}" data-k="${k}" tabindex="${k === state.by ? 0 : -1}">${l}</button>`).join("");

  // time slice controls
  $("slice").hidden = !hourlyAll.length;
  if (hourlyAll.length) {
    const first = hourlyAll[0].hour.slice(0, 16), end = plusHours(hourlyAll[hourlyAll.length - 1].hour.slice(0, 16), 1);
    for (const id of ["slice-from", "slice-to"]) { $(id).min = first; $(id).max = end; }
    $("slice-from").value = sl ? sl.from : ""; $("slice-to").value = sl ? sl.to : "";
    $("slice-reset").disabled = !sl;
    $("slice").classList.toggle("on", !!sl);
  }
  $("basis").innerHTML = sl ? `Showing <b>${esc(sliceLabel(sl.from))} to ${esc(sliceLabel(sl.to))}</b> (${winH.length} hour${winH.length === 1 ? "" : "s"}). Everything below uses only this slice, from OpenCode's hourly estimates; the per-day cards show whole days.`
    : hourly ? "Hourly numbers are a <b>local estimate</b> from OpenCode. The portal's spend isn't split by hour."
    : "Hourly cards are a local estimate. Daily cost is portal spend where known, plus a local estimate for the rest.";

  const pie = state.type === "pie", flat = state.type !== "bar";
  const sumIn = (list, k) => list.reduce((a, r) => a + (r[k] || 0), 0);
  const sumOf = (k) => sumIn(rows, k);
  const sel = (from, to) => setSlice(from.slice(0, 16), plusHours(to.slice(0, 16), 1));
  const noHours = hourlyAll.length ? "No hours in this slice." : "No hourly data. It comes from OpenCode's local usage records.";
  const tokenPie = (list) => () => [["output", "c-output", "s-output", sumIn(list, "output")], ["input", "c-input", "s-input", sumIn(list, "input")], ["cache read", "c-cache", "s-cache", sumIn(list, "cache_read")], ["cache write", "c-cw", "s-cw", sumIn(list, "cache_write")]]
    .map(([name, fill, sw, value]) => ({ name, fill, sw, value, text: tokens(value) }));
  const tokenStacks = [["cache", "c-cache", "cache", "s-cache", (r) => r.cache], ["input", "c-input", "input", "s-input", (r) => r.input], ["output", "c-output", "output", "s-output", (r) => r.output]];
  const tokenTip = (r) => [["total", tokens(r.tokens)], ["input", tokens(r.input || 0)], ["output", tokens(r.output || 0)], ["cache read", tokens(r.cache_read || 0)], ["cache write", tokens(r.cache_write || 0)], ["messages", r.messages], ...topModels(r).map(([m, c]) => [m, money(c, r.hour ? usd : cur)])];

  // 1) tokens per hour (always hourly)
  $("th-cap").textContent = !winH.length ? "" : pie ? "Share by token type · local estimate" : `${tokens(sumIn(winH, "tokens"))} in total · ${sumIn(winH, "messages").toLocaleString()} messages · drag to zoom · local estimate`;
  $("th-legend").innerHTML = pie ? "" : '<span><i class="s-output"></i>output</span><span><i class="s-input"></i>input</span><span><i class="s-cache"></i>cache</span><span><i class="dash"></i>6h average</span>';
  drawTime($("th-chart"), { rows: winH, hourly: true, empty: winH.length ? "" : noHours, avg: 6, label: "Tokens per hour", value: (r) => r.tokens, fmt: axisNum, stacks: tokenStacks, tip: tokenTip, onSelect: sel }, tokenPie(winH), { center: "tokens", fmt: tokens });

  // 2) cost per hour, in dollars only
  $("ch-cap").textContent = !winH.length ? "" : pie ? "Share by model · local estimate" : `${money(sumIn(winH, "cost"), usd)} in total · local estimate (calibrated), not billed credits · drag to zoom`;
  $("ch-legend").innerHTML = pie ? "" : `<span><i class="s-est"></i>estimated cost in dollars (OpenCode's estimate × calibration factor)</span>`;
  drawTime($("ch-chart"), { rows: winH, hourly: true, empty: winH.length ? "" : noHours, label: "Cost per hour", value: (r) => r.cost, fmt: (v) => axisMoney(v, usd),
    stacks: [["est", "c-est", "estimated cost", "s-est", (r) => r.cost]], series: [["cost", "c-est", "cost", "s-est", (r) => r.cost]],
    tip: (r) => [["cost", money(r.cost, usd)], ["tokens", tokens(r.tokens)], ["messages", r.messages], ...topModels(r).map(([m, c]) => [m, money(c, usd)])], onSelect: sel },
    () => paletteItems(modelTotals(winH), "cost", 6, (v) => money(v, usd)), { center: "cost", fmt: (v) => money(v, usd), empty: "No per-model data in this slice." });

  // 3) per day (portal spend where known); a slice or an hourly range shows the whole days it touches
  const dayNote = sl || presetH ? " · whole days" : "";
  $("t-cap").textContent = !winD.length ? "" : pie ? "Share by token type · local estimate" : `${tokens(sumIn(winD, "tokens"))} in total · ${sumIn(winD, "messages").toLocaleString()} messages · local estimate${dayNote}`;
  $("c-cap").textContent = !winD.length ? "" : pie ? "Share by model · estimate" : `${money(sumIn(winD, "cost"), cur)} in total${dayNote}`;
  $("t-legend").innerHTML = pie ? "" : '<span><i class="s-output"></i>output</span><span><i class="s-input"></i>input</span><span><i class="s-cache"></i>cache</span><span><i class="dash"></i>7d average</span>';
  $("c-legend").innerHTML = pie ? "" : flat ? '<span><i class="s-cost"></i>total cost (portal spend where known, estimate for the rest)</span>'
    : '<span><i class="s-cost"></i>portal spend</span><span><i class="s-est"></i>local estimate</span>';
  drawTime($("tokens-chart"), { rows: winD, hourly: false, empty: winD.length ? "" : "No days in this range.", avg: 7, label: "Tokens per day", value: (r) => r.tokens, fmt: axisNum, stacks: tokenStacks, tip: tokenTip }, tokenPie(winD), { center: "tokens", fmt: tokens });
  drawTime($("cost-chart"), {
    rows: winD, hourly: false, empty: winD.length ? "" : "No days in this range.", label: "Cost per day", value: (r) => r.cost, fmt: (v) => axisMoney(v, cur),
    stacks: [["est", "c-est", "local estimate", "s-est", (r) => Math.max(0, r.cost - (r.cost_true || 0))], ["cost", "c-cost", "portal spend", "s-cost", (r) => r.cost_true || 0]],
    series: [["cost", "c-cost", "cost", "s-cost", (r) => r.cost]],
    tip: (r) => [["total", money(r.cost, cur)], ["portal", money(r.cost_true || 0, cur)], ["estimate", money(Math.max(0, r.cost - (r.cost_true || 0)), cur)], ["tokens", tokens(r.tokens)], ...topModels(r).map(([m, c]) => [m, money(c, cur)])],
  }, () => paletteItems(modelTotals(winD), "cost", 6, (v) => money(v, cur)), { center: "cost", fmt: (v) => money(v, cur), empty: "No per-model data in this range." });

  tokenPanels(rows, hourly);
  models(rows, hourly ? usd : cur);
  let last = entry.skill_last || {};
  if (hourly) { last = {}; for (const r of rows) for (const k of Object.keys(r.skills || {})) last[k] = r.hour; }
  skills(rows, last);
  $("foot").textContent = "Cost is OpenCode's own estimate, not billed credits. Only usage OpenCode recorded has a per-model breakdown.";
}

function tokenPanels(rows, hourly) {
  const sum = (k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
  const input = sum("input"), output = sum("output"), cr = sum("cache_read"), cw = sum("cache_write");
  const all = input + output + cr + cw, cache = cr + cw;
  const pct = (v) => (all ? (v / all) * 100 : 0);
  const share = (v) => { const p = pct(v); return p > 0 && p < 1 ? "<1%" : `${Math.round(p)}%`; };
  $("k-cap").textContent = "Local estimate: only what OpenCode recorded, not Copilot use in editors or the CLI. Output includes reasoning tokens.";
  const t = (cls, label, v, small) => `<div><dt><i class="${cls}"></i>${label}</dt><dd>${numSpan("k:" + label, v, tokens)}<small>${small}</small></dd></div>`;
  $("tok-stats").innerHTML = t("s-input", "Input", input, `${share(input)} of all tokens`) + t("s-output", "Output", output, `${share(output)} of all tokens`) +
    t("s-cache", "Cache read", cr, `${share(cr)} of all tokens`) + t("s-cw", "Cache write", cw, `${share(cw)} of all tokens`) +
    `<div><dt>Cache share</dt><dd>${share(cache)}<small>${tokens(all)} tokens in total</small></dd></div>`;
  settleNumbers($("tok-stats"));
  const unit = hourly ? "hour" : "day", empty = hourly && !rows.length ? "No hourly data. It comes from OpenCode's local usage records." : "";
  $("io-cap").textContent = rows.length ? `${tokens(input)} in · ${tokens(output)} out · local estimate` : "";
  $("io-legend").innerHTML = '<span><i class="s-input"></i>input</span><span><i class="s-output"></i>output</span>';
  $("ca-cap").textContent = rows.length ? `${tokens(cr)} read · ${tokens(cw)} written · local estimate` : "";
  $("ca-legend").innerHTML = '<span><i class="s-cache"></i>cache read</span><span><i class="s-cw"></i>cache write</span>';
  const pair = (a, b) => ({
    rows, hourly, empty, grouped: true, fmt: axisNum, stacks: [a, b], value: (r) => Math.max(a[4](r), b[4](r)),
    tip: (r) => [[a[2], tokens(a[4](r))], [b[2], tokens(b[4](r))], ["messages", r.messages]],
  });
  const pie = state.type === "pie";
  if (pie) { $("io-cap").textContent = rows.length ? "Share of input vs output · local estimate" : ""; $("ca-cap").textContent = rows.length ? "Cache read vs write · local estimate" : ""; $("io-legend").innerHTML = ""; $("ca-legend").innerHTML = ""; }
  drawTime($("io-chart"), { ...pair(["input", "c-input", "input", "s-input", (r) => r.input || 0], ["output", "c-output", "output", "s-output", (r) => r.output || 0]), label: `Input and output tokens per ${unit}` },
    () => [["input", "c-input", "s-input", input], ["output", "c-output", "s-output", output]].map(([name, fill, sw, value]) => ({ name, fill, sw, value, text: tokens(value) })).sort((x, y) => y.value - x.value), { center: "tokens", fmt: tokens });
  drawTime($("cache-chart"), { ...pair(["cr", "c-cache", "cache read", "s-cache", (r) => r.cache_read || 0], ["cw", "c-cw", "cache write", "s-cw", (r) => r.cache_write || 0]), label: `Cache tokens per ${unit}` },
    () => [["cache read", "c-cache", "s-cache", cr], ["cache write", "c-cw", "s-cw", cw]].map(([name, fill, sw, value]) => ({ name, fill, sw, value, text: tokens(value) })).sort((x, y) => y.value - x.value), { center: "tokens", fmt: tokens });
}

function lastUsed(iso) {
  if (!iso) return "";
  const d = new Date(iso), now = new Date(), time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
  return days === 0 ? `today ${time}` : days === 1 ? `yesterday ${time}` : `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

function skills(rows, lastMap) {
  const totals = {};
  for (const r of rows) for (const [n, c] of Object.entries(r.skills || {})) totals[n] = (totals[n] || 0) + c;
  const ranked = Object.entries(totals).filter(([, c]) => c > 0).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const sum = ranked.reduce((a, [, c]) => a + c, 0) || 1;
  const top = ranked.slice(0, 8), rest = ranked.slice(8);
  const items = top.map(([n, c]) => ({ name: n, count: c, last: lastMap[n], other: false }));
  if (rest.length) items.push({ name: `Other (${rest.length})`, count: rest.reduce((a, [, c]) => a + c, 0), other: true });
  $("sk-cap").textContent = items.length ? `${sum.toLocaleString()} loads · selected range · counts, not cost` : "How often OpenCode loaded each skill";
  if (state.type === "pie" && items.length) {
    $("sk-cap").textContent = `${sum.toLocaleString()} loads · share of loads · counts, not cost`;
    return rankedPie($("skill-list"), paletteFrom(items.map((it) => ({ name: it.name, value: it.count, text: `${it.count.toLocaleString()} ${it.count === 1 ? "load" : "loads"}`, other: it.other })), 7), { center: "loads", fmt: (v) => v.toLocaleString() });
  }
  $("skill-list").className = "rank" + (state.type === "dots" ? " lolli" : "");
  if (state.type === "line") $("sk-cap").textContent += " · line view shows bars here";
  $("skill-list").innerHTML = items.length ? items.map((it) => {
    const share = it.count / sum;
    return `<li data-name="${esc(it.name)}" class="${it.other ? "other" : ""}"><span class="name" title="${esc(it.name)}">${esc(it.name)}</span>
      <span class="num">${it.count.toLocaleString()} ${it.count === 1 ? "load" : "loads"}<b>${share >= 0.005 ? Math.round(share * 100) + "%" : "<1%"}</b></span>
      <span class="bar"><span data-w="${Math.max(1.5, share * 100)}"></span></span>
      ${it.last ? `<span class="sub">last used ${esc(lastUsed(it.last))}</span>` : ""}</li>`;
  }).join("") : '<li class="none">No skills were loaded in this range. Loads come from OpenCode\'s local records.</li>';
  settleRank($("skill-list"));
}

function topModels(r, count = 2) {
  return Object.entries(r.models || {}).sort((a, b) => b[1].cost - a[1].cost).slice(0, count).map(([m, v]) => [shortModel(m), v.cost]);
}
const shortModel = (m) => String(m || "unknown").split("/").pop();

function models(rows, cur) {
  const totals = {};
  for (const r of rows) for (const [m, v] of Object.entries(r.models || {})) {
    const t = (totals[shortModel(m)] ||= { tokens: 0, cost: 0 });
    t.tokens += v.tokens || 0; t.cost += v.cost || 0;
  }
  const key = state.by;
  const ranked = Object.entries(totals).filter(([, v]) => v.tokens || v.cost).sort((a, b) => b[1][key] - a[1][key]);
  const sum = ranked.reduce((a, [, v]) => a + v[key], 0) || 1;
  const top = ranked.slice(0, 6), rest = ranked.slice(6);
  const items = top.map(([n, v]) => ({ name: n, ...v, other: false }));
  if (rest.length) items.push({ name: `Other (${rest.length})`, tokens: rest.reduce((a, [, v]) => a + v.tokens, 0), cost: rest.reduce((a, [, v]) => a + v.cost, 0), other: true });
  $("m-cap").textContent = items.length ? `Ranked by ${key} · selected range · local estimate` : "";
  if (state.type === "pie" && items.length) {
    $("m-cap").textContent = `Share of ${key} · selected range · local estimate`;
    return rankedPie($("model-list"), paletteFrom(items.map((it) => ({ name: it.name, value: it[key], text: `${tokens(it.tokens)} · ${money(it.cost, cur)}`, other: it.other })), 7), { center: key, fmt: key === "cost" ? (v) => money(v, cur) : tokens });
  }
  $("model-list").className = "rank" + (state.type === "dots" ? " lolli" : "");
  if (state.type === "line" && items.length) $("m-cap").textContent += " · line view shows bars here";
  $("model-list").innerHTML = items.length ? items.map((it, i) => {
    const share = it[key] / sum;
    return `<li data-name="${esc(it.name)}" class="${it.other ? "other" : ""}"><span class="name" title="${esc(it.name)}">${esc(it.name)}</span>
      <span class="num">${tokens(it.tokens)} · ${money(it.cost, cur)}<b>${share >= 0.005 ? Math.round(share * 100) + "%" : "<1%"}</b></span>
      <span class="bar"><span data-w="${Math.max(1.5, share * 100)}"></span></span></li>`;
  }).join("") : '<li class="none">No per-model data in this range.</li>';
  settleRank($("model-list"));
}

/* ------------------------------------------------------------------ pie / donut */
function modelTotals(rows) {
  const totals = {};
  for (const r of rows) for (const [m, v] of Object.entries(r.models || {})) {
    const t = (totals[shortModel(m)] ||= { tokens: 0, cost: 0 });
    t.tokens += v.tokens || 0; t.cost += v.cost || 0;
  }
  return totals;
}
/* items with a palette colour each (p1..p7); everything past `max` and the "Other" entry share the grey slice */
function paletteFrom(list, max) {
  const real = list.filter((it) => !it.other && it.value > 0), extra = list.filter((it) => it.other && it.value > 0);
  const keep = real.slice(0, max), rest = [...real.slice(max), ...extra];
  const out = keep.map((it, i) => ({ ...it, fill: `c-p${i + 1}`, sw: `s-p${i + 1}` }));
  if (rest.length) out.push({ name: extra.length && rest.length === 1 ? extra[0].name : `Other (${rest.length})`, value: rest.reduce((a, it) => a + it.value, 0), text: extra.length && rest.length === 1 ? extra[0].text : "", fill: "c-p8", sw: "s-p8" });
  return out;
}
function paletteItems(totals, key, max, fmt) {
  const ranked = Object.entries(totals).filter(([, v]) => v[key] > 0).sort((x, y) => y[1][key] - x[1][key]);
  return paletteFrom(ranked.map(([name, v]) => ({ name, value: v[key], text: fmt(v[key]) })), max);
}
function rankedPie(list, items, o) {
  list.className = "rank pie";
  list.innerHTML = "<li></li>";
  donut(list.firstChild, items, { id: list.id, ...o });
}

function donut(host, items, o) {
  host.innerHTML = "";
  const total = items.reduce((a, it) => a + it.value, 0);
  if (!(total > 0)) { host.innerHTML = `<p class="nodata">${esc(o.empty || "No data in this range.")}</p>`; return; }
  const wrap = document.createElement("div");
  wrap.className = "donut";
  const C = 100, R = 92, r = 58, TAU = Math.PI * 2;
  const svg = el("svg", { viewBox: "0 0 200 200", role: "img", tabindex: 0, "aria-label": `Pie chart: ${items.map((it) => `${it.name} ${Math.round((it.value / total) * 100)}%`).join(", ")}. Use arrow keys to move between slices.` });
  const pt = (rad, ang) => [C + rad * Math.cos(ang), C + rad * Math.sin(ang)].map((n) => n.toFixed(2));
  const arcPath = (a0, a1) => {
    const frac = (a1 - a0) / TAU;
    if (frac > 0.9995) return `M${C} ${C - R}A${R} ${R} 0 1 1 ${C} ${C + R}A${R} ${R} 0 1 1 ${C} ${C - R}ZM${C} ${C - r}A${r} ${r} 0 1 0 ${C} ${C + r}A${r} ${r} 0 1 0 ${C} ${C - r}Z`;
    const big = frac > 0.5 ? 1 : 0, [x0, y0] = pt(R, a0), [x1, y1] = pt(R, a1), [x2, y2] = pt(r, a1), [x3, y3] = pt(r, a0);
    return `M${x0} ${y0}A${R} ${R} 0 ${big} 1 ${x1} ${y1}L${x2} ${y2}A${r} ${r} 0 ${big} 0 ${x3} ${y3}Z`;
  };
  const pk = "pie|" + (o.id || host.id), before = prevStore[pk], moving = canMove() && !!before;
  const slices = [], final = [], begin = [], saved = { total, items: {} };
  let a0 = -Math.PI / 2;
  items.forEach((it) => {
    const a1 = a0 + (it.value / total) * TAU, old = moving && before.items[it.name];
    final.push([a0, a1]);
    begin.push(old ? old.a : [a0, a0]);                  // a slice that is new grows out of nothing
    saved.items[it.name] = { a: [a0, a1], pc: Math.round((it.value / total) * 100) };
    const path = el("path", { class: `slice ${it.fill}`, "fill-rule": "evenodd", d: arcPath(...(moving ? begin[begin.length - 1] : [a0, a1])) });
    svg.appendChild(path);
    slices.push(path);
    a0 = a1;
  });
  prevStore[pk] = saved;
  const big = el("text", { class: "dbig", x: C, y: C + 2, "text-anchor": "middle" }, o.fmt(moving ? before.total : total));
  const small = el("text", { class: "dsmall", x: C, y: C + 20, "text-anchor": "middle" }, o.center || "");
  svg.appendChild(big); svg.appendChild(small);
  wrap.appendChild(svg);
  const legend = document.createElement("ul");
  legend.className = "dleg";
  const pc = (v) => { const p = (v / total) * 100; return p > 0 && p < 1 ? "<1%" : `${Math.round(p)}%`; };
  items.forEach((it) => {
    const li = document.createElement("li");
    li.innerHTML = `<i class="${it.sw}"></i><span class="n" title="${esc(it.name)}">${esc(it.name)}</span><span class="v">${esc(it.text || "")}</span><b>${pc(it.value)}</b>`;
    legend.appendChild(li);
    const was = moving && before.items[it.name];
    if (was && was.pc !== Math.round((it.value / total) * 100)) flash(li.querySelector("b"));
  });
  wrap.appendChild(legend);
  const tip = document.createElement("div");
  tip.className = "tip";
  wrap.appendChild(tip);
  host.appendChild(wrap);
  let current = null;
  function show(i, ev) {
    const on = i != null && i >= 0 && i < items.length;
    slices.forEach((s, k) => s.classList.toggle("on", on && k === i));
    legend.querySelectorAll("li").forEach((l, k) => l.classList.toggle("on", on && k === i));
    svg.classList.toggle("hover", on);
    current = on ? i : null;
    if (!on) { big.textContent = o.fmt(total); small.textContent = o.center || ""; tip.classList.remove("on"); return; }
    big.textContent = pc(items[i].value); small.textContent = items[i].name.length > 16 ? items[i].name.slice(0, 15) + "…" : items[i].name;
    tip.innerHTML = `<b>${esc(items[i].name)}</b><div class="r"><span>${esc(o.center || "value")}</span><span>${esc(o.fmt(items[i].value))}</span></div><div class="r"><span>share</span><span>${pc(items[i].value)}</span></div>`;
    tip.classList.add("on");
    const box = wrap.getBoundingClientRect(), s = slices[i].getBoundingClientRect();
    const px = ev ? ev.clientX - box.left : s.left - box.left + s.width / 2, py = ev ? ev.clientY - box.top : s.top - box.top + s.height / 2;
    tip.style.left = Math.max(0, Math.min(box.width - tip.offsetWidth, px + 14)) + "px";
    tip.style.top = Math.max(0, py - tip.offsetHeight - 8) + "px";
  }
  slices.forEach((s, k) => { s.addEventListener("pointermove", (ev) => show(k, ev)); s.addEventListener("pointerdown", (ev) => show(k, ev)); });
  svg.addEventListener("pointerleave", () => show(null));
  legend.querySelectorAll("li").forEach((l, k) => { l.addEventListener("pointerenter", () => show(k)); l.addEventListener("pointerleave", () => show(null)); });
  svg.addEventListener("blur", () => show(null));
  if (moving) {
    const changed = final.some((f, i) => Math.abs(f[0] - begin[i][0]) + Math.abs(f[1] - begin[i][1]) > 0.004) || before.total !== total;
    if (changed) {
      if (before.total !== total) flash(big);
      animate((e) => {
        slices.forEach((p, i) => { if (p.isConnected) p.setAttribute("d", arcPath(lerp(begin[i][0], final[i][0], e), Math.max(lerp(begin[i][0], final[i][0], e), lerp(begin[i][1], final[i][1], e)))); });
        if (current == null && big.isConnected) big.textContent = o.fmt(lerp(before.total, total, e));
      });
    } else big.textContent = o.fmt(total);
  }
  svg.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowRight" || ev.key === "ArrowDown") show(((current ?? -1) + 1) % items.length);
    else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") show(((current ?? 0) - 1 + items.length) % items.length);
    else if (ev.key === "Escape") show(null);
    else return;
    ev.preventDefault();
  });
}

/* one entry point for the time-series cards: bar / line / dots draw by day or hour, pie shows the range's split */
function drawTime(host, cfg, pieItems, pieOpts) {
  if (state.type === "pie") {
    if (!cfg.rows.length) { host.innerHTML = `<svg viewBox="0 0 280 230" role="img"><text x="140" y="115" text-anchor="middle" class="none">${esc(cfg.empty || "No data in this range.")}</text></svg>`; return; }
    return donut(host, pieItems(), { empty: "No data in this range.", ...pieOpts });
  }
  barChart(host, { ...cfg, type: state.type });
}

/* ------------------------------------------------------------------ chart */
function el(name, attrs = {}, text) {
  const node = document.createElementNS(svgNS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
}

function barChart(host, cfg) {
  host._cfg = cfg;
  host.innerHTML = "";
  const rows = cfg.rows, W = Math.max(280, host.clientWidth || 520), H = 230;
  const m = { l: 44, r: 8, t: 8, b: 26 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", tabindex: 0, "aria-label": `${cfg.label} chart. ${rows.length} ${cfg.hourly ? "hours" : "days"}. Use left and right arrow keys to read values.` });
  host.appendChild(svg);
  if (!rows.length || cfg.empty) { host._prev = null; svg.appendChild(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "none" }, cfg.empty || "No data in this range.")); return; }
  const x0 = m.l, x1 = W - m.r, top = m.t, bottom = H - m.b, slot = (x1 - x0) / rows.length;
  const [step, ceil] = niceScale(Math.max(...rows.map(cfg.value), 0.0001) * 1.04);
  const y = (v) => bottom - ((bottom - top) * v) / ceil;
  for (let k = 0; k <= 4; k++) {
    const yy = y(step * k);
    svg.appendChild(el("line", { class: "grid-line", x1: x0, x2: x1, y1: yy, y2: yy }));
    svg.appendChild(el("text", { x: x0 - 6, y: yy + 4, "text-anchor": "end" }, cfg.fmt(step * k)));
  }
  // x labels: round hours for hourly (the date at midnight), every n-th day otherwise
  if (cfg.hourly) {
    const every = [1, 2, 3, 4, 6, 8, 12, 24, 48, 72, 96, 168, 336].find((e) => e >= Math.ceil(42 / slot)) || 336;
    const dayNo = (r) => Math.floor(Date.UTC(+r.hour.slice(0, 4), +r.hour.slice(5, 7) - 1, +r.hour.slice(8, 10)) / 864e5);
    rows.forEach((r, i) => { const h = hourOf(r); if (every >= 24 ? (h === 0 && dayNo(r) % (every / 24) === 0) : h % every === 0) svg.appendChild(el("text", { x: x0 + (i + 0.5) * slot, y: H - 8, "text-anchor": "middle" }, h === 0 ? dayLabel(r.hour) : `${String(h).padStart(2, "0")}:00`)); });
  } else {
    const every = Math.max(1, Math.ceil(46 / slot));
    rows.forEach((r, i) => { if ((rows.length - 1 - i) % every === 0) svg.appendChild(el("text", { x: x0 + (i + 0.5) * slot, y: H - 8, "text-anchor": "middle" }, +r.day.slice(8, 10) === 1 || i === 0 || every === 1 && rows.length <= 8 ? dayLabel(r.day) : String(+r.day.slice(8, 10)))); });
  }
  if (cfg.onSelect) host.classList.add("selectable"); else host.classList.remove("selectable");
  const rk = (r) => r.hour || r.day;
  const prevMap = (host._prevType === (cfg.type || "bar") && host._prev) || null, nextMap = {}, frames = [];
  const animOn = canMove() && !!prevMap;
  const hov = el("rect", { class: "hov", y: top - 4, height: bottom - top + 4, width: slot, visibility: "hidden" });
  svg.appendChild(hov);
  const bw = slot < 3 ? Math.max(1, slot * 0.9) : Math.max(2, Math.min(slot * 0.68, 34));
  const type = cfg.type || "bar", marks = [];
  if (type === "line" || type === "dots") {
    const series = cfg.series || cfg.stacks, px = (i) => x0 + (i + 0.5) * slot;
    const rad = Math.max(2, Math.min(slot * 0.26, 4.5));
    series.forEach(([, cls, , , get], si) => {
      const fin = rows.map((r) => y(get(r)));
      const from = fin.map((v, i) => { const key = `L${si}|${rk(rows[i])}`; nextMap[key] = v; return animOn && prevMap[key] !== undefined ? prevMap[key] : v; });
      const moved = from.some((v, i) => Math.abs(v - fin[i]) > 0.5);
      const xs = rows.map((_, i) => px(i));
      let lineEl = null, areaEl = null;
      if (type === "line") {
        if (rows.length > 1) { areaEl = el("path", { class: `ar ${cls}` }); svg.appendChild(areaEl); }
        lineEl = el("path", { class: `ln ${cls}` }); svg.appendChild(lineEl);
      }
      const dots = [];
      if (type === "dots" || rows.length <= 16) rows.forEach((r, i) => { if (get(r) > 0 || type === "dots") { const c = el("circle", { class: `dot ${cls}`, cx: xs[i], r: type === "dots" ? rad : Math.min(rad, 3) }); svg.appendChild(c); dots.push([c, i]); } });
      const paint = (ys) => {
        if (lineEl) {
          const line = "M" + ys.map((v, i) => `${xs[i].toFixed(1)},${v.toFixed(1)}`).join("L");
          lineEl.setAttribute("d", line);
          if (areaEl) areaEl.setAttribute("d", `${line}L${xs[xs.length - 1].toFixed(1)},${bottom}L${xs[0].toFixed(1)},${bottom}Z`);
        }
        dots.forEach(([c, i]) => c.setAttribute("cy", ys[i]));
      };
      paint(moved ? from : fin);
      if (moved) frames.push((e) => paint(from.map((v, i) => lerp(v, fin[i], e))));
      const mk = el("circle", { class: `mk ${cls}`, r: rad + 1.5, visibility: "hidden" });
      svg.appendChild(mk);
      marks.push([mk, get]);
    });
  }
  // a bar keeps its final size in the DOM; on a refresh it starts from its old size (or from nothing) and glides
  const bind = (key, bar, fy, fh) => {
    nextMap[key + ":y"] = fy; nextMap[key + ":h"] = fh;
    if (!animOn) return;
    const py = prevMap[key + ":y"] ?? bottom, ph = prevMap[key + ":h"] ?? 0;
    if (Math.abs(py - fy) < 0.5 && Math.abs(ph - fh) < 0.5) return;
    bar.setAttribute("y", py); bar.setAttribute("height", ph);
    frames.push((e) => { bar.setAttribute("y", lerp(py, fy, e)); bar.setAttribute("height", lerp(ph, fh, e)); });
  };
  const gw = cfg.grouped ? Math.max(2, Math.min(slot * 0.38, 16)) : 0;
  if (type !== "bar") { /* drawn above */ }
  else if (cfg.grouped) rows.forEach((r, i) => cfg.stacks.forEach(([, cls, , , get], k) => {
    const h = bottom - y(get(r));
    if (h <= 0) return;
    const bar = el("rect", { class: cls, x: x0 + (i + 0.5) * slot + (k - cfg.stacks.length / 2) * gw + 0.5, width: Math.max(1, gw - 1), y: bottom - h, height: h, rx: Math.min(3, gw / 2) });
    svg.appendChild(bar);
    bind(`g${k}|${rk(r)}`, bar, bottom - h, h);
  }));
  else rows.forEach((r, i) => {
    let base = bottom;
    cfg.stacks.forEach(([, cls, , , get], si) => {
      const h = (bottom - y(get(r)));
      if (h <= 0) return;
      const bar = el("rect", { class: cls, x: x0 + (i + 0.5) * slot - bw / 2, width: bw, y: base - h, height: h, rx: Math.min(3, bw / 2) });
      svg.appendChild(bar);
      bind(`s${si}|${rk(r)}`, bar, base - h, h);
      base -= h;
    });
  });
  if (cfg.avg) {
    const vals = rows.map(cfg.value);
    const pts = vals.map((_, i) => { const w = vals.slice(Math.max(0, i - cfg.avg + 1), i + 1); return [x0 + (i + 0.5) * slot, y(w.reduce((a, b) => a + b, 0) / w.length)]; });
    const avgEl = el("path", { class: "avg" });
    svg.appendChild(avgEl);
    const fin = pts.map((p) => p[1]), from = fin.map((v, i) => { const key = `A|${rk(rows[i])}`; nextMap[key] = v; return animOn && prevMap[key] !== undefined ? prevMap[key] : v; });
    const paintAvg = (ys) => avgEl.setAttribute("d", "M" + ys.map((v, i) => `${pts[i][0]},${v}`).join("L"));
    paintAvg(from);
    if (from.some((v, i) => Math.abs(v - fin[i]) > 0.5)) frames.push((e) => paintAvg(from.map((v, i) => lerp(v, fin[i], e))));
  }
  host._prev = nextMap; host._prevType = type;
  if (frames.length) animate((e) => frames.forEach((f) => f(e)));
  // hover / touch / keyboard
  const tip = document.createElement("div");
  tip.className = "tip";
  host.appendChild(tip);
  let current = null;
  function show(i) {
    if (i == null || i < 0 || i >= rows.length) { current = null; marks.forEach(([mk]) => mk.setAttribute("visibility", "hidden")); hov.setAttribute("visibility", "hidden"); tip.classList.remove("on"); return; }
    current = i;
    const r = rows[i];
    hov.setAttribute("x", x0 + i * slot); hov.setAttribute("visibility", "visible");
    marks.forEach(([mk, get]) => { mk.setAttribute("cx", x0 + (i + 0.5) * slot); mk.setAttribute("cy", y(get(rows[i]))); mk.setAttribute("visibility", "visible"); });
    tip.innerHTML = `<b>${esc(cfg.hourly ? hourTitle(r) : dayLong(r.day))}</b>` + cfg.tip(r).map(([a, b]) => `<div class="r"><span>${esc(a)}</span><span>${esc(b)}</span></div>`).join("");
    const scale = host.clientWidth / W, cx = (x0 + (i + 0.5) * slot) * scale;
    tip.classList.add("on");
    const w = tip.offsetWidth;
    tip.style.left = Math.max(0, Math.min(host.clientWidth - w, cx + 12 + w > host.clientWidth ? cx - 12 - w : cx + 12)) + "px";
    tip.style.top = "6px";
  }
  const at = (ev) => { const b = svg.getBoundingClientRect(); const x = ((ev.clientX - b.left) / b.width) * W; return x >= x0 && x < x1 ? Math.floor((x - x0) / slot) : null; };
  // drag across an hourly chart to pick a time slice (cfg.onSelect); a plain click or hover only shows the tooltip
  const clampAt = (ev) => { const b = svg.getBoundingClientRect(); const x = ((ev.clientX - b.left) / b.width) * W; return Math.max(0, Math.min(rows.length - 1, Math.floor((x - x0) / slot))); };
  let drag = null, sel = null;
  const drawSel = () => {
    const a = Math.min(drag.i0, drag.i1), b = Math.max(drag.i0, drag.i1);
    if (!sel) { sel = el("rect", { class: "sel", y: top - 4, height: bottom - top + 4 }); svg.appendChild(sel); }
    sel.setAttribute("x", x0 + a * slot); sel.setAttribute("width", (b - a + 1) * slot);
  };
  const endDrag = (ev, apply) => {
    if (!drag) return;
    const a = Math.min(drag.i0, drag.i1), b = Math.max(drag.i0, drag.i1);
    drag = null;
    if (sel) { sel.remove(); sel = null; }
    try { svg.releasePointerCapture(ev.pointerId); } catch (e) { /* not captured */ }
    if (apply && b > a) { show(null); cfg.onSelect(rows[a].hour, rows[b].hour); }
  };
  svg.addEventListener("pointermove", (ev) => {
    if (drag) { drag.i1 = clampAt(ev); drawSel(); show(drag.i1); } else show(at(ev));
  });
  svg.addEventListener("pointerdown", (ev) => {
    const i = at(ev);
    show(i);
    if (cfg.onSelect && i != null && ev.button === 0) { drag = { i0: i, i1: i }; try { svg.setPointerCapture(ev.pointerId); } catch (e) { drag = null; } }
  });
  svg.addEventListener("pointerup", (ev) => endDrag(ev, true));
  svg.addEventListener("pointercancel", (ev) => endDrag(ev, false));
  svg.addEventListener("pointerleave", () => { if (!drag) show(null); });
  svg.addEventListener("blur", () => show(null));
  svg.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowRight") show(Math.min(rows.length - 1, (current ?? -1) + 1));
    else if (ev.key === "ArrowLeft") show(Math.max(0, (current ?? rows.length) - 1));
    else if (ev.key === "Escape") show(null);
    else return;
    ev.preventDefault();
  });
}

/* ------------------------------------------------------------------ settings drawer */
function toast(text) {
  const t = $("toast");
  t.textContent = text; t.classList.add("on");
  clearTimeout(toast.id); toast.id = setTimeout(() => t.classList.remove("on"), 2600);
}

async function loadSettings() {
  try { state.settings = await api("/api/settings"); } catch (e) { toast(e.message); return; }
  const b = state.settings.budget, cur = b.currency;
  $("budget-form").hidden = !b.available || b.readonly;
  $("budget-readonly").hidden = !b.readonly;
  $("budget-label").textContent = `Budget limit (${cur || "USD"})`;
  $("budget-hint").textContent = !b.available ? "No budget is available yet." : b.source ? `GitHub quota: ${money(b.source, cur)}. Leave it empty to use that.` : "The source reports no limit.";
  if (b.readonly) { $("budget-readonly").textContent = `Set in ${b.origin === ".env" ? "the .env file" : "the environment"} (TOKEN_MONITOR_BUDGET), currently ${money(b.effective, cur)}. Change it there.`; }
  if (document.activeElement !== $("budget-input")) $("budget-input").value = b.override || "";
  $("budget-reset").disabled = !b.override;
  $("budget-error").textContent = "";
  $("budget-input").classList.remove("bad");
  renderGroups(state.settings.groups || [], cur || "USD");
}

const closedGroups = new Set(JSON.parse((() => { try { return localStorage.getItem("tm-closed-groups") || "[]"; } catch (e) { return "[]"; } })()));  // ids of collapsed panels
function saveClosed() { try { localStorage.setItem("tm-closed-groups", JSON.stringify([...closedGroups])); } catch (e) { /* storage blocked */ } }

async function setHidden(kind, id, hidden) {
  await post("/api/settings", { hide: { kind, id, hidden } });
  state.payload = await api("/api/data");
  render();
  await loadSettings();
}

function makeSwitch(id, label, checked, onChange, disabled) {
  const box = document.createElement("input");
  Object.assign(box, { type: "checkbox", id, checked, disabled: !!disabled, className: "sw" });
  box.setAttribute("role", "switch");
  box.setAttribute("aria-label", label);
  box.addEventListener("change", async () => {
    try { await onChange(!box.checked); } catch (e) { box.checked = !box.checked; toast(e.message); }
  });
  return box;
}

function renderGroups(groups, cur) {
  const host = $("groups");
  host.innerHTML = "";
  $("mod-empty").hidden = groups.length > 0;
  groups.forEach((g, gi) => {
    const closed = closedGroups.has(String(g.id)), off = g.kind && g.hidden;
    const panel = document.createElement("section");
    panel.className = "group" + (off ? " off" : "");
    const body = `grp-body-${gi}`;
    const cost = g.models.reduce((a, m) => a + m.cost, 0), msgs = g.models.reduce((a, m) => a + m.messages, 0);
    const head = document.createElement("div");
    head.className = "ghead";
    head.innerHTML = `<button type="button" class="gtoggle" aria-expanded="${!closed}" aria-controls="${body}" ${g.models.length ? "" : "disabled"}>
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M8 5l8 7-8 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span class="t"><b>${esc(g.name)}</b><small>${g.models.length ? `${g.models.length} model${g.models.length === 1 ? "" : "s"}${msgs ? ` · ${money(cost, cur)} · ${msgs.toLocaleString()} messages` : ""}` : "no model data"}</small></span></button>`;
    if (g.kind) head.appendChild(makeSwitch(`sw-grp-${gi}`, `Show ${g.name}`, !g.hidden, (hidden) => setHidden(g.kind, g.id, hidden)));
    panel.appendChild(head);
    const list = document.createElement("ul");
    list.className = "switches gbody";
    list.id = body;
    list.hidden = closed || !g.models.length;
    g.models.forEach((m, mi) => {
      const li = document.createElement("li");
      const id = `sw-mod-${gi}-${mi}`;
      li.innerHTML = `<label class="t" for="${id}"><b>${esc(m.name)}</b><small>${m.messages ? `${esc(money(m.cost, cur))} · ${m.messages.toLocaleString()} messages` : "not used recently"}</small></label>`;
      li.appendChild(makeSwitch(id, `Show ${m.name}`, !m.hidden, (hidden) => setHidden("models", m.name, hidden), off));
      list.appendChild(li);
    });
    panel.appendChild(list);
    head.querySelector(".gtoggle").addEventListener("click", (ev) => {
      const open = list.hidden;
      list.hidden = !open;
      ev.currentTarget.setAttribute("aria-expanded", String(open));
      if (open) closedGroups.delete(String(g.id)); else closedGroups.add(String(g.id));
      saveClosed();
    });
    host.appendChild(panel);
  });
}

async function saveBudget(value) {
  const input = $("budget-input"), err = $("budget-error");
  err.textContent = ""; input.classList.remove("bad");
  try {
    state.settings = await post("/api/settings", { budget: value });
    toast(value ? "Budget limit saved" : "Using the GitHub value");
    state.busy = false;
    await load(true);
    loadSettings();
  } catch (e) { err.textContent = e.message; input.classList.add("bad"); }
}

/* ------------------------------------------------------------------ theme */
function applyTheme(theme, save) {
  document.documentElement.setAttribute("data-theme", theme);
  if (save) { try { localStorage.setItem("tm-theme", theme); } catch (e) { /* storage blocked */ } }
  $("theme").setAttribute("aria-label", theme === "light" ? "Switch to dark theme" : "Switch to light theme");
  const bar = document.querySelector('meta[name="theme-color"]');
  if (bar) bar.setAttribute("content", theme === "light" ? "#f1f4f9" : "#0a1424");
}
$("theme").addEventListener("click", () => applyTheme(document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light", true));
const scheme = window.matchMedia("(prefers-color-scheme: light)");
scheme.addEventListener("change", (ev) => { let saved = null; try { saved = localStorage.getItem("tm-theme"); } catch (e) { /* */ } if (!saved) applyTheme(ev.matches ? "light" : "dark", false); });
applyTheme(document.documentElement.getAttribute("data-theme") || "dark", false);

/* ------------------------------------------------------------------ wiring */
function radioGroup(host, onPick) {
  host.addEventListener("click", (ev) => { const b = ev.target.closest("button[data-k]"); if (b) onPick(b.dataset.k); });
  host.addEventListener("keydown", (ev) => {
    const keys = [...host.querySelectorAll("button")], at = keys.findIndex((b) => b.getAttribute("aria-checked") === "true");
    const move = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[ev.key];
    if (!move) return;
    ev.preventDefault();
    const next = keys[(at + move + keys.length) % keys.length];
    onPick(next.dataset.k);
    host.querySelector('button[aria-checked="true"]').focus();
  });
}
radioGroup($("ranges"), (k) => { state.slice = null; state.range = k; localStorage.setItem("tm-range", k); if (state.payload) render(); });
radioGroup($("ctype"), (k) => { state.type = k; try { localStorage.setItem("tm-chart-type", k); } catch (e) { /* storage blocked */ } if (state.payload) render(); });
radioGroup($("by"), (k) => { state.by = k; if (state.payload) render(); });
$("refresh").addEventListener("click", () => load(true));
$("open-settings").addEventListener("click", () => { $("settings").showModal(); loadSettings(); });
$("settings").addEventListener("click", (ev) => { if (ev.target === $("settings")) $("settings").close(); });
$("settings").addEventListener("close", () => $("open-settings").focus());
$("budget-apply").addEventListener("click", () => saveBudget($("budget-input").value));
$("budget-input").addEventListener("keydown", (ev) => { if (ev.key === "Enter") saveBudget($("budget-input").value); });
$("budget-reset").addEventListener("click", () => { $("budget-input").value = ""; saveBudget(""); });

function applyInputs() {
  const hours = state.payload && (state.payload.providers.find((e) => e.summary) || {}).hourly || [];
  const from = $("slice-from").value, to = $("slice-to").value;
  if (!hours.length) return;
  if (!from && !to) return clearSlice();
  const f = from ? from.slice(0, 13) + ":00" : hours[0].hour.slice(0, 16);
  const t = to ? to.slice(0, 13) + ":00" : plusHours(hours[hours.length - 1].hour.slice(0, 16), 1);
  if (!setSlice(f, t)) { toast("The end must be after the start."); if (state.payload) render(); }
}
$("slice-from").addEventListener("change", applyInputs);
$("slice-to").addEventListener("change", applyInputs);
$("slice-reset").addEventListener("click", clearSlice);

let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (state.payload) render(); }, 150); });
setInterval(() => { if (!document.hidden) load(); }, 60000);
setInterval(() => { if (state.payload && !document.hidden) { const e = state.payload.providers.find((x) => x.summary); if (e) hero(e); } }, 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
route();
load().then(() => { if (query.has("settings")) { $("settings").showModal(); loadSettings(); } });

// setup tab: copy buttons next to command blocks
document.addEventListener("click", (e) => {
  const btn = e.target.closest && e.target.closest(".cmd .copy");
  if (!btn) return;
  const text = btn.parentElement.querySelector("code").textContent;
  const done = () => { btn.textContent = "Copied"; setTimeout(() => { btn.textContent = "Copy"; }, 1500); };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => {});
  else { const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select(); try { document.execCommand("copy"); done(); } catch (_) {} t.remove(); }
});
