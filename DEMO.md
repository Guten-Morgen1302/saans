# Demo video script (2-3 minutes) and submission text

## Before recording
- Docker Desktop is running, and `python scripts/run_local.py` is running (check that its log shows "Saans running locally").
- Telegram open on your phone with @SaansSchoolBot, Saans Parents DEMO and Saans Staff DEMO.
- The proof page open in a browser: https://guten-morgen1302.github.io/saans/ (or `docs/index.html`).

## Shot list

| Time | Show | Say (roughly) |
|---|---|---|
| 0:00-0:15 | Proof page headline and chart | "On 11 Nov 2025 Delhi's air hit Severe. The order to move young classes to hybrid came after school had started. Saans told the principal at 18:00 the evening before." |
| 0:15-0:35 | Chart: Saans alert line vs AQI 425 line | "This forecast used only data published by 16:00 on 10 Nov. At least 15 hours of warning." |
| 0:35-1:20 | Phone: send `/replay 2025-11-10`; the plan arrives; open the Hindi and parent previews | "The principal gets one decision: which outdoor periods are cancelled or moved indoors, windows shut, and a heads-up that GRAP III is likely. Every line comes from a rule with a source." |
| 1:20-1:45 | Tap **Approve & send**; the stamp appears; switch to Parents DEMO and Staff DEMO | "One tap. Parents get a Hindi-first notice with no class names and no guesses. Staff get a line per class." |
| 1:45-2:15 | Proof page: scoreboard + "Where it is weak" | "Backtested on 44 school days: 86% of Very Poor days caught, 11% false alarms. Severe days are harder, so the GRAP guess never goes to parents. We dropped a feature that failed its own test." |
| 2:15-2:40 | Admin heartbeat line / README | "It runs every evening. When the official CPCB feed stalled this week, it switched to corrected backup sensors and said so. Built with the Strands Agents SDK. The AI only writes a one-line summary, checked against the plan." |

## Submission text (paste and edit)

**Saans: evening-before air plans for Delhi schools (Air track)**

On "Poor" and "Very Poor" days (AQI 201-400), no school has to change anything, and official orders only arrive at AQI 401+, often after school has started. Saans sends a principal a concrete plan at 18:00 the evening before: which outdoor PE, assembly and recess to move indoors or cancel, when to keep windows shut, and whether GRAP Stage III is likely. One tap sends a Hindi-first notice to parents and a class-by-class list to staff.

- **Honest forecast.** Every forecast uses only data published by 16:00. Backtested on 44 school days (Oct-Dec 2025): 86% of Very Poor-or-worse days caught, 11% false alarms. On 10 Nov 2025 it warned at least 15 hours before the official GRAP III / hybrid-class order.
- **Rules decide, AI writes.** Six rules cite EPA, CPCB and CAQM sources. A Strands Agents SDK agent writes an optional one-line summary, and a validator rejects any number, time, class or band not in the plan.
- **Try it yourself.** Send `/try` to @SaansSchoolBot to get the real 10 Nov 2025 plan in your own chat. "Would your school use this?" feedback is collected from the teachers who tried it.
- **Built to be trusted.** Parents never see class names (asthma counts stay private) or uncertain GRAP guesses. The notice is bilingual and every outcome is stamped on the plan. A backup data path activates when the CPCB feed stalls.
- **Stack.** Python, Strands Agents SDK (AWS open source), DynamoDB Local, Telegram, OpenAQ/CPCB data, Gemini. It's also deployable on AWS Lambda + EventBridge + DynamoDB via the included SAM template. 210 tests.

Repo: https://github.com/Guten-Morgen1302/saans · Proof page: https://guten-morgen1302.github.io/saans/
