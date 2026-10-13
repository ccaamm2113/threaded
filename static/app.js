/* BTC 15-Minute Kalshi Analyzer — frontend */
const S = {
  forceTicker: "", ticker: "", targetOverride: "",
  history: 750, refreshSec: 3, paused: false,
  closeEpochMs: null, markets: [], marketsKey: "", timer: null, quickTimer: null, countdownTimer: null,
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
  const p = new URLSearchParams({ history: S.history });
  if (S.forceTicker) p.set("force_ticker", S.forceTicker);
  if (S.ticker) p.set("ticker", S.ticker);
  if (S.targetOverride) p.set("target_override", S.targetOverride);
  return p.toString();
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
  $("model-up").textContent = pct1(d.model.p_up);
  $("model-down").textContent = pct1(d.model.p_down);
  $("candle-time").textContent = d.btc.candle_time_short;
  $("candle-full").textContent = d.btc.candle_time_utc;

  const m = d.model;
  const emoji = m.lean_cls === "up" ? "🟢" : m.lean_cls === "down" ? "🔴" : "🟡";
  $("dash-signal").className = bannerCls(m.lean_cls);
  $("dash-signal-title").textContent = emoji + " Model signal: " + m.lean_label + (m.lean_cls === "wait" ? " ⏸️" : m.lean_cls === "up" ? " 📈" : " 📉");
  $("dash-signal-sub").innerHTML = "Estimated probability BTC is higher in 15 minutes: <b>" + pct1(m.p_up) + "</b>";
  $("dash-prob-fill").style.width = Math.round(Math.min(1, Math.max(0, m.p_up)) * 100) + "%";
  $("dash-girl").src = girlFor(m.lean_cls);
  const says = {
    up: "Yuki senses <b class='pos'>bullish momentum</b>! Probabilities, not promises! 💚",
    down: "Yuki senses <b class='neg'>bearish pressure</b> building... stay sharp! ❤️",
    wait: "Yuki says: <b>no clear edge</b> right now. Patience is a strategy too! 💛",
  };
  $("dash-yuki-says").innerHTML = says[m.lean_cls];

  $("holdout-acc").textContent = m.accuracy == null ? "—" : (m.accuracy * 100).toFixed(1) + "%";
  $("brier").textContent = m.brier == null ? "—" : m.brier.toFixed(3);
  $("n-train").textContent = Number(m.n_train).toLocaleString();
}

