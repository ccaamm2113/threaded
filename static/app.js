/**
 * BTC 15M Kalshi Platform Frontend Engine
 * Connects directly to the 15-Minute Internet Intelligence & Kalshi Up/Down Feed.
 */

(function() {
    'use strict';

    let lastPrice = null;
    let secondsToClose = 900;
    let countdownInterval = null;

    function formatTime(sec) {
        const m = Math.floor(sec / 60);
        const s = sec % 60;
        return `${m < 10 ? '0' : ''}${m}:${s < 10 ? '0' : ''}${s}`;
    }

    async function fetch15mStats(fresh = false) {
        try {
            const res = await fetch(`/api/btc-15m-stats${fresh ? '?fresh=true' : ''}`);
            if (!res.ok) return;
            const data = await res.json();
            updateUI(data);
        } catch (err) {
            console.error('Failed to sync 15m stats:', err);
        }
    }

    function updateUI(data) {
        const priceEl = document.getElementById('ticker-price');
        const changeEl = document.getElementById('ticker-change');
        const activeRecBadge = document.getElementById('active-rec-badge');
        const upBox = document.getElementById('up-box');
        const downBox = document.getElementById('down-box');
        const upProb = document.getElementById('up-prob');
        const downProb = document.getElementById('down-prob');
        const upMaxCents = document.getElementById('up-max-cents');
        const downMaxCents = document.getElementById('down-max-cents');
        const strikePriceDisplay = document.getElementById('strike-price-display');
        const spotPriceDisplay = document.getElementById('spot-price-display');
        const distStrikeDisplay = document.getElementById('dist-strike-display');
        const candleProgDisplay = document.getElementById('candle-prog-display');
        const stopLossVal = document.getElementById('stop-loss-val');
        const takeProfitVal = document.getElementById('take-profit-val');
        const signalsUl = document.getElementById('signals-ul');
        const indRsi = document.getElementById('ind-rsi');
        const indAtr = document.getElementById('ind-atr');
        const indEma9 = document.getElementById('ind-ema9');
        const indEma21 = document.getElementById('ind-ema21');
        const indFee = document.getElementById('ind-fee');
        const indFng = document.getElementById('ind-fng');

        const kalshi = data.kalshi_betting || {};

        // Price Flash Animation
        if (priceEl && data.price) {
            if (lastPrice !== null) {
                if (data.price > lastPrice) {
                    priceEl.classList.add('flash-green');
                    setTimeout(() => priceEl.classList.remove('flash-green'), 800);
                } else if (data.price < lastPrice) {
                    priceEl.classList.add('flash-red');
                    setTimeout(() => priceEl.classList.remove('flash-red'), 800);
                }
            }
            priceEl.textContent = '$' + Number(data.price).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            lastPrice = data.price;
        }

        if (spotPriceDisplay && data.price) {
            spotPriceDisplay.textContent = '$' + Number(data.price).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }

        // Strike & Delta
        if (strikePriceDisplay && data.open_strike_15m) {
            strikePriceDisplay.textContent = '$' + Number(data.open_strike_15m).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }

        if (changeEl && data.price_change_15m !== undefined) {
            const isPos = data.price_change_15m >= 0;
            changeEl.textContent = `${isPos ? '+' : ''}$${data.price_change_15m}`;
            changeEl.className = `ticker-val ${isPos ? 'text-green' : 'text-red'}`;
        }

        if (distStrikeDisplay && data.price_change_15m !== undefined) {
            const isPos = data.price_change_15m >= 0;
            distStrikeDisplay.textContent = `${isPos ? '+' : ''}$${data.price_change_15m}`;
            distStrikeDisplay.className = isPos ? 'text-green' : 'text-red';
        }

        if (candleProgDisplay && data.candle_progress_percent !== undefined) {
            candleProgDisplay.textContent = `${data.candle_progress_percent}%`;
        }

        // KALSHI UP/DOWN BETTING BEACONS
        if (kalshi.recommendation) {
            if (activeRecBadge) {
                if (kalshi.recommendation === 'BET_UP') {
                    activeRecBadge.textContent = '▲ BET UP RECOMMENDED';
                    activeRecBadge.className = 'badge badge-bullish';
                } else if (kalshi.recommendation === 'BET_DOWN') {
                    activeRecBadge.textContent = '▼ BET DOWN RECOMMENDED';
                    activeRecBadge.className = 'badge badge-bearish';
                } else {
                    activeRecBadge.textContent = 'PAUSE / CHOP ZONE';
                    activeRecBadge.className = 'badge badge-neutral';
                }
            }

            if (upBox) {
                if (kalshi.recommendation === 'BET_UP') upBox.classList.add('active-up');
                else upBox.classList.remove('active-up');
            }

            if (downBox) {
                if (kalshi.recommendation === 'BET_DOWN') downBox.classList.add('active-down');
                else downBox.classList.remove('active-down');
            }

            if (upProb && kalshi.up_win_probability_pct !== undefined) {
                upProb.textContent = `${kalshi.up_win_probability_pct}%`;
            }
            if (downProb && kalshi.down_win_probability_pct !== undefined) {
                downProb.textContent = `${kalshi.down_win_probability_pct}%`;
            }
            if (upMaxCents && kalshi.recommended_max_contract_cents !== undefined) {
                upMaxCents.textContent = `${kalshi.recommended_max_contract_cents}¢`;
            }
            if (downMaxCents && kalshi.recommended_max_contract_cents !== undefined) {
                downMaxCents.textContent = `${100 - kalshi.recommended_max_contract_cents}¢`;
            }
        }

        // Stops
        if (stopLossVal && data.verdict) {
            stopLossVal.textContent = '$' + Number(data.verdict.suggested_stop_loss).toLocaleString('en-US', { minimumFractionDigits: 2 });
        }
        if (takeProfitVal && data.verdict) {
            takeProfitVal.textContent = '$' + Number(data.verdict.suggested_take_profit).toLocaleString('en-US', { minimumFractionDigits: 2 });
        }

        // Signals List
        if (signalsUl && kalshi.reasons) {
            signalsUl.innerHTML = kalshi.reasons.map(r => `<li>✓ ${r}</li>`).join('');
        }

        // Indicators
        if (indRsi && data.indicators) indRsi.textContent = data.indicators.rsi14;
        if (indAtr && data.indicators) indAtr.textContent = '$' + data.indicators.atr14;
        if (indEma9 && data.indicators) indEma9.textContent = '$' + Number(data.indicators.ema9).toLocaleString();
        if (indEma21 && data.indicators) indEma21.textContent = '$' + Number(data.indicators.ema21).toLocaleString();
        if (indFee && data.macro) indFee.textContent = `${data.macro.mempool_fastest_fee} sat/vB`;
        if (indFng && data.macro) indFng.textContent = `${data.macro.fear_greed_score}/100`;

        if (data.seconds_remaining_in_candle !== undefined) {
            secondsToClose = data.seconds_remaining_in_candle;
        }
    }

    function startCountdown() {
        if (countdownInterval) clearInterval(countdownInterval);
        countdownInterval = setInterval(() => {
            if (secondsToClose > 0) {
                secondsToClose--;
            } else {
                secondsToClose = 900;
                fetch15mStats(true);
            }
            const cdEl = document.getElementById('ticker-countdown');
            if (cdEl) {
                cdEl.textContent = formatTime(secondsToClose);
            }
        }, 1000);
    }

    document.addEventListener('DOMContentLoaded', () => {
        fetch15mStats();
        startCountdown();

        // 10s auto-refresh
        setInterval(() => fetch15mStats(false), 10000);

        const refreshBtn = document.getElementById('manual-refresh-btn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => {
                refreshBtn.textContent = 'Scanning...';
                fetch15mStats(true).finally(() => {
                    setTimeout(() => { refreshBtn.textContent = '⚡ Re-Scan Internet'; }, 1000);
                });
            });
        }
    });
})();
