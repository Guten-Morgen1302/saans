import os

import boto3
import pytest
from moto import mock_aws

from saans.lifecycle import Deps, MessengerError, Settings
from saans.store import PlanStore

SETTINGS = Settings(
    school_id="demo-delhi-01",
    principal_user_id=111, principal_chat_id=111,
    admin_user_ids=frozenset({999}),
    parent_channel_id=-100, staff_group_id=-200,
    demo_parent_channel_id=-300, demo_staff_group_id=-400,
)


class FakeMessenger:
    def __init__(self):
        self.sent = []          # (chat_id, text, buttons)
        self.answers = []       # (callback_id, text)
        self.fail_chats = {}    # chat_id -> number of failures before success
        self._next = 1

    def send(self, chat_id, text, buttons=None):
        if self.fail_chats.get(chat_id, 0) > 0:
            self.fail_chats[chat_id] -= 1
            raise MessengerError(f"telegram error for {chat_id}")
        self.sent.append((chat_id, text, buttons))
        self._next += 1
        return self._next

    def answer_callback(self, callback_id, text):
        self.answers.append((callback_id, text))

    def to(self, chat_id):
        return [t for c, t, _ in self.sent if c == chat_id]


@pytest.fixture
def table():
    os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        ddb = boto3.resource("dynamodb")
        t = ddb.create_table(
            TableName="saans",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield t


@pytest.fixture
def deps(table):
    return Deps(store=PlanStore(table), messenger=FakeMessenger(), settings=SETTINGS,
                sleep=lambda s: None)


TEXTS = {"principal": "PLAN for principal", "parents": "NOTICE for parents", "staff": "LIST for staff"}