/* ---------- kalshi tab ---------- */
function renderKalshi(d) {
  paperTick(d);
  renderPaper();
  // market dropdown: rebuild ONLY when the market list actually changes
  // (was rebuilding ~50 options on every 3s poll = pointless DOM churn)
  S.markets = d.markets || [];
  const sel = $("market-select");
  const prev = S.ticker || (sel.value || "");
  const mkKey = S.markets.map((m) => m.ticker + (m.live ? ":1" : ":0")).join(",");
  if (mkKey !== S.marketsKey) {
    S.marketsKey = mkKey;
    sel.innerHTML = "";
    if (!S.markets.length) {
      const o = document.createElement("option"); o.value = ""; o.textContent = "— no markets discovered —";
      sel.appendChild(o);
    } else {
      S.markets.forEach((m) => {
        const o = document.createElement("option");
        o.value = m.ticker;
        o.textContent = (m.live ? "🟢 " : "⚪ ") + m.title + " | closes " + m.close_label + " | " + m.ticker;
        sel.appendChild(o);
      });
    }
  }
  if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  else if (S.markets.length) sel.value = S.markets[0].ticker;
  S.ticker = sel.value || "";

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

  // direction signal
  const s = d.signal;
  if (s) {
    $("kalshi-signal").className = bannerCls(s.cls);
    const emoji = s.cls === "up" ? "🟢" : s.cls === "down" ? "🔴" : "🟡";
    $("kalshi-signal-title").textContent = emoji + " Direction check: " + s.label;
    $("kalshi-signal-reason").textContent = s.reason;
    $("kalshi-signal-blend").innerHTML = s.blended_up == null ? "" :
      "Blended UP probability: <b>" + pct1(s.blended_up) + "</b>" +
      (s.model_p_up != null ? " (model " + pct1(s.model_p_up) + " · historical " + pct1(s.target_p) + ")" : "");
    $("kalshi-girl").src = "/static/" + s.girl;
  }

  // conviction breakdown
  renderWhy(d);

  // AI read
  renderAI(d);

  // Engine B second opinion
  renderEngineB(d);

  // projected spike
  renderProjection(d);

  // buy / sell
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
    ? "Adaptive edge threshold: " + Math.round(s.min_edge) + "¢ (tighter near expiry) · Probability bar: " + Math.round(s.prob_bar * 100) + "% (higher under 3 min). Experimental signal only — Kalshi settles on a 60-second CF Benchmarks index average; spot candles can disagree near the target. WAIT means no clear setup."
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
  $("why-tier").textContent = s.tier === "strong" ? "· STRONG" : s.tier === "lean" ? "· LEAN" : "· WAIT";
  const cv = s.conviction == null ? 0 : s.conviction;
  const fill = $("conv-fill");
  fill.style.width = Math.round(Math.min(1, Math.abs(cv)) * 100) + "%";
  fill.style.background = cv >= 0 ? "var(--up)" : "var(--down)";
  $("conv-val").textContent = (cv >= 0 ? "+" : "") + cv.toFixed(2);
  const dot = (v) => v === "up" ? "pos" : v === "down" ? "neg" : "";
  const rows = [
    ["Model vote", pct1(c.model_p_up) + " up", c.model_p_up >= 0.5 ? "up" : "down"],
    ["History vote", pct1(c.target_p) + " up", c.target_p >= 0.5 ? "up" : "down"],
    ["Blend", Math.round(c.w_model * 100) + "% model / " + Math.round(c.w_hist * 100) + "% history", ""],
    ["Momentum", c.momentum + " (" + pctSigned(c.r3) + " / " + pctSigned(c.r5) + ")", c.momentum === "mixed" ? "" : c.momentum],
    ["Edge", edgeTxt(c.edge_cents), c.edge_cents == null ? "" : (c.edge_cents >= 0 ? "up" : "down")],
    ["Regime", c.choppy ? "choppy (" + c.chop_ratio.toFixed(1) + "× norm)" : "normal (" + c.chop_ratio.toFixed(1) + "×)", c.choppy ? "down" : ""],
  ];
  if (c.disagree) rows.push(["Note", "model and history disagree — capped at LEAN", ""]);
  // extra prediction sources: microstructure flow
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
    rows.push(["AI vote", pct1(c.ai_p_up) + " up · conf " + Math.round((c.ai_confidence || 0) * 100) + "%", c.ai_p_up >= 0.5 ? "up" : "down"]);
    if (c.ai_disagree) rows.push(["Note", "AI reader disagrees — capped at LEAN", ""]);
  }
  if (c.param_p != null) rows.push(["Parametric", pct1(c.param_p) + " up (vol model)", c.param_p >= 0.5 ? "up" : "down"]);
  if (c.htf_p != null) {
    const tfs = (d.htf && d.htf.tfs) || {};
    const tfTxt = ["5m", "15m", "1H", "4H"].filter((tf) => tfs[tf] != null).map((tf) => {
      const v = tfs[tf];
      return tf + (v > 0.15 ? "▲" : v < -0.15 ? "▼" : "●");
    }).join(" ");
    rows.push(["Higher TFs", pct1(c.htf_p) + " up" + (tfTxt ? " · " + tfTxt : ""), c.htf_p >= 0.5 ? "up" : "down"]);
  }
  if (d.sentiment && d.sentiment.score != null) rows.push(["Sentiment", d.sentiment.label + " (" + d.sentiment.score + ")", ""]);
  if (c.engine_b_p != null) {
    rows.push(["Engine B", pct1(c.engine_b_p) + " up" + (c.engines_agree ? " · agrees" : c.engine_b_disagree ? " · disagrees" : ""), c.engine_b_p >= 0.5 ? "up" : "down"]);
  }
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

