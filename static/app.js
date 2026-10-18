/* BTC 15-Minute Kalshi Analyzer — frontend (v26.6: single view — market + chart) */
const S = {
  history: 750, refreshSec: 3, paused: false,
  closeEpochMs: null, timer: null, quickTimer: null, countdownTimer: null,
  lastStateAt: null, flipLevels: null,
};

const $ = (id) => document.getElementById(id);
const money = (v) => v == null ? "—" : "$" + Number(v).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const cents = (v) => v == null ? "—" : Math.round(v) + "¢";
const pct1 = (v) => v == null ? "—" : (v * 100).toFixed(1) + "%";
const bannerCls = (cls) => cls === "up" ? "signal-up" : cls === "down" ? "signal-down" : "signal-wait";

/* ---------- state polling ---------- */
function stateParams() {
  // always auto: backend discovers the live market + target itself
  return new URLSearchParams({ history: S.history }).toString();
}

async function pollState() {
  if (S.paused) return;
  $("poll-status").textContent = "updating…";
  try {
    const r = await fetch("/api/state?" + stateParams());
    const d = await r.json();
    if (!d.ok) { showError("dash-error", d.error || "unknown error"); $("poll-status").textContent = "error"; return; }
    hideError("dash-error");
    renderMarket(d);
    S.lastStateAt = Date.now();
    $("poll-status").textContent = "updated just now";
  } catch (e) {
    showError("dash-error", "Could not reach the analyzer backend: " + e.message);
    $("poll-status").textContent = "error";
  }
}

function showError(id, msg) { const el = $(id); el.textContent = msg; el.classList.remove("hidden"); }
function hideError(id) { $(id).classList.add("hidden"); }

/* ---------- quick price tick (lightweight, every 2s) ---------- */
async function pollQuick() {
  if (S.paused) return;
  try {
    const r = await fetch("/api/quick");
    const d = await r.json();
    if (!d.ok) return;
    $("btc-price").textContent = money(d.price);
    $("m-btc-price").textContent = money(d.price);
    const ch = $("btc-change");
    if (d.change_1m_pct == null) {
      ch.textContent = "warming up…";
      ch.className = "card-sub";
    } else {
      ch.textContent = (d.change_1m_pct >= 0 ? "+" : "") + d.change_1m_pct.toFixed(3) + "% last minute";
      ch.className = "card-sub " + (d.change_1m_pct >= 0 ? "pos" : "neg");
    }
    $("btc-source-label").textContent = "BTC spot (" + d.source + ")";
  } catch (e) { /* silent: full poll will surface real errors */ }
}

/* ---------- market panel (single view: call card + essentials) ---------- */
function renderMarket(d) {
  $("btc-source-label").textContent = "BTC spot (" + d.btc.source + ")";
  $("btc-price").textContent = money(d.btc.price);
  $("m-btc-price").textContent = money(d.btc.price);
  const ch = $("btc-change");
  ch.textContent = (d.btc.change_1m_pct >= 0 ? "+" : "") + d.btc.change_1m_pct.toFixed(3) + "% last minute";
  ch.className = "card-sub " + (d.btc.change_1m_pct >= 0 ? "pos" : "neg");

  const mk = d.market, s = d.signal, call = d.call || {};
  if (d.market_error) showError("dash-error", d.market_error); else hideError("dash-error");

  // call card — the product. One call per window, latched once sealed.
  if (s) {
    const emoji = s.cls === "up" ? "🟢" : s.cls === "down" ? "🔴" : "🟡";
    $("dash-signal").className = bannerCls(s.cls);
    $("dash-signal-title").textContent = emoji + " " + s.label + (call.strong ? " ★" : "");
    $("dash-signal-sub").textContent = s.reason;
    renderCallMeta($("dash-call-meta"), call);
    renderCallSell($("dash-call-sell"), call);
    $("dash-prob-fill").style.width = Math.round(Math.min(1, Math.max(0, s.blended_up || 0)) * 100) + "%";
    $("c-blended").textContent = s.blended_up != null ? pct1(s.blended_up) : "—";
  }

  // market essentials
  if (mk) {
    $("market-title").textContent = mk.title || "";
    $("market-ticker").textContent = "Ticker: " + mk.ticker + " · Status: " + mk.status;
    $("market-close").textContent = mk.close_utc ? "Closes: " + mk.close_utc : "";
    $("market-status").textContent = "Found via " + (mk.discovery_source || "");
    S.closeEpochMs = mk.close_epoch_ms;
    $("c-target").textContent = mk.target != null ? money(mk.target) : "—";
    $("c-candle").textContent = d.btc.candle_time_utc;
    $("q-yes-bid").textContent = cents(mk.up_bid);
    $("q-yes-ask").textContent = cents(mk.up_ask);
    $("q-no-bid").textContent = cents(mk.down_bid);
    $("q-no-ask").textContent = cents(mk.down_ask);
  }

  // v26.4: keep the latest 1m momentum flip levels for the chart overlay.
  S.flipLevels = d.momentum || null;
  if (chartData.length) drawChart();
}

