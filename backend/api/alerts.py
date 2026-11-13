"""
DBTRAC Proactive Outage & Incident Alert Dispatcher
===================================================
Provides asynchronous, non-blocking outbound webhook notifications
to Telegram, Slack, MS Teams, or custom automation webhooks when
operational outages, critical replication failures, or SLA breaches occur.

Environment Variables:
  - TELEGRAM_BOT_TOKEN : Telegram bot token (e.g. 123456:ABC-DEF...)
  - TELEGRAM_CHAT_ID   : Telegram destination chat or channel ID
  - ALERT_WEBHOOK_URL  : Generic JSON webhook URL (Slack / Teams / n8n / Opsgenie)
  - ALERT_ENABLED      : Global kill-switch (defaults to True if credentials are configured)
"""

import os
import json
import logging
import threading
import urllib.request
import urllib.error
from datetime import datetime

logger = logging.getLogger(__name__)


def _post_json(url, payload, headers=None, timeout=4):
    """Safely POSTs a JSON payload without raising exceptions."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        req.add_header("Content-Type", "application/json")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except Exception as e:
        logger.warning(f"[DBTRAC Alert] Webhook delivery failed ({url}): {e}")
        return None


def _send_telegram(bot_token, chat_id, text, timeout=4):
    """Sends a markdown-formatted message via the official Telegram Bot API."""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        return _post_json(url, payload, timeout=timeout)
    except Exception as e:
        logger.warning(f"[DBTRAC Alert] Telegram dispatch failed: {e}")
        return None


def dispatch_alert_async(title, details, severity="Critical", target=None):
    """
    Fires an operational alert notification in an isolated daemon thread.
    Never blocks caller execution or web request cycles.
    Silently no-ops if no alerting endpoints are configured.
    """
    enabled = os.environ.get("ALERT_ENABLED", "true").lower() in ("true", "1", "yes")
    if not enabled:
        return

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat = os.environ.get("TELEGRAM_CHAT_ID")
    webhook_url = os.environ.get("ALERT_WEBHOOK_URL")

    if not (telegram_token and telegram_chat) and not webhook_url:
        return  # No endpoints configured

    def _worker():
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        emoji = "🚨" if str(severity).lower() in ("critical", "high", "failed") else "⚠️"
        msg_text = (
            f"{emoji} *DBTRAC NOTIFICATION: {title}*\n"
            f"*Severity:* `{severity}`\n"
            f"*Target:* `{target or 'System'}`\n"
            f"*Time:* `{now_str}`\n"
            f"*Details:* {details}"
        )

        if telegram_token and telegram_chat:
            _send_telegram(telegram_token, telegram_chat, msg_text)

        if webhook_url:
            payload = {
                "title": title,
                "severity": severity,
                "target": target,
                "timestamp": now_str,
                "details": details,
                "text": msg_text,
            }
            _post_json(webhook_url, payload)

    try:
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
    except Exception as e:
        logger.warning(f"[DBTRAC Alert] Failed to launch background alert thread: {e}")
