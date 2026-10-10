"""Plan lifecycle (eng review R1; Edit toggles deferred by CEO D6).

    CREATED ──send──▶ SENT ──Approve──▶ APPROVED ──▶ POSTING ──▶ POSTED
       ▲  (retry resends)  │                                   └──▶ FAILED (principal told)
       │                   ├──Skip─────▶ SKIPPED
       │                   └──07:00────▶ EXPIRED (late Approve rejected)
    /replay resets its replay plan to CREATED; replay plans get no reminder or expiry.

Invalid moves (e.g. SKIPPED → APPROVED, EXPIRED → POSTING) are impossible
because every transition is a conditional update from the expected state.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Protocol

from saans.store import PlanStore

log = logging.getLogger(__name__)

POST_ATTEMPTS = 3
DONE = {"APPROVED", "POSTING", "POSTED", "FAILED"}


class MessengerError(RuntimeError):
    pass


class Messenger(Protocol):
    def send(self, chat_id: int, text: str, buttons: list[tuple[str, str]] | None = None) -> int: ...
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


def callback_data(action: str, target: date, mode: str) -> str:
    return f"{action}|{target.isoformat()}|{mode}"


def parse_callback(data: str) -> tuple[str, date, str]:
    action, day, mode = data.split("|")
    if action not in ("approve", "skip") or mode not in ("live", "replay"):
        raise ValueError(f"bad callback data {data!r}")
    return action, date.fromisoformat(day), mode


def send_plan(deps: Deps, target: date, mode: str, texts: dict[str, str]) -> str:
    """Create (if needed) and send the plan to the principal. Idempotent per key.

    texts: {"principal": ..., "parents": ..., "staff": ...}
    """
    s, st = deps.settings, deps.store
    if mode == "replay":
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
        buttons=[("Approve", callback_data("approve", target, mode)),
                 ("Skip", callback_data("skip", target, mode))],
    )
    st.transition(s.school_id, target, mode, {"CREATED"}, "SENT", "sent",
                  extra={"principal_msg_id": msg_id})
    return "sent"


def approve(deps: Deps, target: date, mode: str, by: int) -> str:
    s, st = deps.settings, deps.store
    if not st.transition(s.school_id, target, mode, {"SENT"}, "APPROVED", "approved", by=str(by)):
        return _explain(deps, target, mode)
    st.transition(s.school_id, target, mode, {"APPROVED"}, "POSTING", "posting")
    plan = st.get(s.school_id, target, mode)
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
    if failed:
        deps.messenger.send(s.principal_chat_id,
                            f"Could not post the plan to: {', '.join(failed)}. Please share it manually.")
        return "failed"
    return "posted"


def skip(deps: Deps, target: date, mode: str, by: int) -> str:
    s = deps.settings
    if deps.store.transition(s.school_id, target, mode, {"SENT"}, "SKIPPED", "skipped", by=str(by)):
        return "skipped"
    return _explain(deps, target, mode)


def remind(deps: Deps, today: date) -> str:
    """06:30: nudge the principal only if today's live plan is still waiting."""
    s = deps.settings
    plan = deps.store.get(s.school_id, today, "live")
    if not plan or plan["status"] != "SENT":
        return "nothing_to_remind"
    deps.messenger.send(s.principal_chat_id,
                        "Reminder: today's air plan is waiting for Approve or Skip. It expires at 07:00.")
    return "reminded"


def expire(deps: Deps, today: date) -> str:
    """07:00: a live plan nobody answered expires; nothing is sent to parents."""
    s = deps.settings
    if deps.store.transition(s.school_id, today, "live", {"SENT"}, "EXPIRED", "expired"):
        return "expired"
    return "nothing_to_expire"


def _explain(deps: Deps, target: date, mode: str) -> str:
    plan = deps.store.get(deps.settings.school_id, target, mode)
    status = plan["status"] if plan else None
    if status == "EXPIRED":
        deps.messenger.send(deps.settings.principal_chat_id, "This plan expired at 07:00.")
        return "expired"
    if status == "SKIPPED":
        return "already_skipped"
    if status in DONE:
        return "already_done"
    return "not_found"


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