/* Call-card meta + sell lines. */
function renderCallMeta(el, call) {
  if (!call || call.status === "none") {
    el.textContent = call && call.confirm > 0
      ? "Gates clear — confirming (" + call.confirm + "/" + call.confirm_needed + ")…"
      : "";
    return;
  }
  const band = (call.band_lo != null && call.band_hi != null)
    ? " · entry band " + Math.round(call.band_lo) + "–" + Math.round(call.band_hi) + "¢"
      + (call.band_status === "out-of-band" ? " (out of band — do not chase)" : "") : "";
  el.textContent = "Sealed " + (call.sealed_at || "—") + " at " + cents(call.seal_ask) + " ask"
    + " · blended P " + (call.seal_blended != null ? pct1(call.seal_blended) : "—") + band;
}

function renderCallSell(el, call) {
  if (!call || call.status !== "sold") { el.textContent = ""; return; }
  el.textContent = "🔴 SELL — " + (call.sell_reason || "sell trigger fired");
}

/* ---------- momentum flip state (chart overlay) ---------- */
function momentumStateAfterFlip(m, comp) {
  if (!m || m.r3 == null || m.r5 == null) return null;
  let s3 = m.r3 >= 0 ? 1 : -1, s5 = m.r5 >= 0 ? 1 : -1;
  if (comp === "r3") s3 = -s3; else if (comp === "r5") s5 = -s5; else return null;
  return (s3 > 0 && s5 > 0) ? "UP" : (s3 < 0 && s5 < 0) ? "DOWN" : "MIXED";
}

/* ---------- countdown (client-side, ticks every second) ---------- */
function tickCountdown() {
  const ps = $("poll-status");
  if (S.lastStateAt && !S.paused && ps.textContent.startsWith("updated")) {
    const s = Math.floor((Date.now() - S.lastStateAt) / 1000);
    ps.textContent = "updated " + s + "s ago";
  }
  if (S.closeEpochMs == null) { $("time-left").textContent = "—"; return; }
  const ms = S.closeEpochMs - Date.now();
  if (ms <= 0) { $("time-left").textContent = "closing…"; return; }
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000);
  $("time-left").textContent = m + "m " + String(s).padStart(2, "0") + "s";
}

