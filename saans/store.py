"""Plan records in the single DynamoDB table (eng review D5).

Key: PK = school id, SK = "plan#<target date>#<mode>" (mode: live | replay).
Every status change is a conditional update, so a double tap, a webhook
retry or the 07:00 expiry racing an Approve can only win once (eng R1).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


def plan_sk(target: date, mode: str) -> str:
    return f"plan#{target.isoformat()}#{mode}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PlanStore:
    def __init__(self, table):
        self.table = table

    def get(self, school: str, target: date, mode: str) -> dict | None:
        return self.table.get_item(Key={"pk": school, "sk": plan_sk(target, mode)}).get("Item")

    def create(self, school: str, target: date, mode: str, texts: dict[str, str]) -> bool:
        """Insert a CREATED plan; False if one already exists for this key."""
        try:
            self.table.put_item(
                Item=self._new_item(school, target, mode, texts),
                ConditionExpression=Attr("pk").not_exists(),
            )
            return True
        except ClientError as e:
            if _conditional(e):
                return False
            raise

    def reset(self, school: str, target: date, mode: str, texts: dict[str, str]) -> None:
        """Overwrite with a fresh CREATED plan (replay reruns only)."""
        if mode != "replay":
            raise ValueError("only replay plans can be reset")
        self.table.put_item(Item=self._new_item(school, target, mode, texts))

    def transition(self, school: str, target: date, mode: str, frm: set[str], to: str,
                   event: str, by: str | None = None, extra: dict[str, Any] | None = None) -> bool:
        """Move status from any of `frm` to `to`; False if the plan was not in `frm`."""
        names = {"#s": "status"}
        values = {":to": to, ":ev": [{"at": _now(), "event": event, "by": by or "system"}],
                  ":now": _now()}
        sets = ["#s = :to", "updated_at = :now", "events = list_append(events, :ev)"]
        for i, (k, v) in enumerate((extra or {}).items()):
            names[f"#x{i}"] = k
            values[f":x{i}"] = v
            sets.append(f"#x{i} = :x{i}")
        try:
            self.table.update_item(
                Key={"pk": school, "sk": plan_sk(target, mode)},
                UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression=Attr("status").is_in(sorted(frm)),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as e:
            if _conditional(e):
                return False
            raise

    def set_delivery(self, school: str, target: date, mode: str, channel: str, state: str) -> None:
        self.table.update_item(
            Key={"pk": school, "sk": plan_sk(target, mode)},
            UpdateExpression="SET delivery.#c = :s, updated_at = :now",
            ExpressionAttributeNames={"#c": channel},
            ExpressionAttributeValues={":s": state, ":now": _now()},
        )

    def plans_for(self, school: str, target: date, mode: str = "live") -> list[dict]:
        resp = self.table.query(
            KeyConditionExpression=Key("pk").eq(school) & Key("sk").eq(plan_sk(target, mode)))
        return resp.get("Items", [])

    @staticmethod
    def _new_item(school: str, target: date, mode: str, texts: dict[str, str]) -> dict:
        now = _now()
        return {
            "pk": school, "sk": plan_sk(target, mode), "target": target.isoformat(), "mode": mode,
            "status": "CREATED", "texts": texts, "delivery": {},
            "events": [{"at": now, "event": "created", "by": "system"}],
            "created_at": now, "updated_at": now,
        }


def _conditional(e: ClientError) -> bool:
    return e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
