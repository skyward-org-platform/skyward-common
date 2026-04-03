"""Quick CLI to verify a Slack webhook is working.

Usage:
    python -m skyward.notifications.ping general
    python -m skyward.notifications.ping alerts
"""

import random
import sys

from dotenv import load_dotenv

load_dotenv()

from skyward.notifications import send_slack, SlackError

MESSAGES = [
    "beep boop. the robots are listening.",
    "this is a test. this is only a test. had this been an actual emergency you'd already be on fire.",
    "slack webhook works. mass panic averted.",
    "hey. just checking if anyone reads these. reply 'pineapple' if you do.",
    "good news: notifications work. bad news: you can never escape them.",
    "webhook online. your pipeline will now scream into the void slightly louder.",
    "if you're reading this, congratulations: you have successfully configured a webhook. your prize is more notifications.",
]


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m skyward.notifications.ping <channel>")
        print("  e.g. python -m skyward.notifications.ping general")
        print()
        print("Looks up SLACK_WEBHOOK_<CHANNEL> from your env vars.")
        sys.exit(1)

    channel = sys.argv[1].lower()
    msg = random.choice(MESSAGES)

    try:
        send_slack(channel, msg)
        print(f"Sent to #{channel}: {msg}")
    except SlackError as e:
        print(f"Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
