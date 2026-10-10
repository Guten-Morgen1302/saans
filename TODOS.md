# TODOS

## Onboarding

### Multi-school onboarding

**What:** Let any school sign up. The school's location is turned into its set of nearby air stations (OpenAQ radius search), a `/start` invite code links the principal and the parent/staff channels, and data is pulled every night for each school.

**Why:** v1 serves only the hardcoded demo school (eng review D9). Every other school is locked out until this lands.

**Context:** v1 has a single school, and the code marks that assumption with `gstack-shortcut(dec-d62fa552)` comments. Remove those first: the `schools` item in the single DynamoDB table already stores school_id, so start with `/start <code>` in the webhook and a station lookup in `saans/ingest.py`. Upgrade when a second real school wants it or after the 2026-10-11 submission.

**Effort:** L
**Priority:** P2
**Depends on:** v1 shipped

### Timetable web form

**What:** A web form where a principal enters the weekly timetable, school-wide events and class-level asthma counts.

**Why:** v1 keeps the demo school's timetable in `school.yaml` (CEO review D4). Real schools need to enter it without editing files.

**Context:** Deferred because v1 serves one school (eng D9), so the form would edit one file's worth of data and add an authenticated write path. Build it together with multi-school onboarding. Its fields match the `school.yaml` schema.

**Effort:** M
**Priority:** P2
**Depends on:** Multi-school onboarding

## Agent

### Strands Q&A agent

**What:** The principal asks "Can we hold the match tomorrow at 11?" and the agent answers only through the `get_forecast(school, tomorrow)`, `get_protocol` and `explain_rule` tools.

**Why:** It shows proper Strands tool use and answers the questions principals actually ask.

**Context:** Deferred in the CEO review (D3) to keep the least-tested LLM path out of the demo. The design doc's Q&A spec still applies: tomorrow-only horizon, and "I only forecast tomorrow" for later dates. Reuse the summary validator for its answers.

**Effort:** M
**Priority:** P3
**Depends on:** v1 shipped

## Forecast

### Challenger forecasts (corrected Open-Meteo, IITM EWS)

**What:** Backtest the corrected Open-Meteo model and IITM Pune's Early Warning System forecasts against the baseline, using the R6 selection rule.

**Why:** Beating the official forecast would be a strong claim. v1 only compares the baseline with pure persistence.

**Context:** Deferred in the CEO review (D5). The blocker is whether an honest archive of forecasts as issued exists. Replaying today's model output leaks hindsight. Check for such an archive first. Prior learning: Open-Meteo reads Delhi PM2.5 about 2x too low on smog days.

**Effort:** M
**Priority:** P3
**Depends on:** An archive of issued forecasts existing

## Messaging (principal controls)

### Edit toggles on the nightly plan

**What:** Per-action on/off toggles (a swap toggles both periods) before Approve, with EN/HI re-rendering and summary re-validation.

**Why:** A principal who disagrees with one line currently has to Skip the whole plan.

**Context:** Deferred in the CEO review (D6). The R1 lifecycle is SENT→APPROVED/SKIPPED/EXPIRED. Add EDITING as a sub-state of SENT, and keep expiry conditional on it.

**Effort:** M
**Priority:** P2
**Depends on:** v1 shipped

### /mute and /unmute for no-action nights

**What:** Let the principal silence the daily "no rule applies" line.

**Why:** A daily line becomes noise over months of real use.

**Context:** Deferred in the CEO review (D7). During the demo week, the daily line doubles as evidence that the nightly run is alive. Use the eng-review R3 principal-only check and a `mute_no_action` field on the school item.

**Effort:** S
**Priority:** P3
**Depends on:** v1 shipped

## Rescheduler

### Rescheduler: teacher-clash and fixed-room constraints

**What:** Reject swaps that double-book a PE teacher or move a class into a room that's already taken.

**Why:** v1 ignores teachers and rooms. A real principal won't approve a plan that puts one PE teacher in two places at 13:10.

**Context:** `saans/reschedule.py` swaps periods within the same class, choosing by cleanest forecast band. Add `teacher_id` and `room` to the timetable grid, then filter candidate swaps before choosing the cleanest. The README currently admits the gap.

**Effort:** M
**Priority:** P2
**Depends on:** The timetable form storing teacher and room

## Messaging

### WhatsApp Business delivery channel

**What:** Move principal and parent delivery from Telegram to the WhatsApp Business Cloud API, using approved message templates.

**Why:** Indian principals and parents live on WhatsApp, and the pitch names it as the production channel.

**Context:** Telegram was chosen for the hackathon because Indian SMS needs DLT sender registration and WhatsApp needs pre-approved templates. Expect days of lead time for Meta business verification and template approval, and buttons only work inside approved templates. Start from the EN/HI templates in `saans/messages.py`.

**Effort:** M
**Priority:** P3
**Depends on:** v1 shipped; a Meta business account

## Completed
