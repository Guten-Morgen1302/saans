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
- `saans/summary.py` adds an optional one- or two-sentence summary from a Strands agent on Amazon Bedrock. The summary is checked against the plan and dropped if it adds anything.
- `template.yaml` deploys it on AWS: DynamoDB, two Lambdas (the Telegram webhook, and the 18:00 / 06:30 / 07:00 IST crons), a missed-run heartbeat alarm, and secrets in SSM Parameter Store.

"Saans air band" is our PM2.5 forecast mapped onto CPCB bands. It is **not the official AQI**.

## Run the tests

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Windows
python -m pytest -q
python backtest/run.py        # needs data/raw/pm25_hourly.csv (scripts/backfill.py)
```

## Deploy

Prerequisites: an AWS account, the AWS CLI configured, the AWS SAM CLI, Docker, and Bedrock model access if you want the summary.

1. **Telegram:** create a bot with @BotFather, and create a parents' channel and a staff group (plus demo copies for replays). Add the bot as an admin to each.
2. **Secrets** go in SSM as SecureStrings, never in git:
   ```bash
   aws ssm put-parameter --type SecureString --name /saans/telegram_bot_token --value '<token>'
   aws ssm put-parameter --type SecureString --name /saans/telegram_webhook_secret --value "$(openssl rand -hex 32)"
   aws ssm put-parameter --type SecureString --name /saans/openaq_api_key --value '<key>'
   ```
3. **Deploy.** The first time, run `sam build --use-container && sam deploy --guided --stack-name saans` and enter the Telegram ids and alarm email when asked. After that, `make deploy` builds, deploys and runs the smoke test.
4. **Connect the bot:** `python scripts/set_webhook.py --stack saans`
5. **Try it:** as an admin, send the bot `/replay 2025-11-10`.

## Data

CPCB hourly PM2.5 via the [OpenAQ](https://openaq.org) API, for six Delhi stations (`config/stations.yaml`). `saans/data/replay_2025q4.csv` ships the Oct-Dec 2025 station means so replays work offline.

## Known limits

- One demo school (`school.yaml`). Multi-school onboarding is in `TODOS.md`.
- Severe-day forecasts are right about 60% of the time, with about 50% false alarms. That's why the GRAP warning goes to the principal and staff only.
- The Hindi copy needs a native-speaker check before real use.
