"""Runtime config (T10, CEO D9): secrets from SSM Parameter Store, IDs from env.

Secrets are SecureString parameters under SAANS_SSM_PREFIX (default /saans):
    /saans/telegram_bot_token, /saans/telegram_webhook_secret, /saans/openaq_api_key
They are read once per cold start. For local runs, SAANS_LOCAL=1 reads the
same three values from the environment (.env) instead.

Non-secret IDs come from the environment (set by template.yaml parameters).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from saans.lifecycle import Settings

SECRET_NAMES = {
    "bot_token": ("telegram_bot_token", "TELEGRAM_BOT_TOKEN"),
    "webhook_secret": ("telegram_webhook_secret", "TELEGRAM_WEBHOOK_SECRET"),
    "openaq_key": ("openaq_api_key", "OPENAQ_API_KEY"),
}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    settings: Settings
    bot_token: str
    webhook_secret: str
    openaq_key: str
    table_name: str
    admin_chat_id: int


def _int(env: Mapping[str, str], name: str, default: str | None = None) -> int:
    raw = env.get(name, default)
    if raw in (None, ""):
        raise ConfigError(f"missing environment variable {name}")
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer Telegram id, got {raw!r}") from None


def _secrets(env: Mapping[str, str], ssm) -> dict[str, str]:
    if env.get("SAANS_LOCAL") == "1":
        values = {k: env.get(envname, "") for k, (_, envname) in SECRET_NAMES.items()}
    else:
        prefix = env.get("SAANS_SSM_PREFIX", "/saans").rstrip("/")
        names = {f"{prefix}/{param}": key for key, (param, _) in SECRET_NAMES.items()}
        if ssm is None:
            import boto3
            ssm = boto3.client("ssm")
        resp = ssm.get_parameters(Names=list(names), WithDecryption=True)
        values = {names[p["Name"]]: p["Value"] for p in resp.get("Parameters", [])}
    missing = sorted(k for k in SECRET_NAMES if not values.get(k))
    if missing:
        raise ConfigError(f"missing secrets: {', '.join(missing)}")
    return values


def load_config(school_id: str, env: Mapping[str, str] | None = None, ssm=None) -> Config:
    env = os.environ if env is None else env
    principal = _int(env, "SAANS_PRINCIPAL_USER_ID")
    admins = frozenset(int(x) for x in env.get("SAANS_ADMIN_USER_IDS", "").split(",") if x.strip())
    if not admins:
        raise ConfigError("missing environment variable SAANS_ADMIN_USER_IDS")
    settings = Settings(
        school_id=school_id,
        principal_user_id=principal,
        principal_chat_id=_int(env, "SAANS_PRINCIPAL_CHAT_ID", str(principal)),
        admin_user_ids=admins,
        parent_channel_id=_int(env, "SAANS_PARENT_CHANNEL_ID"),
        staff_group_id=_int(env, "SAANS_STAFF_GROUP_ID"),
        demo_parent_channel_id=_int(env, "SAANS_DEMO_PARENT_CHANNEL_ID"),
        demo_staff_group_id=_int(env, "SAANS_DEMO_STAFF_GROUP_ID"),
    )
    secrets = _secrets(env, ssm)
    return Config(settings=settings, table_name=env.get("SAANS_TABLE", "saans"),
                  admin_chat_id=min(admins), **secrets)
