"""Telegram webhook: secret check, then principal/admin authorization (eng R3).

Every request must carry Telegram's X-Telegram-Bot-Api-Secret-Token header,
otherwise 401. Callbacks (Approve/Skip) and non-admin commands are accepted
only from the stored principal; /replay only from admins. Anyone else gets
"not registered". Handled updates always return 200 so Telegram does not
retry them.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
from datetime import date
from typing import Callable

from saans import lifecycle
from saans.lifecycle import Deps

log = logging.getLogger(__name__)

NOT_REGISTERED = "Sorry, this bot only works for registered school staff."
HELP = "You'll get tomorrow's air plan here at 18:00 on the evening before each school day."


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

    s = deps.settings
    if cb := update.get("callback_query"):
        user = (cb.get("from") or {}).get("id")
        if user != s.principal_user_id:
            deps.messenger.answer_callback(cb["id"], NOT_REGISTERED)
            return _response(200)
        try:
            action, target, mode = lifecycle.parse_callback(cb.get("data", ""))
        except ValueError:
            deps.messenger.answer_callback(cb["id"], "Unknown button.")
            return _response(200)
        fn = lifecycle.approve if action == "approve" else lifecycle.skip
        outcome = fn(deps, target, mode, by=user)
        deps.messenger.answer_callback(cb["id"], _callback_text(outcome))
        log.info("callback %s %s %s by %s -> %s", action, target, mode, user, outcome)
        return _response(200)

    if msg := update.get("message"):
        user = (msg.get("from") or {}).get("id")
        chat = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if text.startswith("/replay"):
            if user not in s.admin_user_ids:
                deps.messenger.send(chat, NOT_REGISTERED)
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
            deps.messenger.send(chat, HELP)
        else:
            deps.messenger.send(chat, NOT_REGISTERED)
        return _response(200)

    return _response(200)  # other update types are ignored


def _callback_text(outcome: str) -> str:
    return {
        "posted": "Approved. Parents and staff have been notified.",
        "failed": "Approved, but some channels failed. See the message above.",
        "skipped": "Skipped. Nothing was sent.",
        "expired": "This plan expired at 07:00.",
        "already_done": "Already approved.",
        "already_skipped": "Already skipped.",
        "not_found": "This plan no longer exists.",
    }.get(outcome, "Done.")
