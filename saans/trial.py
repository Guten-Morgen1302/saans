"""Public demo (/try) and feedback, so real teachers and judges can try Saans.

Anyone can send /try in a private chat with the bot. They get the real replay
plan for Tue 11 Nov 2025 (made from data available at 18:00 on 10 Nov) with
demo buttons. "Approve & send" shows them, in their own chat, what parents and
staff would receive. Nothing is ever posted to the school's channels. Then the
bot asks "Would your school use this?" (Yes / Maybe / No) and takes an optional
one-line comment. /feedback (admins) shows the tally and recent comments.

Rate limit: 3 /try per person per IST day (conditional counter in DynamoDB).
Stored per feedback: Telegram user id (to keep one answer per person), answer,
comment, time. No names.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from functools import cached_property

from botocore.exceptions import ClientError

from saans import messages, pipeline
from saans.forecast import forecast
from saans.ingest import IST
from saans.lifecycle import Deps
from saans.rules import protocol
from saans.summary import summarize

log = logging.getLogger(__name__)

REPLAY_DAY = date(2025, 11, 10)
TRIES_PER_DAY = 3
ANSWERS = {"yes": "Yes", "maybe": "Maybe", "no": "No"}

INTRO = (
    "<b>Saans demo</b> · You are the principal of a Delhi school. This is the plan Saans "
    "sent at 18:00 on Mon 10 Nov 2025, using only data available then. The next morning "
    "Delhi's air turned Severe and the government moved young classes to hybrid.\n"
    "Tap <b>Approve & send</b> to see what parents and staff would get. Nothing is sent "
    "to anyone else.\n"
    "<i>डेमो: आप एक दिल्ली स्कूल के प्रिंसिपल हैं। यह 10 नवंबर 2025 की शाम 18:00 बजे की योजना है।</i>"
)
LIMIT_REACHED = "You've used the demo 3 times today. Try again tomorrow."
SENT_PARENTS = "<b>Parents' channel would get:</b>"
SENT_STAFF = "<b>Staff group would get:</b>"
ASK = "<b>Would your school use this?</b>\nक्या आपका स्कूल इसका इस्तेमाल करेगा?"
ASK_COMMENT = ("Thanks. Reply with one line on why (optional). It may be quoted anonymously.\n"
               "धन्यवाद। एक लाइन में कारण लिखें (वैकल्पिक)।")
THANKS = "Thank you. Saved. / धन्यवाद।"
SKIPPED = "Skipped (demo). Nothing would be sent. Send /try to see it again."
PUBLIC_HELP = ("Saans sends school principals an air plan at 18:00 the evening before each "
               "school day. Send /try to see a demo.\n"
               "Saans हर स्कूल दिवस से पहले की शाम प्रिंसिपल को वायु योजना भेजता है। डेमो के लिए /try भेजें।")


@dataclass
class Trial:
    deps: Deps
    ctx: pipeline.Context
    series: dict = field(default_factory=pipeline.load_series_csv)
    now: callable = lambda: datetime.now(IST)

    @property
    def table(self):
        return self.deps.store.table

    @cached_property
    def _plan(self):
        made_at = datetime.combine(REPLAY_DAY, time(18), IST)
        f = forecast(self.series, made_at, hours=self.ctx.school.hour_window(), persistence=True)
        return protocol(f.hourly, self.ctx.school, f.target, self.ctx.rules)

    @cached_property
    def _shared_texts(self) -> dict[str, str]:
        return messages.render_plan(self._plan, self.ctx.school, replay_of=REPLAY_DAY)

    # --- /try ---------------------------------------------------------------

    def start(self, chat_id: int, user_id: int) -> str:
        if not self._take_slot(user_id):
            self.deps.messenger.send(chat_id, LIMIT_REACHED)
            return "rate_limited"
        summary = summarize(self._plan, self.ctx.school, self.ctx.model_call)
        principal = messages.render_plan(self._plan, self.ctx.school, replay_of=REPLAY_DAY,
                                         summary=summary)["principal"]
        self.deps.messenger.send(chat_id, INTRO)
        self.deps.messenger.send(chat_id, principal, buttons=[
            ("Approve & send (demo)", "try|approve"), ("Skip (demo)", "try|skip")])
        log.info("trial started by %s", user_id)
        return "started"

    def _take_slot(self, user_id: int) -> bool:
        today = self.now().date().isoformat()
        try:
            self.table.update_item(
                Key={"pk": f"try#{user_id}", "sk": today},
                UpdateExpression="ADD tries :one",
                ConditionExpression="attribute_not_exists(tries) OR tries < :max",
                ExpressionAttributeValues={":one": 1, ":max": TRIES_PER_DAY},
            )
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise

    # --- demo buttons and feedback -------------------------------------------

    def on_callback(self, cb: dict) -> None:
        user = (cb.get("from") or {}).get("id")
        msg = cb.get("message") or {}
        chat = (msg.get("chat") or {}).get("id", user)
        parts = (cb.get("data") or "").split("|")
        m = self.deps.messenger
        if parts[:2] == ["try", "approve"]:
            m.answer_callback(cb["id"], "Showing what parents and staff would get…")
            self._stamp(chat, msg, "<i>Approved (demo). Nothing was sent to anyone else.</i>")
            m.send(chat, f"{SENT_PARENTS}\n\n{self._shared_texts['parents']}")
            m.send(chat, f"{SENT_STAFF}\n\n{self._shared_texts['staff']}")
            self.ask(chat)
        elif parts[:2] == ["try", "skip"]:
            m.answer_callback(cb["id"], "Skipped (demo).")
            self._stamp(chat, msg, f"<i>{SKIPPED}</i>")
            self.ask(chat)
        elif len(parts) == 2 and parts[0] == "fb" and parts[1] in ANSWERS:
            self.record_answer(user, parts[1])
            m.answer_callback(cb["id"], "Thanks.")
            m.send(chat, ASK_COMMENT)
        else:
            m.answer_callback(cb["id"], "Unknown button.")

    def ask(self, chat_id: int) -> None:
        self.deps.messenger.send(chat_id, ASK, buttons=[
            ("Yes", "fb|yes"), ("Maybe", "fb|maybe"), ("No", "fb|no")])

    def _stamp(self, chat: int, msg: dict, stamp: str) -> None:
        text = msg.get("text")
        if not text or not msg.get("message_id"):
            return
        try:   # Telegram returns plain text; escape it before re-sending as HTML
            self.deps.messenger.edit(chat, int(msg["message_id"]), f"{html.escape(text)}\n\n{stamp}")
        except Exception as e:  # noqa: BLE001 - a stamp is cosmetic
            log.warning("demo stamp failed: %s", e)

    def record_answer(self, user_id: int, answer: str) -> None:
        self.table.put_item(Item={
            "pk": "feedback", "sk": str(user_id), "answer": answer, "comment": "",
            "awaiting_comment": True, "at": self.now().isoformat(timespec="seconds"),
        })

    def maybe_comment(self, chat_id: int, user_id: int, text: str) -> bool:
        """Save a plain-text reply as the comment if we asked this user for one."""
        item = self.table.get_item(Key={"pk": "feedback", "sk": str(user_id)}).get("Item")
        if not item or not item.get("awaiting_comment") or not text or text.startswith("/"):
            return False
        self.table.update_item(
            Key={"pk": "feedback", "sk": str(user_id)},
            UpdateExpression="SET #c = :c, awaiting_comment = :f",
            ExpressionAttributeNames={"#c": "comment"},
            ExpressionAttributeValues={":c": text[:500], ":f": False},
        )
        self.deps.messenger.send(chat_id, THANKS)
        return True

    # --- /feedback (admins) -------------------------------------------------

    def report(self) -> str:
        from boto3.dynamodb.conditions import Key
        items = self.table.query(KeyConditionExpression=Key("pk").eq("feedback")).get("Items", [])
        tries = self.table.scan(
            FilterExpression="begins_with(pk, :p)", ExpressionAttributeValues={":p": "try#"},
        ).get("Items", [])
        people = {i["pk"] for i in tries}
        counts = {k: sum(1 for i in items if i["answer"] == k) for k in ANSWERS}
        lines = [f"<b>Saans demo feedback</b>",
                 f"{len(people)} people tried the demo · {len(items)} answered",
                 " · ".join(f"{ANSWERS[k]}: {v}" for k, v in counts.items())]
        comments = sorted((i for i in items if i.get("comment")), key=lambda i: i["at"], reverse=True)
        if comments:
            lines.append("")
            lines += [f"[{ANSWERS[i['answer']]}] {html.escape(i['comment'])}" for i in comments[:10]]
        return "\n".join(lines)
