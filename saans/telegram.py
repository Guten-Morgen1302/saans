"""Telegram Bot API client implementing lifecycle.Messenger (parse_mode=HTML, design 13A)."""

from __future__ import annotations

import logging

import requests

from saans.lifecycle import MessengerError

log = logging.getLogger(__name__)

API = "https://api.telegram.org"


class TelegramMessenger:
    def __init__(self, token: str, session: requests.Session | None = None, timeout: float = 15.0):
        if not token:
            raise MessengerError("Telegram bot token is empty")
        self._base = f"{API}/bot{token}"
        self.session = session or requests.Session()
        self.timeout = timeout

    def _call(self, method: str, payload: dict) -> dict:
        try:
            resp = self.session.post(f"{self._base}/{method}", json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise MessengerError(f"{method}: {type(e).__name__}") from e
        try:
            body = resp.json()
        except ValueError:
            raise MessengerError(f"{method}: HTTP {resp.status_code}, non-JSON body") from None
        if not body.get("ok"):
            # The token is part of the URL, so never log the URL, only the API's description.
            raise MessengerError(f"{method}: {body.get('error_code')} {body.get('description')}")
        return body.get("result") or {}

    def send(self, chat_id: int, text: str, buttons: list[tuple[str, str]] | None = None) -> int:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                   "link_preview_options": {"is_disabled": True}}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [
                [{"text": label, "callback_data": data} for label, data in buttons]]}
        return int(self._call("sendMessage", payload)["message_id"])

    def edit(self, chat_id: int, message_id: int, text: str) -> None:
        """Replace the text and remove the buttons (design 5A)."""
        self._call("editMessageText", {
            "chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "reply_markup": {"inline_keyboard": []},
        })

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        """Long polling for the local runner (no public URL needed)."""
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        try:
            resp = self.session.post(f"{self._base}/getUpdates", json=payload,
                                     timeout=timeout + self.timeout)
        except requests.RequestException as e:
            raise MessengerError(f"getUpdates: {type(e).__name__}") from e
        body = resp.json()
        if not body.get("ok"):
            raise MessengerError(f"getUpdates: {body.get('error_code')} {body.get('description')}")
        return body.get("result") or []

    def delete_webhook(self) -> None:
        """Long polling and a webhook can't both be active."""
        self._call("deleteWebhook", {"drop_pending_updates": False})

    def set_webhook(self, url: str, secret_token: str) -> None:
        self._call("setWebhook", {"url": url, "secret_token": secret_token,
                                  "allowed_updates": ["message", "callback_query"],
                                  "drop_pending_updates": True})
