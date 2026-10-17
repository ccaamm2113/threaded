/* BTC 15-Minute Kalshi Analyzer — frontend */
const S = {
  history: 750, refreshSec: 3, paused: false,
  closeEpochMs: null, timer: null, quickTimer: null, countdownTimer: null,
  lastStateAt: null,
};

const $ = (id) => document.getElementById(id);
const money = (v) => v == null ? "—" : "$" + Number(v).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const cents = (v) => v == null ? "—" : Math.round(v) + "¢";
const pct1 = (v) => v == null ? "—" : (v * 100).toFixed(1) + "%";
const pctSigned = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(3) + "%";
const edgeTxt = (v) => v == null ? "Ask unavailable" : (v >= 0 ? "+" : "") + v.toFixed(1) + "¢";
const girlFor = (cls) => "/static/img/" + (cls === "up" ? "yuki_bullish.webp" : cls === "down" ? "yuki_bearish.webp" : "yuki_chill.webp");
const bannerCls = (cls) => cls === "up" ? "signal-up" : cls === "down" ? "signal-down" : "signal-wait";

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tabpage").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "chart") { sizeChart(); drawChart(); }
  });
});

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
    renderDashboard(d);
    renderKalshi(d);
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

/* ---------- dashboard ---------- */
function renderDashboard(d) {
  $("btc-source-label").textContent = "BTC spot (" + d.btc.source + ")";
  $("btc-price").textContent = money(d.btc.price);
  const ch = $("btc-change");
  ch.textContent = (d.btc.change_1m_pct >= 0 ? "+" : "") + d.btc.change_1m_pct.toFixed(3) + "% last minute";
  ch.className = "card-sub " + (d.btc.change_1m_pct >= 0 ? "pos" : "neg");
  const s = d.signal, call = d.call || {};
  $("c-blended").textContent = s && s.blended_up != null ? pct1(s.blended_up) : "—";
  $("c-target-p").textContent = s && s.target_p != null ? pct1(s.target_p) : "—";
  $("c-param-p").textContent = s && s.param_p != null ? pct1(s.param_p) : "—";
  $("c-net-edge").textContent = edgeTxt(call.net_edge);
  $("c-time-left").textContent = d.market ? d.market.mins_display : "—";
  $("c-target").textContent = d.market && d.market.target != null ? money(d.market.target) : "—";
  $("c-candle").textContent = d.btc.candle_time_utc;

  if (s) {
    const emoji = s.cls === "up" ? "🟢" : s.cls === "down" ? "🔴" : "🟡";
    $("dash-signal").className = bannerCls(s.cls);
    $("dash-signal-title").textContent = emoji + " " + s.label + (call.strong ? " ★" : "");
    $("dash-signal-sub").textContent = s.reason;
    renderCallMeta($("dash-call-meta"), call);
    renderCallSell($("dash-call-sell"), call);
    $("dash-prob-fill").style.width = Math.round(Math.min(1, Math.max(0, s.blended_up || 0)) * 100) + "%";
    $("dash-girl").src = girlFor(s.cls);
  }
  const says = {
    up: "Yuki senses <b class='pos'>a real setup</b>! The call is latched — no flip-flopping! 💚",
    down: "Yuki senses <b class='neg'>a real setup</b>! The call is latched — no flip-flopping! ❤️",
    wait: "Yuki says: <b>no clear edge</b> right now. SIT OUT is a call too! 💛",
  };
  $("dash-yuki-says").innerHTML = says[s ? s.cls : "wait"];
  renderFlipWatch(d);
  renderScalePlan(d);
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
  const row = (t) => "<tr><td><b>" + t.id + "</b></td><td>" + t.pct + "%</td><td>" + cents(t.price)
    + "</td><td>" + t.fee.toFixed(2) + "¢</td><td class='fine'>" + t.note + "</td></tr>";
  $("scale-tranches").innerHTML = plan.tranches.map(row).join("");
  $("scale-exits").innerHTML = plan.exits.map(row).join("");
  const sgn = (v) => (v >= 0 ? "+" : "") + v.toFixed(2) + "¢";
  $("scale-fees").innerHTML = "Fees per contract: plan <b>" + plan.plan_fee_total.toFixed(2) + "¢</b>"
    + " (entry " + plan.entry_fee.toFixed(2) + "¢ + exit " + plan.exit_fee.toFixed(2) + "¢)"
    + " vs single-fill <b>" + plan.single_fee_total.toFixed(2) + "¢</b>"
    + " · net edge after plan fees <b>" + sgn(plan.net_edge_plan) + "</b>"
    + " (single-fill " + sgn(plan.net_edge_single) + ") · × your contracts";
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
  const s3 = m.r3 >= 0 ? 1 : -1, s5 = m.r5 >= 0 ? 1 : -1;
  let ns3 = s3, ns5 = s5;
  if (nearest.comp === "r3") ns3 = -s3; else ns5 = -s5;
  const newState = (ns3 > 0 && ns5 > 0) ? "UP" : (ns3 < 0 && ns5 < 0) ? "DOWN" : "MIXED";
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

/* Call-card meta + sell lines, shared by the dashboard and Kalshi tabs. */
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

/* ---------- kalshi tab ---------- */
function renderKalshi(d) {
  paperTick(d);
  renderPaper();
  const mk = d.market;
  if (d.market_error && !mk) {
    showError("kalshi-error", d.market_error);
    $("market-body").classList.add("hidden");
    $("market-status").textContent = "";
    return;
  }
  hideError("kalshi-error");
  if (d.market_error) showError("kalshi-error", d.market_error); else hideError("kalshi-error");

  $("market-status").textContent = mk ? ("Found via " + mk.discovery_source) : "";
  $("market-body").classList.remove("hidden");

  $("market-title").textContent = mk.title;
  $("market-ticker").textContent = "Ticker: " + mk.ticker + " · Status: " + mk.status;
  S.closeEpochMs = mk.close_epoch_ms;

  $("q-yes-bid").textContent = cents(mk.up_bid);
  $("q-yes-ask").textContent = cents(mk.up_ask);
  $("q-no-bid").textContent = cents(mk.down_bid);
  $("q-no-ask").textContent = cents(mk.down_ask);
  $("q-last").textContent = cents(mk.last);

  $("target-price").textContent = money(mk.target);
  $("target-source").textContent = mk.target_source || "";
  const sv = $("spot-vs");
  sv.textContent = mk.spot_vs_target == null ? "—" : money(mk.spot_vs_target);
  sv.className = "card-value " + (mk.spot_vs_target == null ? "" : mk.spot_vs_target >= 0 ? "pos" : "neg");
  $("spot-vs-pct").textContent = mk.spot_vs_target_pct == null ? "" : (mk.spot_vs_target_pct >= 0 ? "+" : "") + mk.spot_vs_target_pct.toFixed(3) + "% from target";

  const ab = $("above-below"), abt = $("above-below-text");
  if (mk.above == null) { ab.className = "signal-wait slim"; abt.textContent = "Spot vs target unknown."; }
  else if (mk.above) { ab.className = "signal-up slim"; abt.textContent = "🟢 BTC is " + money(Math.abs(mk.spot_vs_target)) + " ABOVE the target."; }
  else { ab.className = "signal-down slim"; abt.textContent = "🔴 BTC is " + money(Math.abs(mk.spot_vs_target)) + " BELOW the target."; }

  // call card
  const s = d.signal;
  if (s) {
    $("kalshi-signal").className = bannerCls(s.cls);
    const emoji = s.cls === "up" ? "🟢" : s.cls === "down" ? "🔴" : "🟡";
    $("kalshi-signal-title").textContent = emoji + " " + s.label + ((d.call && d.call.strong) ? " ★" : "");
    $("kalshi-signal-reason").textContent = s.reason;
    $("kalshi-signal-blend").innerHTML = s.blended_up == null ? "" :
      "Blended P(above target): <b>" + pct1(s.blended_up) + "</b>" +
      " (history " + pct1(s.target_p) + (s.param_p != null ? " · parametric " + pct1(s.param_p) : "") + ")";
    renderCallMeta($("call-meta"), d.call || {});
    renderCallSell($("call-sell"), d.call || {});
    $("kalshi-girl").src = "/static/" + s.girl;
  }

  // conviction breakdown
  renderWhy(d);

  // AI read
  renderAI(d);

  // projected spike
  renderProjection(d);

  // entry gates / sell triggers
  renderEntryExit(d);

  // scalp signals
  renderScalp(d);

  // details
  $("m-r1").textContent = pctSigned(d.momentum.r1);
  $("m-r3").textContent = pctSigned(d.momentum.r3);
  $("m-r5").textContent = pctSigned(d.momentum.r5);
  ["m-r1", "m-r3", "m-r5"].forEach((id, i) => {
    const v = [d.momentum.r1, d.momentum.r3, d.momentum.r5][i];
    $(id).className = "card-value " + (v >= 0 ? "pos" : "neg");
  });
  $("m-up-edge").textContent = edgeTxt(s ? s.up_edge : null);
  $("m-down-edge").textContent = edgeTxt(s ? s.down_edge : null);
  $("m-hist").textContent = s && s.target_p != null ? pct1(s.target_p) : "—";
  $("m-hist-sub").textContent = s && s.sample_count ? ("~" + s.horizon + " min · " + Number(s.sample_count).toLocaleString() + " windows") : "";
  $("signal-thresholds").textContent = s && s.min_edge != null
    ? "Net edge = blended probability × 100 − ask − Kalshi taker fee. Entry needs net edge ≥ " + Math.round(s.min_edge) + "¢ (tighter near expiry, +2¢ when choppy), probability ≥ " + Math.round(s.prob_bar * 100) + "% (higher under 3 min), momentum agreeing, spread ≤ 10¢, ≥ 2 min left, and BTC ≥ 0.05% from target. Kalshi settles on a 60-second CF Benchmarks index average; spot candles can disagree near the target. SIT OUT means no clear setup."
    : "";

  $("c-subtitle").textContent = mk.subtitle || "—";
  $("c-yes").textContent = mk.yes_sub_title || "—";
  $("c-no").textContent = mk.no_sub_title || "—";
  $("c-rules").textContent = mk.rules_primary || "Not returned by this endpoint";
  $("c-close").textContent = mk.close_utc || "Not returned";
}

function renderProjection(d) {
  const p = d.projection, panel = $("projection-panel");
  if (!p) { panel.hidden = true; return; }
  panel.hidden = false;
  $("proj-h").textContent = p.horizon_min + " min";
  $("proj-up").textContent = "+" + p.up_spike_pct.toFixed(2) + "% → ~" + money(p.up_spike_price);
  $("proj-down").textContent = "−" + p.down_spike_pct.toFixed(2) + "% → ~" + money(p.down_spike_price);
  $("proj-req-up").textContent = (p.req_up_pct >= 0 ? "+" : "") + p.req_up_pct.toFixed(2) + "%";
  $("proj-req-down").textContent = (p.req_down_pct >= 0 ? "+" : "") + p.req_down_pct.toFixed(2) + "%";
  const v = $("proj-verdict"), cls = d.signal ? d.signal.cls : "wait";
  if (cls === "up") {
    v.innerHTML = p.covers_up
      ? "✅ A normal up-spike would clear the target — the required move is within reach."
      : "⚠️ The target needs a bigger-than-normal up-spike. Entry is blocked until volatility picks up or price gets closer.";
  } else if (cls === "down") {
    v.innerHTML = p.covers_down
      ? "✅ A normal down-spike would clear the target — the required move is within reach."
      : "⚠️ The target needs a bigger-than-normal down-spike. Entry is blocked until volatility picks up or price gets closer.";
  } else {
    v.innerHTML = "No lean — spikes shown for context. UP needs " + (p.req_up_pct >= 0 ? "+" : "") + p.req_up_pct.toFixed(2) +
      "% vs ≈+" + p.up_spike_pct.toFixed(2) + "% expected · DOWN needs " + (p.req_down_pct >= 0 ? "+" : "") + p.req_down_pct.toFixed(2) +
      "% vs ≈−" + p.down_spike_pct.toFixed(2) + "% expected.";
  }
}

function renderWhy(d) {
  const s = d.signal, panel = $("signal-why");
  const c = s ? s.components : null;
  if (!s || !c) { panel.hidden = true; return; }
  panel.hidden = false;
  $("why-tier").textContent = s.tier === "strong" ? "· STRONG" : s.tier === "call" ? "· CALL" : "· SIT OUT";
  const cv = s.conviction == null ? 0 : s.conviction;
  const fill = $("conv-fill");
  fill.style.width = Math.round(Math.min(1, Math.abs(cv)) * 100) + "%";
  fill.style.background = cv >= 0 ? "var(--up)" : "var(--down)";
  $("conv-val").textContent = (cv >= 0 ? "+" : "") + cv.toFixed(2);
  const dot = (v) => v === "up" ? "pos" : v === "down" ? "neg" : "";
  const rows = [
    ["History vote", pct1(c.target_p) + " up", c.target_p >= 0.5 ? "up" : "down"],
    ["Parametric vote", pct1(c.param_p) + " up (vol model)", c.param_p != null && c.param_p >= 0.5 ? "up" : "down"],
    ["Blended", "85% history / 15% parametric → " + pct1(c.blended_up) + " up", c.blended_up >= 0.5 ? "up" : "down"],
    ["Momentum", c.momentum + " (" + pctSigned(c.r3) + " / " + pctSigned(c.r5) + ")", c.momentum === "mixed" ? "" : c.momentum],
    ["Net edge", edgeTxt(c.edge_cents) + " (after fee)", c.edge_cents == null ? "" : (c.edge_cents >= 0 ? "up" : "down")],
    ["Min net edge", Math.round(c.min_edge) + "¢ to fire", ""],
    ["Regime", c.choppy ? "choppy (" + c.chop_ratio.toFixed(1) + "× norm)" : "normal (" + c.chop_ratio.toFixed(1) + "×)", c.choppy ? "down" : ""],
  ];
  if (c.too_close) rows.push(["Too close", "BTC within 0.05% of the target — no bet", "down"]);
  // microstructure flow (display context only — never a call input)
  const fp = c.flow_parts || {};
  const ffmt = (v) => (v >= 0 ? "+" : "") + Math.round(v * 100) + "%";
  if (fp.book != null) rows.push(["Order book", "bids " + ffmt(fp.book) + " vs asks", fp.book >= 0 ? "up" : "down"]);
  if (fp.flow != null) rows.push(["Trade flow", "taker " + ffmt(fp.flow), fp.flow >= 0 ? "up" : "down"]);
  if (fp.kbook != null) rows.push(["Kalshi book", "YES depth " + ffmt(fp.kbook), fp.kbook >= 0 ? "up" : "down"]);
  if (c.funding_ann != null) {
    const f = c.funding_ann * 100;
    rows.push(["Perp funding", (f >= 0 ? "+" : "") + f.toFixed(1) + "%/yr" + (Math.abs(f) > 25 ? " (crowded)" : ""), Math.abs(f) > 25 ? (f > 0 ? "down" : "up") : ""]);
  }
  if (c.oi_chg_15m != null) rows.push(["Open interest Δ15m", (c.oi_chg_15m >= 0 ? "+" : "") + (c.oi_chg_15m * 100).toFixed(1) + "%", ""]);
  if (c.ai_p_up != null) {
    rows.push(["AI read (display only)", pct1(c.ai_p_up) + " up · conf " + Math.round((c.ai_confidence || 0) * 100) + "%", c.ai_p_up >= 0.5 ? "up" : "down"]);
  }
  if (d.sentiment && d.sentiment.score != null) rows.push(["Sentiment", d.sentiment.label + " (" + d.sentiment.score + ")", ""]);
  $("why-rows").innerHTML = rows.map((r) =>
    '<div class="why-row"><span>' + r[0] + '</span><b class="' + dot(r[2]) + '">' + r[1] + "</b></div>").join("");
}

function renderAI(d) {
  const panel = $("ai-panel");
  const a = d.ai;
  if (!a || a.p_up == null) { panel.hidden = true; return; }
  panel.hidden = false;
  const v = Math.round(a.p_up * 100);
  $("ai-vote").textContent = v + "% UP";
  $("ai-vote").className = a.p_up >= 0.5 ? "pos" : "neg";
  $("ai-fill").style.width = v + "%";
  $("ai-fill").style.background = a.p_up >= 0.5 ? "var(--up)" : "var(--down)";
  $("ai-conf").textContent = "· confidence " + Math.round((a.confidence || 0) * 100) + "%";
  $("ai-model").textContent = "· " + (a.model || "ai");
  $("ai-reasoning").textContent = a.reasoning || "";
  $("ai-bull").innerHTML = (a.bull || []).map((x) => "<li>" + x + "</li>").join("");
  $("ai-bear").innerHTML = (a.bear || []).map((x) => "<li>" + x + "</li>").join("");
}

function renderEntryExit(d) {
  const entry = d.entry, exit = d.exit;
  const ec = $("entry-checks"), xc = $("exit-checks");
  ec.innerHTML = ""; xc.innerHTML = "";
  const call = d.call || {};
  if (!entry || !exit) {
    ec.innerHTML = "<li>No call right now — Yuki is sitting this one out. ☕</li>";
    xc.innerHTML = "<li>Nothing to exit — no call is live.</li>";
    $("entry-banner").className = "signal-wait slim";
    $("entry-banner-text").textContent = "SIT OUT.";
    $("exit-banner").className = "signal-wait slim";
    $("exit-banner-text").textContent = "No exit signal.";
    $("entry-fill").style.width = "0";
    return;
  }
  entry.checks.forEach((c) => {
    const li = document.createElement("li");
    li.innerHTML = (c.ok ? "✅" : "❌") + " <b>" + c.label + "</b><br><span class='detail'>" + c.detail + "</span>";
    ec.appendChild(li);
  });
  const frac = entry.total ? entry.passed / entry.total : 0;
  $("entry-fill").style.width = Math.round(frac * 100) + "%";
  const eb = $("entry-banner"), ebt = $("entry-banner-text");
  if (call.status === "active") {
    eb.className = "signal-up slim";
    ebt.textContent = "📌 CALL SEALED — " + entry.buy_label + " at " + cents(call.seal_ask) + " ask. Latched; entry gates stay frozen until the window rolls.";
  } else if (call.status === "sold") {
    eb.className = "signal-wait slim";
    ebt.textContent = "🔴 CALL SOLD — " + (call.sell_reason || "sell trigger fired") + ". No fresh entry this window.";
  } else if (entry.status === "open") {
    eb.className = "signal-up slim";
    ebt.textContent = "🚀 GATES CLEAR — confirming (" + (call.confirm || 0) + "/" + (call.confirm_needed || 2) + ") for " + entry.buy_label + ".";
  } else if (entry.status === "almost") {
    eb.className = "signal-wait slim";
    ebt.textContent = "👀 ALMOST — " + entry.passed + "/" + entry.total + ". Waiting on: " + entry.missing.join(", ") + ".";
  } else {
    eb.className = "signal-wait slim";
    ebt.textContent = "⏸️ SIT OUT — " + entry.passed + "/" + entry.total + " gates met.";
  }

  exit.triggers.forEach((t) => {
    const li = document.createElement("li");
    li.innerHTML = (t.fired ? "🔔" : "▫️") + " <b>" + t.label + "</b><br><span class='detail'>" + t.detail + "</span>";
    xc.appendChild(li);
  });
  const xb = $("exit-banner"), xbt = $("exit-banner-text");
  if (exit.status === "exit") {
    xb.className = "signal-down slim";
    xbt.innerHTML = "🔔 SELL — " + (call.sell_reason || "a sell trigger fired") + "<br>• " + exit.fired_labels.join("<br>• ");
  } else if (call.status === "active") {
    xb.className = "signal-up slim";
    xbt.textContent = "💎 HOLD — no sell triggers. Let the call work.";
  } else {
    xb.className = "signal-wait slim";
    xbt.textContent = "No call live — nothing to sell.";
  }
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

/* ---------- scalp signals ---------- */
function renderScalp(d) {
  const sc = d.scalp;
  const panel = $("scalp-panel");
  if (!sc) { panel.hidden = true; return; }
  panel.hidden = false;
  const v = $("scalp-verdict");
  if (sc.verdict === "up") { v.textContent = "⚡ BEST: SCALP UP"; v.className = "scalp-verdict pos"; }
  else if (sc.verdict === "down") { v.textContent = "⚡ BEST: SCALP DOWN"; v.className = "scalp-verdict neg"; }
  else if (sc.verdict === "up_lean") { v.textContent = "👀 Leaning UP"; v.className = "scalp-verdict pos"; }
  else if (sc.verdict === "down_lean") { v.textContent = "👀 Leaning DOWN"; v.className = "scalp-verdict neg"; }
  else { v.textContent = "⏸ No scalp — wait"; v.className = "scalp-verdict"; }
  ["up", "down"].forEach((side) => {
    const s = sc[side];
    $("scalp-" + side + "-fill").style.width = s.score + "%";
    $("scalp-" + side + "-score").textContent = s.score;
    const tag = $("scalp-" + side + "-label");
    tag.textContent = s.label === "SCALP" ? "⚡ SCALP" : s.label === "LEAN" ? "👀 LEAN" : "⏸ WAIT";
    tag.className = "scalp-tag " + (s.label === "SCALP" ? (side === "up" ? "pos" : "neg") : "");
    const ul = $("scalp-" + side + "-reasons");
    ul.innerHTML = "";
    (s.reasons || []).forEach((r) => {
      const li = document.createElement("li");
      li.textContent = "• " + r;
      ul.appendChild(li);
    });
    if (!(s.reasons || []).length) {
      const li = document.createElement("li");
      li.textContent = "—";
      ul.appendChild(li);
    }
    const st = s.stats || {};
    const el = $("scalp-" + side + "-stats");
    if (st.p_2m != null) {
      const ev = st.ev_c == null ? "—" : (st.ev_c >= 0 ? "+" : "") + st.ev_c.toFixed(1) + "¢";
      const evCls = st.ev_c == null ? "" : (st.ev_c >= 0 ? "pos" : "neg");
      el.innerHTML = "2m win <b>" + Math.round(st.p_2m * 100) + "%</b> · " +
        "+" + (st.exp_gain_c != null ? st.exp_gain_c.toFixed(1) : "—") + "¢/" +
        "−" + (st.exp_heat_c != null ? st.exp_heat_c.toFixed(1) : "—") + "¢ · " +
        "spread " + (st.spread_c != null ? st.spread_c.toFixed(1) : "—") + "¢ · " +
        "EV <b class='" + evCls + "'>" + ev + "</b> <span class='fine'>(rough)</span>";
    } else {
      el.innerHTML = "";
    }
  });
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
        b.side + "</b> " + b.contracts + " @ " + b.priceC + "¢" + uTxt +
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
      b.side + "</b> " + b.contracts + " @ " + b.priceC + "¢" + extra + "</span>" + res + "</div>";
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
