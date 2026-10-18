/* BTC 15-Minute Kalshi Analyzer — frontend (v26.7: single view — market + chart) */
const S = {
  history: 750, refreshSec: 3, paused: false,
  closeEpochMs: null, timer: null, quickTimer: null, countdownTimer: null,
  lastStateAt: null, flipLevels: null,
};

const $ = (id) => document.getElementById(id);
const money = (v) => v == null ? "—" : "$" + Number(v).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const cents = (v) => v == null ? "—" : Math.round(v) + "¢";
const pct1 = (v) => v == null ? "—" : (v * 100).toFixed(1) + "%";
const pctSigned = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(3) + "%";
const edgeTxt = (v) => v == null ? "Ask unavailable" : (v >= 0 ? "+" : "") + v.toFixed(1) + "¢";
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
    renderStatCards(d);
    renderFlipWatch(d);
    renderFlow(d);
    renderScalePlan(d);
    paperTick(d);
    renderPaper();
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

/* ---------- six stat cards (v26.5 layout, s- IDs: the market panel owns c-*) ---------- */
function renderStatCards(d) {
  const s = d.signal, call = d.call || {};
  $("s-blended").textContent = s && s.blended_up != null ? pct1(s.blended_up) : "—";
  $("s-target-p").textContent = s && s.target_p != null ? pct1(s.target_p) : "—";
  $("s-param-p").textContent = s && s.param_p != null ? pct1(s.param_p) : "—";
  $("s-net-edge").textContent = edgeTxt(call.net_edge);
  $("s-time-left").textContent = d.market ? d.market.mins_display : "—";
  $("s-target").textContent = d.market && d.market.target != null ? money(d.market.target) : "—";
  $("s-candle").textContent = d.btc.candle_time_utc;
}

/* ---------- momentum flip watch (v26.2, display-only) ---------- */
function renderFlipWatch(d) {
  const m = d.momentum, panel = $("flip-panel");
  if (!m || m.r3_flip_price == null || m.r5_flip_price == null) { panel.hidden = true; return; }
  panel.hidden = false;
  const price = d.btc.price;
  const stateLabel = m.state === "up" ? "UP 🟢" : m.state === "down" ? "DOWN 🔴" : "MIXED 🟡";
  $("flip-state").textContent = "Momentum " + stateLabel + " (3m " + pctSigned(m.r3) + " · 5m " + pctSigned(m.r5) + ")";
  $("flip-badge").classList.toggle("hidden", !m.flip_watch);
  const line = (label, flip) => {
    const diff = price - flip;
    const verb = diff > 0 ? "↓ fall" : diff < 0 ? "↑ rise" : "at level";
    return label + " flip " + money(flip) + " · " + verb + " " + money(Math.abs(diff)) + " (" + (Math.abs(diff) / price * 100).toFixed(3) + "%) to cross";
  };
  $("flip-r3").textContent = line("3m", m.r3_flip_price);
  $("flip-r5").textContent = line("5m", m.r5_flip_price);
  // Nearest flip: the closest flip level. Crossing any level changes the
  // momentum state, so the nearest level is the next flip.
  const lvls = [
    { label: "3m", price: m.r3_flip_price, comp: "r3" },
    { label: "5m", price: m.r5_flip_price, comp: "r5" },
  ];
  let nearest = null;
  for (const L of lvls) {
    const dist = Math.abs(price - L.price);
    if (!nearest || dist < nearest.dist) nearest = { ...L, dist };
  }
  const newState = momentumStateAfterFlip(m, nearest.comp);
  const dir = nearest.price > price ? "rises ↑" : "falls ↓";
  $("flip-nearest").textContent = "Nearest flip: " + newState + " if BTC " + dir + " " + money(nearest.dist) + " (" + (nearest.dist / price * 100).toFixed(3) + "%) to " + money(nearest.price) + " (" + nearest.label + ")";
  const lf = m.last_flip;
  if (!lf) {
    $("flip-last").textContent = "Last flip: none yet this session.";
  } else {
    const mins = Math.max(0, Math.round((Date.now() - Date.parse(lf.at)) / 60000));
    const ago = mins < 1 ? "just now" : mins < 60 ? mins + " min ago" : Math.floor(mins / 60) + "h " + (mins % 60) + "m ago";
    $("flip-last").textContent = "Last flip: " + String(lf.from).toUpperCase() + " → " + String(lf.to).toUpperCase() + ", " + ago + ".";
  }
}

