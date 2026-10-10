"""Plan lifecycle (eng review R1; Edit toggles deferred by CEO D6).

    CREATED ──send──▶ SENT ──Approve──▶ APPROVED ──▶ POSTING ──▶ POSTED
       ▲  (retry resends)  │                                   └──▶ FAILED (principal told)
       │                   ├──Skip─────▶ SKIPPED
       │                   └──07:00────▶ EXPIRED (late Approve rejected)
    /replay resets its replay plan to CREATED; replay plans get no reminder or expiry.

Invalid moves (e.g. SKIPPED → APPROVED, EXPIRED → POSTING) are impossible
because every transition is a conditional update from the expected state.

Every final state is stamped on the principal's plan message and its buttons
are removed (design 5A). Stamping is best-effort (eng R19): an edit failure is
logged, never changes plan state, and a final stamp falls back to a new message.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Protocol

from saans import messages
from saans.ingest import IST
from saans.store import PlanStore

log = logging.getLogger(__name__)

POST_ATTEMPTS = 3
DONE = {"APPROVED", "POSTING", "POSTED", "FAILED"}


class MessengerError(RuntimeError):
    pass


class Messenger(Protocol):
    def send(self, chat_id: int, text: str, buttons: list[tuple[str, str]] | None = None) -> int: ...
    def edit(self, chat_id: int, message_id: int, text: str) -> None: ...
    def answer_callback(self, callback_id: str, text: str) -> None: ...


@dataclass(frozen=True)
class Settings:
    school_id: str
    principal_user_id: int
    principal_chat_id: int
    admin_user_ids: frozenset[int]
    parent_channel_id: int
    staff_group_id: int
    demo_parent_channel_id: int
    demo_staff_group_id: int

    def channels(self, mode: str) -> dict[str, int]:
        if mode == "replay":
            return {"parents": self.demo_parent_channel_id, "staff": self.demo_staff_group_id}
        return {"parents": self.parent_channel_id, "staff": self.staff_group_id}


@dataclass
class Deps:
    store: PlanStore
    messenger: Messenger
    settings: Settings
    sleep: Callable[[float], None] = field(default=_time.sleep)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(IST))


def callback_data(action: str, target: date, mode: str) -> str:
    return f"{action}|{target.isoformat()}|{mode}"


def parse_callback(data: str) -> tuple[str, date, str]:
    action, day, mode = data.split("|")
    if action not in ("approve", "skip") or mode not in ("live", "replay"):
        raise ValueError(f"bad callback data {data!r}")
    return action, date.fromisoformat(day), mode


def send_plan(deps: Deps, target: date, mode: str, texts: dict[str, str]) -> str:
    """Create (if needed) and send the plan to the principal. Idempotent per key.

    texts: {"principal": ..., "parents": ..., "staff": ...} (Telegram HTML)
    """
    s, st = deps.settings, deps.store
    if mode == "replay":
        old = st.get(s.school_id, target, mode)
        if old and old.get("principal_msg_id"):
            _stamp(deps, old, messages.stamp("replaced"), fallback=False)   # eng R18
        st.reset(s.school_id, target, mode, texts)
    elif not st.create(s.school_id, target, mode, texts):
        existing = st.get(s.school_id, target, mode)
        if existing["status"] != "CREATED":
            log.info("plan %s %s already %s; not resending", target, mode, existing["status"])
            return "already_sent"
        texts = existing["texts"]
        log.warning("plan %s %s was saved but never sent; resending", target, mode)

    msg_id = deps.messenger.send(
        s.principal_chat_id, texts["principal"],
        buttons=messages.buttons(callback_data("approve", target, mode),
                                 callback_data("skip", target, mode)),
    )
    st.transition(s.school_id, target, mode, {"CREATED"}, "SENT", "sent",
                  extra={"principal_msg_id": msg_id})
    return "sent"


def is_current(deps: Deps, target: date, mode: str, message_id) -> bool | None:
    """Did this tap come from the plan's current message? None if no plan (eng R18)."""
    plan = deps.store.get(deps.settings.school_id, target, mode)
    if not plan:
        return None
    stored = plan.get("principal_msg_id")
    return stored is not None and message_id is not None and int(stored) == int(message_id)


