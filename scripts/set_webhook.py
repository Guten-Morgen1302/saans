"""Point the Telegram bot at the deployed webhook URL with the secret token.

Usage: python scripts/set_webhook.py --stack saans
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saans.telegram import TelegramMessenger  # noqa: E402
from scripts.smoke import stack_info  # noqa: E402


def main() -> None:
    import boto3

    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", default="saans")
    args = ap.parse_args()
    url, _, _, ssm_path = stack_info(boto3.client("cloudformation"), args.stack)
    ssm = boto3.client("ssm")
    get = lambda n: ssm.get_parameter(Name=f"/{ssm_path}/{n}", WithDecryption=True)["Parameter"]["Value"]  # noqa: E731
    TelegramMessenger(get("telegram_bot_token")).set_webhook(url, get("telegram_webhook_secret"))
    print(f"webhook set to {url}")


if __name__ == "__main__":
    main()
