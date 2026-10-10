"""Post-deploy smoke test (CEO D11, eng R14).

Sends a synthetic Telegram update ("/replay 2025-11-10" from an admin) to the
deployed webhook URL with the real secret-token header, then waits until the
replay plan for 2025-11-11 reaches SENT. Exits non-zero with a reason otherwise.
This posts one replay message to the principal and nothing to live channels.

Usage: python scripts/smoke.py --stack saans
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
REPLAY_DAY, TARGET = "2025-11-10", "2025-11-11"


def stack_info(cfn, stack: str) -> tuple[str, str, int, str]:
    s = cfn.describe_stacks(StackName=stack)["Stacks"][0]
    out = {o["OutputKey"]: o["OutputValue"] for o in s.get("Outputs", [])}
    params = {p["ParameterKey"]: p["ParameterValue"] for p in s.get("Parameters", [])}
    admin = int(params["AdminUserIds"].split(",")[0])
    return out["WebhookUrl"], out["TableName"], admin, params.get("SsmPath", "saans")


def run(url: str, secret: str, admin: int, table, school_id: str,
        timeout: float = 60, sleep=time.sleep, clock=time.monotonic) -> str | None:
    """Returns None on success, or the failure reason."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    update = {"update_id": int(clock() * 1000) % 2**31,
              "message": {"message_id": 1, "date": int(time.time()), "text": f"/replay {REPLAY_DAY}",
                          "from": {"id": admin, "is_bot": False, "first_name": "smoke"},
                          "chat": {"id": admin, "type": "private"}}}
    try:
        resp = requests.post(url, json=update, timeout=65,
                             headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    except requests.RequestException as e:
        return f"webhook unreachable: {type(e).__name__}"
    if resp.status_code != 200:
        return f"webhook returned HTTP {resp.status_code}"
    deadline = clock() + timeout
    while clock() < deadline:
        item = table.get_item(Key={"pk": school_id, "sk": f"plan#{TARGET}#replay"}).get("Item")
        if item and item.get("status") == "SENT" and item.get("updated_at", "") >= started:
            return None
        sleep(3)
    return f"replay plan did not reach SENT within {timeout:.0f}s"


def main() -> None:
    import boto3

    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", default="saans")
    args = ap.parse_args()
    url, table_name, admin, ssm_path = stack_info(boto3.client("cloudformation"), args.stack)
    secret = boto3.client("ssm").get_parameter(
        Name=f"/{ssm_path}/telegram_webhook_secret", WithDecryption=True)["Parameter"]["Value"]
    school_id = yaml.safe_load((ROOT / "school.yaml").read_text(encoding="utf-8"))["id"]
    reason = run(url, secret, admin, boto3.resource("dynamodb").Table(table_name), school_id)
    if reason:
        sys.exit(f"SMOKE FAILED: {reason}")
    print(f"smoke ok: /replay {REPLAY_DAY} reached SENT")


if __name__ == "__main__":
    main()