/* ---------- chart ---------- */
let chartData = [];
function sizeChart() {
  const c = $("chart"), wrap = c.parentElement;
  const r = wrap.getBoundingClientRect();
  c.width = Math.max(300, r.width - 16) * devicePixelRatio;
  c.height = 480 * devicePixelRatio;
}
async function loadChart() {
  const h = $("chart-history").value, iv = $("chart-interval").value;
  $("chart-status").textContent = "loading…";
  try {
    const r = await fetch("/api/candles?history=" + h + "&interval=" + iv);
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || "chart failed");
    chartData = d.candles;
    $("chart-caption").textContent = "Showing " + d.candles.length + " candles · Source: " + d.source + " · Times in UTC";
    $("chart-status").textContent = "";
    sizeChart(); drawChart();
  } catch (e) { $("chart-status").textContent = "chart error: " + e.message; }
}
function drawChart() {
  const c = $("chart"); if (!c || !chartData.length) return;
  const ctx = c.getContext("2d"), W = c.width, H = c.height;
  ctx.clearRect(0, 0, W, H);
  const padL = 10 * devicePixelRatio, padR = 70 * devicePixelRatio,
        padT = 14 * devicePixelRatio, padB = 24 * devicePixelRatio;
  const data = chartData;
  let lo = Infinity, hi = -Infinity;
  data.forEach((k) => { lo = Math.min(lo, k.l); hi = Math.max(hi, k.h); });
  const span = (hi - lo) || 1; lo -= span * 0.06; hi += span * 0.06;
  const X = (i) => padL + (i / Math.max(1, data.length - 1)) * (W - padL - padR);
  const Y = (p) => padT + (1 - (p - lo) / (hi - lo)) * (H - padT - padB);
  const up = data[data.length - 1].c >= data[0].o;
  ctx.fillStyle = up ? "rgba(34,197,94,.06)" : "rgba(239,68,68,.06)";
  ctx.fillRect(0, 0, W, H);
  // grid + price axis
  ctx.strokeStyle = "rgba(255,255,255,.08)"; ctx.fillStyle = "rgba(255,255,255,.55)";
  ctx.font = (10 * devicePixelRatio) + "px Outfit, sans-serif"; ctx.lineWidth = 1;
  for (let g = 0; g <= 5; g++) {
    const p = lo + (span * 1.12) * g / 5, y = Y(p);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.fillText("$" + Math.round(p).toLocaleString(), W - padR + 6 * devicePixelRatio, y + 3 * devicePixelRatio);
  }
  // time labels
  const step = Math.ceil(data.length / 6);
  ctx.fillStyle = "rgba(255,255,255,.45)";
  for (let i = 0; i < data.length; i += step) {
    const dt = new Date(data[i].t);
    ctx.fillText(dt.toISOString().slice(11, 16) + "Z", X(i) - 14 * devicePixelRatio, H - 8 * devicePixelRatio);
  }
  // candles
  const bw = Math.max(2 * devicePixelRatio, (W - padL - padR) / data.length * 0.62);
  data.forEach((k, i) => {
    const x = X(i), col = k.c >= k.o ? "#22c55e" : "#ef4444";
    ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = Math.max(1, devicePixelRatio);
    ctx.beginPath(); ctx.moveTo(x, Y(k.h)); ctx.lineTo(x, Y(k.l)); ctx.stroke();
    const yO = Y(k.o), yC = Y(k.c);
    ctx.fillRect(x - bw / 2, Math.min(yO, yC), bw, Math.max(2, Math.abs(yC - yO)));
  });
  // v26.4 flip levels (display-only): dashed lines from 1m momentum.
  // Crossing a line flips that component's sign, so each label shows the
  // resulting momentum state. Levels outside the visible range are skipped;
  // the chart scale itself is never changed for these lines.
  const fl = S.flipLevels;
  if (fl && (fl.r3_flip_price != null || fl.r5_flip_price != null)) {
    const levels = [
      { label: "3m", price: fl.r3_flip_price, comp: "r3", color: "#f59e0b", above: true },
      { label: "5m", price: fl.r5_flip_price, comp: "r5", color: "#a855f7", above: false },
    ];
    for (const L of levels) {
      if (L.price == null || typeof L.price !== "number" || !isFinite(L.price)) continue;
      if (L.price < lo || L.price > hi) continue;
      const y = Y(L.price);
      const after = momentumStateAfterFlip(fl, L.comp);
      const txt = L.label + " flip → " + (after || "?") + " · " + money(L.price) + " (1m)";
      ctx.save();
      ctx.strokeStyle = L.color; ctx.lineWidth = Math.max(1, devicePixelRatio);
      ctx.setLineDash([6 * devicePixelRatio, 4 * devicePixelRatio]);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = (10 * devicePixelRatio) + "px Outfit, sans-serif";
      const tw = ctx.measureText(txt).width;
      const tx = Math.max(padL + 4 * devicePixelRatio, W - padR - tw - 8 * devicePixelRatio);
      // 3m label sits above its line, 5m below, so near-identical levels do not collide.
      const ty = L.above
        ? Math.max(padT + 11 * devicePixelRatio, y - 5 * devicePixelRatio)
        : Math.min(H - padB - 3 * devicePixelRatio, y + 14 * devicePixelRatio);
      ctx.fillStyle = "rgba(0,0,0,.55)";
      ctx.fillRect(tx - 4 * devicePixelRatio, ty - 10 * devicePixelRatio, tw + 8 * devicePixelRatio, 14 * devicePixelRatio);
      ctx.fillStyle = L.color;
      ctx.fillText(txt, tx, ty);
      ctx.restore();
    }
  }
}
window.addEventListener("resize", () => { sizeChart(); drawChart(); });

/* ---------- wire up controls ---------- */
$("refresh-select").addEventListener("change", (e) => { S.refreshSec = parseInt(e.target.value, 10); restartPoll(); });
$("pause-btn").addEventListener("click", () => {
  S.paused = !S.paused;
  $("pause-btn").textContent = S.paused ? "▶ Resume" : "⏸ Pause";
  $("poll-status").textContent = S.paused ? "paused" : "";
  if (!S.paused) pollState();
});
$("chart-history").addEventListener("change", loadChart);
$("chart-interval").addEventListener("change", loadChart);

function restartPoll() {
  if (S.timer) clearInterval(S.timer);
  if (S.quickTimer) clearInterval(S.quickTimer);
  S.timer = setInterval(pollState, S.refreshSec * 1000);
  S.quickTimer = setInterval(pollQuick, 2000); // 2s: matches the server's SPOT_TTL, 1s polling just re-fetched cached data
}

/* ---------- boot ---------- */
restartPoll();
S.countdownTimer = setInterval(tickCountdown, 1000);
pollState();
loadChart();
