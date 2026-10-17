# Threaded AI Reader — setup

v19 adds an **AI market reader**: an LLM that reads the full quant snapshot
(model, history, momentum, order-book imbalance, trade flow, funding, Kalshi
quotes) and returns a directional vote + short written reasoning. It counts as
one modest vote inside the conviction score — it can nudge conviction and cap
the tier at LEAN when it strongly disagrees, but it can never single-handedly
flip the quant direction.

## It works with zero config

If no key is set, the engine is 100% quant and behaves exactly as before.
The dashboard simply hides the AI panel.

## Enabling it (2 minutes)

The reader uses any **OpenAI-compatible chat-completions API**. Set these as
environment variables on Render (Dashboard → your service → Environment):

| Variable      | Required | Default                       | Notes                                      |
|---------------|----------|-------------------------------|--------------------------------------------|
| `AI_API_KEY`  | yes      | —                             | Your provider API key. Enables the reader. |
| `AI_MODEL`    | no       | `gpt-4o-mini`                 | Any chat model your provider offers.       |
| `AI_BASE_URL` | no       | `https://api.openai.com/v1`   | e.g. `https://api.x.ai/v1` for xAI, `https://api.deepseek.com/v1` for DeepSeek, `https://openrouter.ai/api/v1` for OpenRouter. |

Redeploy after saving (Render restarts automatically on env change).

## Cost & latency guardrails

- The reader refreshes **at most once per 90 seconds per contract** — about
  10 calls per 15-minute window. With a mini-class model that's a few cents
  a day.
- It **never blocks the dashboard**: reads happen in a background thread and
  `/api/state` always serves the latest cached read (or nothing on the very
  first poll).
- Any API error, timeout, or malformed reply fails silently back to quant-only.

## Honest expectations

An LLM has no proven edge on 15-minute BTC direction — treat the AI panel as
a second opinion with written reasoning, not a signal. That's why its weight
is small and its disagreement only *caps* conviction instead of reversing it.
Paper-trade with it on vs. off before trusting it.
