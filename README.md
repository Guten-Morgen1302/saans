# Saans

Evening-before air plans for Delhi schools. At 18:00 IST on the evening before each school day, Saans forecasts tomorrow's PM2.5 from CPCB station data. It applies fixed, sourced rules (move PE indoors, cancel outdoor activity on Severe days, keep windows shut) and sends the principal a Telegram plan with **Approve & send** / **Skip**. On approval, parents and staff get one bilingual (Hindi + English) post each.

On the evening of 10 Nov 2025, using only data published by 16:00, Saans would have forecast a Severe school day for 11 Nov. GRAP Stage III and the Delhi DoE hybrid-classes order came the next morning.

## How it works

```
CPCB stations (OpenAQ) ─▶ forecast (persistence, as of 18:00) ─▶ rules.yaml ─▶ Telegram plan
                                                                               │ Approve & send
                                                           parents' channel ◀──┴──▶ staff group
```

- `saans/forecast.py` makes the forecast. It uses only the data available at 18:00. Pure persistence was selected by the backtest (`backtest/run.py`).
- `rules.yaml` holds the rules. Each rule cites EPA, CPCB or CAQM sources, and adaptations are labelled.
- `saans/messages.py` and `saans/templates.py` write the messages, in English and Hindi. Parents never see class names or GRAP guesses.
- `saans/summary.py` adds an optional one- or two-sentence summary from a Strands agent (Gemini locally, Amazon Bedrock on AWS). The summary is checked against the plan and dropped if it adds anything.
- `scripts/run_local.py` runs everything on a laptop. `template.yaml` deploys the same code on AWS: DynamoDB, two Lambdas (the Telegram webhook, and the 18:00 / 06:30 / 07:00 IST crons), a missed-run heartbeat alarm, and secrets in SSM Parameter Store.

"Saans air band" is our PM2.5 forecast mapped onto CPCB bands. It is **not the official AQI**.

## Run the tests

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Windows
python -m pytest -q
python backtest/run.py        # needs data/raw/pm25_hourly.csv (scripts/backfill.py)
```

## Run it locally (no AWS account, no card)

This is how the demo runs. It uses the Strands Agents SDK (an AWS open-source tool) with a free Gemini key for the summary, Amazon's DynamoDB Local in Docker, and Telegram long polling, so no public URL is needed.

1. **Telegram:** create a bot with @BotFather. Create a parents' channel and a staff group, plus demo copies of both, and add the bot as an admin to each.
2. **Ids:** get the numeric ids for yourself (principal/admin) and the four chats, e.g. by forwarding a message from each to @userinfobot.
3. `cp .env.example .env` and fill it in. `GEMINI_API_KEY` is optional; it's free at aistudio.google.com.
4. Start Docker Desktop, then run `docker compose up -d` to start DynamoDB Local.
5. `python scripts/run_local.py`. Leave it running: the 18:00 plan, the 06:30 reminder and the 07:00 expiry run only while it's on.
6. Try it: as the admin, send the bot `/replay 2025-11-10`.

## Deploy on AWS (optional)

`template.yaml` deploys the same code as two Lambdas (a Telegram webhook, plus the 18:00 / 06:30 / 07:00 IST crons), DynamoDB, a missed-run alarm and secrets in SSM. It needs an AWS account (and a card):

```bash
aws ssm put-parameter --type SecureString --name /saans/telegram_bot_token --value '<token>'
aws ssm put-parameter --type SecureString --name /saans/telegram_webhook_secret --value "$(openssl rand -hex 32)"
aws ssm put-parameter --type SecureString --name /saans/openaq_api_key --value '<key>'
sam build --use-container && sam deploy --guided --stack-name saans   # first time
make deploy                                                           # later: build, deploy, smoke test
python scripts/set_webhook.py --stack saans
```

## Data

CPCB hourly PM2.5 via the [OpenAQ](https://openaq.org) API, for six Delhi stations (`config/stations.yaml`). `saans/data/replay_2025q4.csv` ships the Oct-Dec 2025 station means so replays work offline.

## Known limits

- One demo school (`school.yaml`). Multi-school onboarding is in `TODOS.md`.
- Severe-day forecasts are right about 60% of the time, with about 50% false alarms. That's why the GRAP warning goes to the principal and staff only.
- The Hindi copy needs a native-speaker check before real use.
