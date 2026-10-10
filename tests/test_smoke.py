import json

import responses

from scripts import smoke

URL = "https://abc.lambda-url.ap-south-1.on.aws/"


class FakeTable:
    def __init__(self, items):
        self.items = list(items)       # returned one per poll

    def get_item(self, Key):
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        return {"Item": item} if item else {}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(table, **kw):
    clock = Clock()
    return smoke.run(URL, "s3cret", 999, table, "demo", timeout=10, sleep=clock.sleep, clock=clock, **kw)


@responses.activate
def test_good_deploy_passes_and_sends_a_real_looking_update():
    responses.post(URL, body="ok")
    table = FakeTable([None, {"status": "CREATED", "updated_at": "9999"},
                       {"status": "SENT", "updated_at": "9999"}])
    assert run(table) is None
    req = responses.calls[0].request
    assert req.headers["X-Telegram-Bot-Api-Secret-Token"] == "s3cret"
    body = json.loads(req.body)
    assert body["message"]["text"] == "/replay 2025-11-10" and body["message"]["from"]["id"] == 999


@responses.activate
def test_wrong_secret_or_broken_webhook_fails():
    responses.post(URL, status=401)
    assert run(FakeTable([None])) == "webhook returned HTTP 401"


@responses.activate
def test_plan_never_sent_times_out():
    responses.post(URL, body="ok")
    assert "did not reach SENT" in run(FakeTable([{"status": "CREATED", "updated_at": "9999"}]))


@responses.activate
def test_stale_sent_plan_from_an_earlier_run_does_not_count():
    responses.post(URL, body="ok")
    assert "did not reach SENT" in run(FakeTable([{"status": "SENT", "updated_at": "2000-01-01"}]))


def test_stack_info_reads_outputs_and_params():
    class Cfn:
        def describe_stacks(self, StackName):
            return {"Stacks": [{"Outputs": [{"OutputKey": "WebhookUrl", "OutputValue": URL},
                                            {"OutputKey": "TableName", "OutputValue": "t"}],
                                "Parameters": [{"ParameterKey": "AdminUserIds", "ParameterValue": "5,6"},
                                               {"ParameterKey": "SsmPath", "ParameterValue": "saans"}]}]}
    assert smoke.stack_info(Cfn(), "saans") == (URL, "t", 5, "saans")
