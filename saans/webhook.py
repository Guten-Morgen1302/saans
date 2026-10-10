"""Telegram webhook: secret check, then principal/admin authorization (eng R3).

Every request must carry Telegram's X-Telegram-Bot-Api-Secret-Token header,
otherwise 401. Callbacks (Approve/Skip) and non-admin commands are accepted
only from the stored principal; /replay only from admins. Anyone else gets
"not registered". A tap only counts if it comes from the plan's current
message (eng R18). Handled updates always return 200 so Telegram does not
retry them.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
from datetime import date
from typing import Callable

from saans import lifecycle, messages
from saans.lifecycle import Deps

log = logging.getLogger(__name__)

OUTCOME_TOAST = {
    "skipped": "toast_skipped",
    "expired": "toast_expired",
    "already_done": "toast_done",
    "already_skipped": "toast_done",
    "not_found": "toast_gone",
}


def _response(status: int, body: str = "ok") -> dict:
    return {"statusCode": status, "body": body}


def handle(event: dict, deps: Deps, secret: str,
           run_replay: Callable[[date], str]) -> dict:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not secret or not hmac.compare_digest(headers.get("x-telegram-bot-api-secret-token", ""), secret):
        log.warning("webhook rejected: bad or missing secret token")
        return _response(401, "unauthorized")

    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        update = json.loads(body)
    except json.JSONDecodeError:
        return _response(400, "bad json")

    if cb := update.get("callback_query"):
        _handle_callback(cb, deps)
        return _response(200)

    s = deps.settings
    if msg := update.get("message"):
        user = (msg.get("from") or {}).get("id")
        chat = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if text.startswith("/replay"):
            if user not in s.admin_user_ids:
                deps.messenger.send(chat, messages.system("not_registered"))
                return _response(200)
            parts = text.split()
            try:
                day = date.fromisoformat(parts[1])
            except (IndexError, ValueError):
                deps.messenger.send(chat, "Usage: /replay YYYY-MM-DD")
                return _response(200)
            log.info("replay %s requested by %s", day, user)
            deps.messenger.send(chat, f"Replay {day}: {run_replay(day)}")
            return _response(200)
        if user == s.principal_user_id or user in s.admin_user_ids:
            deps.messenger.send(chat, messages.system("help"))
        else:
            deps.messenger.send(chat, messages.system("not_registered"))
        return _response(200)

    return _response(200)  # other update types are ignored


def _handle_callback(cb: dict, deps: Deps) -> None:
    answer = lambda key: deps.messenger.answer_callback(cb["id"], messages.toast(key))  # noqa: E731
    user = (cb.get("from") or {}).get("id")
    if user != deps.settings.principal_user_id:
        answer("not_registered")
        return
    try:
        action, target, mode = lifecycle.parse_callback(cb.get("data", ""))
    except ValueError:
        answer("toast_unknown")
        return

    current = lifecycle.is_current(deps, target, mode, (cb.get("message") or {}).get("message_id"))
    if current is None:
        answer("toast_gone")
        return
    if not current:
        answer("toast_replaced")
        return

    if action == "skip":
        outcome = lifecycle.skip(deps, target, mode, by=user)
        answer(OUTCOME_TOAST.get(outcome, "toast_done"))
    else:
        answered = []

        def accepted():
            answer("toast_sending")
            answered.append(True)

        outcome = lifecycle.approve(deps, target, mode, by=user, on_accepted=accepted)
        if not answered:
            answer(OUTCOME_TOAST.get(outcome, "toast_done"))
    log.info("callback %s %s %s by %s -> %s", action, target, mode, user, outcome)