def approve(deps: Deps, target: date, mode: str, by: int,
            on_accepted: Callable[[], None] | None = None) -> str:
    s, st = deps.settings, deps.store
    if not st.transition(s.school_id, target, mode, {"SENT"}, "APPROVED", "approved", by=str(by)):
        return _explain(deps, target, mode)
    if on_accepted:
        on_accepted()                                     # design 6A: answer the tap first
    approved_at = deps.now()
    st.transition(s.school_id, target, mode, {"APPROVED"}, "POSTING", "posting")
    plan = st.get(s.school_id, target, mode)
    _stamp(deps, plan, messages.stamp("sending"), fallback=False)

    failed = []
    for channel, chat_id in s.channels(mode).items():
        st.set_delivery(s.school_id, target, mode, channel, "POSTING")
        if _post_with_retries(deps, chat_id, plan["texts"][channel]):
            st.set_delivery(s.school_id, target, mode, channel, "POSTED")
        else:
            st.set_delivery(s.school_id, target, mode, channel, "FAILED")
            failed.append(channel)
    final = "FAILED" if failed else "POSTED"
    st.transition(s.school_id, target, mode, {"POSTING"}, final, final.lower())

    plan = st.get(s.school_id, target, mode)
    _stamp(deps, plan, messages.stamp("approved", approved_at.time(), plan["delivery"]))
    for channel in failed:
        _send_quietly(deps, s.principal_chat_id,
                      messages.render_forward(channel, plan["texts"][channel]))
    return "failed" if failed else "posted"


def skip(deps: Deps, target: date, mode: str, by: int) -> str:
    s = deps.settings
    if not deps.store.transition(s.school_id, target, mode, {"SENT"}, "SKIPPED", "skipped", by=str(by)):
        return _explain(deps, target, mode)
    _stamp(deps, deps.store.get(s.school_id, target, mode), messages.stamp("skipped", deps.now().time()))
    return "skipped"


def remind(deps: Deps, today: date) -> str:
    """06:30: nudge the principal only if today's live plan is still waiting."""
    s = deps.settings
    plan = deps.store.get(s.school_id, today, "live")
    if not plan or plan["status"] != "SENT":
        return "nothing_to_remind"
    deps.messenger.send(s.principal_chat_id, messages.render_reminder(today))
    return "reminded"


def expire(deps: Deps, today: date) -> str:
    """07:00: a live plan nobody answered expires; nothing is sent to parents."""
    s = deps.settings
    if not deps.store.transition(s.school_id, today, "live", {"SENT"}, "EXPIRED", "expired"):
        return "nothing_to_expire"
    _stamp(deps, deps.store.get(s.school_id, today, "live"), messages.stamp("expired"))
    return "expired"


def _explain(deps: Deps, target: date, mode: str) -> str:
    plan = deps.store.get(deps.settings.school_id, target, mode)
    status = plan["status"] if plan else None
    if status == "EXPIRED":
        return "expired"
    if status == "SKIPPED":
        return "already_skipped"
    if status in DONE:
        return "already_done"
    return "not_found"


def _stamp(deps: Deps, plan: dict, stamp_text: str, fallback: bool = True) -> None:
    """Rewrite the principal's plan with an outcome stamp and no buttons (eng R19)."""
    chat = deps.settings.principal_chat_id
    try:
        deps.messenger.edit(chat, int(plan["principal_msg_id"]),
                            messages.with_stamp(plan["texts"]["principal"], stamp_text))
        return
    except (MessengerError, KeyError, TypeError) as e:
        log.warning("stamp edit failed for plan %s: %s", plan.get("sk"), e)
    if fallback:
        _send_quietly(deps, chat, stamp_text)


def _send_quietly(deps: Deps, chat_id: int, text: str) -> None:
    try:
        deps.messenger.send(chat_id, text)
    except MessengerError as e:
        log.error("could not send to %s: %s", chat_id, e)


def _post_with_retries(deps: Deps, chat_id: int, text: str) -> bool:
    for attempt in range(1, POST_ATTEMPTS + 1):
        try:
            deps.messenger.send(chat_id, text)
            return True
        except MessengerError as e:
            log.warning("post to %s failed (attempt %d/%d): %s", chat_id, attempt, POST_ATTEMPTS, e)
            if attempt < POST_ATTEMPTS:
                deps.sleep(2 ** attempt)
    return False
