"""Slack webhook notifications."""

import os
from typing import Optional

import requests


class SlackError(Exception):
    """Raised when a Slack webhook request fails."""


def send_slack(
    channel: str,
    text: str,
    *,
    webhooks: Optional[dict] = None,
) -> int:
    """Send a message to a Slack channel via webhook.

    Args:
        channel: Channel key (e.g. "general", "alerts", "pipeline").
            Used to look up the webhook URL.
        text: Message body.
        webhooks: Optional dict mapping channel keys to webhook URLs.
            Falls back to env var ``SLACK_WEBHOOK_{CHANNEL}`` (uppercased).

    Returns:
        HTTP status code from the Slack API (200 on success).

    Raises:
        SlackError: If the webhook URL is missing, the request fails,
            or Slack returns a non-200 status.
    """
    url = None
    if webhooks:
        url = webhooks.get(channel)

    if not url:
        env_key = f"SLACK_WEBHOOK_{channel.upper()}"
        url = os.environ.get(env_key)

    if not url:
        raise SlackError(
            f"No webhook URL for channel '{channel}'. "
            f"Pass it via the webhooks dict or set SLACK_WEBHOOK_{channel.upper()}."
        )

    try:
        resp = requests.post(url, json={"text": text}, timeout=10)
    except requests.RequestException as exc:
        raise SlackError(f"Request to Slack failed: {exc}") from exc

    if resp.status_code != 200:
        raise SlackError(
            f"Slack returned {resp.status_code}: {resp.text}"
        )

    return resp.status_code
