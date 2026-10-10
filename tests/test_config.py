import boto3
import pytest
from moto import mock_aws

from saans.config import ConfigError, load_config

ENV = {
    "SAANS_PRINCIPAL_USER_ID": "111",
    "SAANS_ADMIN_USER_IDS": "999, 998",
    "SAANS_PARENT_CHANNEL_ID": "-100",
    "SAANS_STAFF_GROUP_ID": "-200",
    "SAANS_DEMO_PARENT_CHANNEL_ID": "-300",
    "SAANS_DEMO_STAFF_GROUP_ID": "-400",
    "SAANS_TABLE": "saans-test",
}


@pytest.fixture
def ssm(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        client = boto3.client("ssm")
        for name, value in [("telegram_bot_token", "BOT"), ("telegram_webhook_secret", "HOOK"),
                            ("openaq_api_key", "AQ")]:
            client.put_parameter(Name=f"/saans/{name}", Value=value, Type="SecureString")
        yield client


def test_secrets_come_from_ssm_securestrings(ssm):
    cfg = load_config("demo", ENV, ssm=ssm)
    assert (cfg.bot_token, cfg.webhook_secret, cfg.openaq_key) == ("BOT", "HOOK", "AQ")
    assert cfg.settings.principal_chat_id == 111            # defaults to the principal's user id
    assert cfg.settings.admin_user_ids == frozenset({998, 999}) and cfg.admin_chat_id == 998
    assert cfg.table_name == "saans-test"


def test_missing_ssm_secret_fails_loudly(ssm):
    ssm.delete_parameter(Name="/saans/openaq_api_key")
    with pytest.raises(ConfigError, match="openaq_key"):
        load_config("demo", ENV, ssm=ssm)


def test_custom_prefix(ssm):
    ssm.put_parameter(Name="/other/telegram_bot_token", Value="B2", Type="SecureString")
    ssm.put_parameter(Name="/other/telegram_webhook_secret", Value="H2", Type="SecureString")
    ssm.put_parameter(Name="/other/openaq_api_key", Value="A2", Type="SecureString")
    cfg = load_config("demo", {**ENV, "SAANS_SSM_PREFIX": "/other/"}, ssm=ssm)
    assert cfg.bot_token == "B2"


def test_local_mode_reads_env():
    env = {**ENV, "SAANS_LOCAL": "1", "TELEGRAM_BOT_TOKEN": "b", "TELEGRAM_WEBHOOK_SECRET": "h",
           "OPENAQ_API_KEY": "a"}
    assert load_config("demo", env).bot_token == "b"
    with pytest.raises(ConfigError, match="bot_token"):
        load_config("demo", {**env, "TELEGRAM_BOT_TOKEN": ""})


@pytest.mark.parametrize("key,value,match", [
    ("SAANS_PRINCIPAL_USER_ID", "", "SAANS_PRINCIPAL_USER_ID"),
    ("SAANS_PARENT_CHANNEL_ID", "abc", "integer"),
    ("SAANS_ADMIN_USER_IDS", "", "SAANS_ADMIN_USER_IDS"),
])
def test_bad_ids_fail_loudly(key, value, match):
    env = {**ENV, key: value, "SAANS_LOCAL": "1", "TELEGRAM_BOT_TOKEN": "b",
           "TELEGRAM_WEBHOOK_SECRET": "h", "OPENAQ_API_KEY": "a"}
    with pytest.raises(ConfigError, match=match):
        load_config("demo", env)