function renderEngineB(d) {
  const panel = $("engb-panel");
  const b = d.engine_b;
  const c = d.signal ? d.signal.components : null;
  if (!b || b.p_up == null) { panel.hidden = true; return; }
  panel.hidden = false;
  const v = Math.round(b.p_up * 100);
  $("engb-vote").textContent = v + "% UP";
  $("engb-vote").className = b.p_up >= 0.5 ? "pos" : "neg";
  $("engb-fill").style.width = Math.round(Math.abs(b.p_up - 0.5) * 200) + "%";
  $("engb-fill").style.background = b.p_up >= 0.5 ? "var(--up)" : "var(--down)";
  $("engb-agree").textContent = c && c.engines_agree ? "· agrees with Engine A" : c && c.engine_b_disagree ? "· disagrees with Engine A" : "";
  const dot = (x) => x >= 0 ? "pos" : "neg";
  const names = {macro: "Macro regime", positioning: "Positioning", tape: "Kalshi tape", sentiment: "Sentiment"};
  const rows = Object.keys(b.votes || {}).map((k) => {
    const val = b.votes[k];
    return '<div class="why-row"><span>' + (names[k] || k) + '</span><b class="' + dot(val) + '">' +
      (val >= 0 ? "+" : "") + Math.round(val * 100) + "%</b></div>";
  });
  $("engb-rows").innerHTML = rows.join("");
}

function renderEntryExit(d) {
  const entry = d.entry, exit = d.exit;
  const ec = $("entry-checks"), xc = $("exit-checks");
  ec.innerHTML = ""; xc.innerHTML = "";
  if (!entry || !exit) {
    ec.innerHTML = "<li>No lean right now — Yuki is sitting this one out. No buy signal. ☕</li>";
    xc.innerHTML = "<li>Nothing to exit — no position signal active.</li>";
    $("entry-banner").className = "signal-wait slim";
    $("entry-banner-text").textContent = "No entry signal.";
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
  const frac = entry.passed / entry.total;
  $("entry-fill").style.width = Math.round(frac * 100) + "%";
  const eb = $("entry-banner"), ebt = $("entry-banner-text");
  if (entry.status === "open") {
    eb.className = "signal-up slim";
    ebt.textContent = "🚀 ENTRY WINDOW OPEN — " + entry.passed + "/" + entry.total + " green for buying " + entry.buy_label + ".";
  } else if (entry.status === "almost") {
    eb.className = "signal-wait slim";
    ebt.textContent = "👀 ALMOST — " + entry.passed + "/" + entry.total + ". Waiting on: " + entry.missing.join(", ") + ".";
  } else {
    eb.className = "signal-wait slim";
    ebt.textContent = "⏸️ NO ENTRY — " + entry.passed + "/" + entry.total + " conditions met.";
  }

  exit.triggers.forEach((t) => {
    const li = document.createElement("li");
    li.innerHTML = (t.fired ? "🔔" : "▫️") + " <b>" + t.label + "</b><br><span class='detail'>" + t.detail + "</span>";
    xc.appendChild(li);
  });
  const xb = $("exit-banner"), xbt = $("exit-banner-text");
  if (exit.status === "exit") {
    xb.className = "signal-down slim";
    xbt.innerHTML = "🔔 CONSIDER EXITING<br>• " + exit.fired_labels.join("<br>• ");
  } else {
    xb.className = "signal-up slim";
    xbt.textContent = "💎 HOLD — no exit triggers. Let the edge work.";
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
$("apply-market").addEventListener("click", () => {
  S.forceTicker = $("force-ticker").value.trim();
  S.targetOverride = $("target-override").value.trim();
  pollState();
});
$("market-select").addEventListener("change", (e) => {
  S.ticker = e.target.value; S.forceTicker = ""; $("force-ticker").value = "";
  pollState();
});
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
  S.quickTimer = setInterval(pollQuick, 1000);
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
