"""List the chats @SaansSchoolBot has been added to, with their numeric ids.

Add the bot to each channel/group (as admin in channels), post any message in
each, then run: python scripts/find_chats.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from scripts.env import load_env  # noqa: E402


def main() -> None:
    load_env()
    base = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
    body = requests.post(f"{base}/getUpdates", timeout=30, json={
        "allowed_updates": ["message", "channel_post", "my_chat_member"]}).json()
    if not body.get("ok"):
        sys.exit(f"Telegram error: {body.get('description')}")
    chats = {}
    for u in body["result"]:
        for key in ("message", "channel_post", "my_chat_member"):
            chat = (u.get(key) or {}).get("chat")
            if chat and chat["type"] != "private":
                chats[chat["id"]] = (chat["type"], chat.get("title", ""))
    if not chats:
        print("No group/channel seen yet. Add the bot to each chat, post a message there, and rerun.")
    for cid, (kind, title) in sorted(chats.items(), key=lambda x: x[1][1]):
        print(f"{cid:>16}  {kind:10} {title}")


if __name__ == "__main__":
    main()