/* ---------- order flow panel (standalone; v26.5 showed these as Why rows) ---------- */
function renderFlow(d) {
  const fp = (d.signal && d.signal.components && d.signal.components.flow_parts) || null;
  const panel = $("flow-panel");
  if (!fp || (fp.book == null && fp.flow == null && fp.kbook == null)) { panel.hidden = true; return; }
  panel.hidden = false;
  const ffmt = (v) => (v >= 0 ? "+" : "") + Math.round(v * 100) + "%";
  const dot = (v) => v == null ? "" : (v >= 0 ? "pos" : "neg");
  const rows = [];
  if (fp.book != null) rows.push(["Order book", "bids " + ffmt(fp.book) + " vs asks", dot(fp.book)]);
  if (fp.flow != null) rows.push(["Trade flow", "taker " + ffmt(fp.flow), dot(fp.flow)]);
  if (fp.kbook != null) rows.push(["Kalshi book", "UP depth " + ffmt(fp.kbook), dot(fp.kbook)]);
  $("flow-rows").innerHTML = rows.map((r) =>
    '<div class="why-row"><span>' + r[0] + '</span><b class="' + r[2] + '">' + r[1] + "</b></div>").join("");
}

/* ---------- scale plan (v26.3, display-only) ---------- */
function renderScalePlan(d) {
  const call = d.call || {}, plan = call.scale_plan, panel = $("scale-panel");
  if (!plan) { panel.hidden = true; return; }
  panel.hidden = false;
  $("scale-status").textContent = call.status === "sold"
    ? "Plan closed — call sold: " + (call.sell_reason || "")
    : "Active plan";
  $("scale-side").textContent = "BET " + plan.side_word + " · sealed at " + cents(plan.seal_ask)
    + " ask · P(" + plan.side_word + ") " + pct1(plan.p_side);
  const row = (t) => "<tr><td><b>" + t.id + "</b></td><td>" + t.pct + "%</td><td>" + (t.contracts ?? "—") + "</td><td>" + cents(t.price)
    + "</td><td>" + t.fee.toFixed(2) + "¢</td><td class='fine'>" + t.note + "</td></tr>";
  $("scale-tranches").innerHTML = plan.tranches.map(row).join("");
  $("scale-exits").innerHTML = plan.exits.map(row).join("");
  const sgn = (v) => (v >= 0 ? "+" : "") + v.toFixed(2) + "¢";
  $("scale-fees").innerHTML = "Plan for <b>" + plan.size + " contracts</b> — fees per contract: plan <b>" + plan.plan_fee_total.toFixed(2) + "¢</b>"
    + " (entry " + plan.entry_fee.toFixed(2) + "¢ + exit " + plan.exit_fee.toFixed(2) + "¢)"
    + " vs single-fill <b>" + plan.single_fee_total.toFixed(2) + "¢</b>"
    + " · net edge after plan fees <b>" + sgn(plan.net_edge_plan) + "</b>"
    + " (single-fill " + sgn(plan.net_edge_single) + ") · × your contracts"
    + (plan.note ? "<br><span class='fine'>" + plan.note + "</span>" : "");
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

/* ---------- v24: paper betting (simulated, localStorage) ---------- */
const paper = {
  key: "threaded_paper_v1",
  bets: [],
  side: "YES",
  prevTicker: null,
  lastPrice: null,
  lastQuotes: null,
  lastTarget: null,
  load() {
    try { this.bets = JSON.parse(localStorage.getItem(this.key)) || []; }
    catch (e) { this.bets = []; }
    // open bets can't be settled after a reload — mark expired, no P&L
    let dropped = false;
    this.bets.forEach((b) => {
      if (b.status === "open") { b.status = "expired"; dropped = true; }
    });
    if (dropped) this.save();
  },
  save() {
    try { localStorage.setItem(this.key, JSON.stringify(this.bets.slice(-200))); }
    catch (e) {}
  },
};

function paperMoney(c) {
  if (c == null || !isFinite(c)) return "—";
  return (c >= 0 ? "+$" : "−$") + (Math.abs(c) / 100).toFixed(2);
}

// Display label for a stored side. Sides stay "YES"/"NO" internally (they key
// off quotes.yes_*/no_* and the settlement comparison); only the label is UP/DOWN.
const paperSideLabel = (s) => s === "YES" ? "UP" : "DOWN";

// called on every state poll: track ticker rollover, auto-settle finished windows
function paperTick(d) {
  const ticker = d.market && d.market.ticker;
  const price = d.btc && d.btc.price;
  const target = d.market && d.market.target;
  if (ticker && paper.prevTicker && ticker !== paper.prevTicker) {
    let changed = false;
    paper.bets.forEach((b) => {
      if (b.status === "open" && b.ticker === paper.prevTicker &&
          paper.lastPrice != null && b.target != null) {
        const win = b.side === "YES" ? paper.lastPrice >= b.target : paper.lastPrice < b.target;
        b.status = "settled";
        b.settlePx = Math.round(paper.lastPrice);
        b.win = win;
        b.pnlC = win ? b.contracts * (100 - b.priceC) : -b.contracts * b.priceC;
        changed = true;
      }
    });
    if (changed) paper.save();
  }
  if (ticker) paper.prevTicker = ticker;
  if (price != null) paper.lastPrice = price;
  if (d.quotes) paper.lastQuotes = d.quotes;
  if (target != null) paper.lastTarget = target;
}

function paperSetSide(s) {
  paper.side = s;
  $("paper-yes").classList.toggle("sel-yes", s === "YES");
  $("paper-no").classList.toggle("sel-no", s === "NO");
  const q = paper.lastQuotes;
  if (q) $("paper-price").placeholder = String(s === "YES" ? q.yes_ask : q.no_ask) + "¢ ask";
}

function paperPlace() {
  const q = paper.lastQuotes;
  if (!q || !paper.prevTicker || paper.lastTarget == null) return;
  const contracts = Math.max(1, Math.min(10000, parseInt($("paper-contracts").value, 10) || 0));
  if (!contracts) return;
  const askC = paper.side === "YES" ? q.yes_ask : q.no_ask;
  if (askC == null) return;
  let priceC = Math.round(parseFloat($("paper-price").value));
  if (!(priceC >= 1 && priceC <= 99)) priceC = askC; // empty = fill at ask
  paper.bets.push({
    id: Date.now(), ticker: paper.prevTicker, side: paper.side,
    contracts: contracts, priceC: priceC, target: paper.lastTarget,
    placedAt: Date.now(), status: "open",
  });
  paper.save();
  renderPaper();
}

function paperClose(id) {
  const b = paper.bets.find((x) => x.id === id);
  if (!b || b.status !== "open") return;
  const q = paper.lastQuotes;
  const bidC = b.side === "YES" ? (q && q.yes_bid) : (q && q.no_bid);
  if (bidC == null) return;
  b.status = "closed";
  b.exitC = bidC;
  b.pnlC = b.contracts * (bidC - b.priceC);
  paper.save();
  renderPaper();
}

function renderPaper() {
  const q = paper.lastQuotes;
  $("paper-yes-price").textContent = q && q.yes_ask != null ? q.yes_ask + "¢" : "—";
  $("paper-no-price").textContent = q && q.no_ask != null ? q.no_ask + "¢" : "—";

  const open = paper.bets.filter((b) => b.status === "open");
  const oh = $("paper-open");
  if (!open.length) { oh.innerHTML = ""; }
  else {
    oh.innerHTML = open.map((b) => {
      let uTxt = "";
      if (q) {
        const bidC = b.side === "YES" ? q.yes_bid : q.no_bid;
        if (bidC != null) {
          const u = b.contracts * (bidC - b.priceC);
          uTxt = ' · <b class="' + (u >= 0 ? "pos" : "neg") + '">' + paperMoney(u) + "</b>";
        }
      }
      return '<div class="paper-bet"><span><b class="' + (b.side === "YES" ? "pos" : "neg") + '">' +
        paperSideLabel(b.side) + "</b> " + b.contracts + " @ " + b.priceC + "¢" + uTxt +
        '</span><button class="x" data-close="' + b.id + '" title="exit at bid">✕</button></div>';
    }).join("");
    oh.querySelectorAll("[data-close]").forEach((el) => {
      el.onclick = () => paperClose(parseInt(el.getAttribute("data-close"), 10));
    });
  }

  const done = paper.bets.filter((b) => b.status !== "open").slice(-8).reverse();
  $("paper-history").innerHTML = done.map((b) => {
    let res;
    if (b.status === "expired") res = '<span class="fine">expired</span>';
    else {
      const w = b.status === "settled" ? b.win : b.pnlC > 0;
      res = '<b class="' + (w ? "pos" : "neg") + '">' +
        (b.status === "settled" ? (b.win ? "WIN" : "LOSS") : "EXIT") + " " + paperMoney(b.pnlC) + "</b>";
    }
    const extra = b.status === "settled" ? ' <span class="fine">settle $' + b.settlePx + "</span>" : "";
    return '<div class="paper-bet"><span><b class="' + (b.side === "YES" ? "pos" : "neg") + '">' +
      paperSideLabel(b.side) + "</b> " + b.contracts + " @ " + b.priceC + "¢" + extra + "</span>" + res + "</div>";
  }).join("");

  const scored = paper.bets.filter((b) => (b.status === "settled" || b.status === "closed") && b.pnlC != null);
  if (!scored.length) {
    $("paper-totals").innerHTML = '<span class="fine">No settled bets yet — paper trade the signals and watch this space.</span>';
  } else {
    const tot = scored.reduce((a, b) => a + b.pnlC, 0);
    const wins = scored.filter((b) => b.pnlC > 0).length;
    $("paper-totals").innerHTML = 'Total <b class="' + (tot >= 0 ? "pos" : "neg") + '">' + paperMoney(tot) +
      "</b> · " + wins + "W-" + (scored.length - wins) + "L · " + Math.round(wins / scored.length * 100) + "%";
  }
}

function paperInit() {
  paper.load();
  paperSetSide("YES");
  $("paper-yes").onclick = () => paperSetSide("YES");
  $("paper-no").onclick = () => paperSetSide("NO");
  $("paper-place").onclick = paperPlace;
  $("paper-clear").onclick = (e) => {
    e.preventDefault();
    if (confirm("Clear all paper betting history?")) { paper.bets = []; paper.save(); renderPaper(); }
  };
}

/* ---------- boot ---------- */
paperInit();
restartPoll();
S.countdownTimer = setInterval(tickCountdown, 1000);
pollState();
loadChart();
