import json

import pytest
import requests
import responses

from saans.lifecycle import MessengerError
from saans.telegram import TelegramMessenger

BASE = "https://api.telegram.org/botTOKEN"


def body(call):
    return json.loads(call.request.body)


@responses.activate
def test_send_uses_html_and_inline_buttons():
    responses.post(f"{BASE}/sendMessage", json={"ok": True, "result": {"message_id": 42}})
    mid = TelegramMessenger("TOKEN").send(-100, "<b>hi</b>", buttons=[("Approve & send", "approve|x|live")])
    assert mid == 42
    b = body(responses.calls[0])
    assert b["parse_mode"] == "HTML" and b["chat_id"] == -100
    assert b["reply_markup"] == {"inline_keyboard": [[{"text": "Approve & send",
                                                       "callback_data": "approve|x|live"}]]}


@responses.activate
def test_send_without_buttons_has_no_keyboard():
    responses.post(f"{BASE}/sendMessage", json={"ok": True, "result": {"message_id": 1}})
    TelegramMessenger("TOKEN").send(1, "plain")
    assert "reply_markup" not in body(responses.calls[0])


@responses.activate
def test_edit_removes_buttons():
    responses.post(f"{BASE}/editMessageText", json={"ok": True, "result": {}})
    TelegramMessenger("TOKEN").edit(1, 42, "stamped")
    b = body(responses.calls[0])
    assert b["message_id"] == 42 and b["reply_markup"] == {"inline_keyboard": []}


@responses.activate
def test_api_error_raises_messenger_error_without_leaking_token():
    responses.post(f"{BASE}/editMessageText",
                   json={"ok": False, "error_code": 400,
                         "description": "Bad Request: message is not modified"})
    with pytest.raises(MessengerError) as e:
        TelegramMessenger("TOKEN").edit(1, 42, "same")
    assert "not modified" in str(e.value) and "TOKEN" not in str(e.value)


@responses.activate
def test_network_error_and_non_json_become_messenger_errors():
    responses.post(f"{BASE}/sendMessage", body=requests.ConnectionError("down"))
    with pytest.raises(MessengerError, match="ConnectionError"):
        TelegramMessenger("TOKEN").send(1, "x")
    responses.replace(responses.POST, f"{BASE}/sendMessage", body="<html>502</html>", status=502)
    with pytest.raises(MessengerError, match="502"):
        TelegramMessenger("TOKEN").send(1, "x")


@responses.activate
def test_answer_callback_truncates_to_200_chars():
    responses.post(f"{BASE}/answerCallbackQuery", json={"ok": True, "result": True})
    TelegramMessenger("TOKEN").answer_callback("cb", "x" * 300)
    assert len(body(responses.calls[0])["text"]) == 200


@responses.activate
def test_set_webhook_sends_secret_and_update_types():
    responses.post(f"{BASE}/setWebhook", json={"ok": True, "result": True})
    TelegramMessenger("TOKEN").set_webhook("https://x.lambda-url.aws/", "s3cret")
    b = body(responses.calls[0])
    assert b["secret_token"] == "s3cret" and b["allowed_updates"] == ["message", "callback_query"]


def test_empty_token_rejected():
    with pytest.raises(MessengerError):
        TelegramMessenger("")
