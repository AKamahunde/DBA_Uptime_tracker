"""
DBTRAC Proactive Outage & Incident Alert Dispatcher
===================================================
Provides asynchronous, non-blocking outbound notifications via:
  1. Corporate Email (SMTP / Exchange / Office 365 / Internal Relay)
  2. Telegram Bot API
  3. Generic Webhook (Slack / MS Teams / Automation)

Triggered automatically when:
  - An unplanned database or infrastructure outage is detected.
  - MySQL DR replication breaks (threads down or stalled).
  - High severity incidents are registered.

Environment Variables:
  # Email Settings (SMTP)
  - EMAIL_HOST             : SMTP server (e.g. mail.nms.go.ug or smtp.office365.com)
  - EMAIL_PORT             : Port (25 for internal relay, 587 for STARTTLS, 465 for SSL)
  - EMAIL_HOST_USER        : SMTP username (optional if relay is unauthenticated)
  - EMAIL_HOST_PASSWORD    : SMTP password
  - EMAIL_USE_TLS          : "true" for STARTTLS (port 587)
  - EMAIL_USE_SSL          : "true" for SSL (port 465)
  - EMAIL_FROM             : "From" address (e.g. dbtrac-alerts@nms.go.ug)
  - ALERT_EMAIL_RECIPIENTS : Comma-separated DBA emails (e.g. dba@nms.go.ug,sysadmin@nms.go.ug)

  # Telegram Settings
  - TELEGRAM_BOT_TOKEN     : Telegram bot token
  - TELEGRAM_CHAT_ID       : Telegram chat / group ID

  # Webhook Settings
  - ALERT_WEBHOOK_URL      : Generic JSON webhook URL

  # Global Control
  - ALERT_ENABLED          : "true" / "false" (defaults to true)
  - DBTRAC_WEB_URL         : Base URL for dashboard links in emails (e.g. http://192.168.35.38:90)
"""

import os
import json
import logging
import smtplib
import threading
import urllib.request
import urllib.error
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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


def _send_email_alert(title, details, severity, target, now_str):
    """Sends a structured HTML and plaintext alert email via corporate SMTP."""
    host = os.environ.get("EMAIL_HOST")
    recipients_str = os.environ.get("ALERT_EMAIL_RECIPIENTS", "")
    if not host or not recipients_str:
        return

    recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]
    if not recipients:
        return

    port = int(os.environ.get("EMAIL_PORT", 25))
    user = os.environ.get("EMAIL_HOST_USER", "")
    password = os.environ.get("EMAIL_HOST_PASSWORD", "")
    use_tls = os.environ.get("EMAIL_USE_TLS", "false").lower() in ("true", "1", "yes")
    use_ssl = os.environ.get("EMAIL_USE_SSL", "false").lower() in ("true", "1", "yes")
    from_email = os.environ.get("EMAIL_FROM") or user or "dbtrac-alerts@nms.go.ug"
    tracker_url = os.environ.get("DBTRAC_WEB_URL", "http://192.168.35.38:90")

    color_accent = "#dc2626" if str(severity).lower() in ("critical", "failed") else "#d97706"

    # Clean, Minimalist Corporate HTML Email Body
    is_crit = str(severity).lower() in ("critical", "failed")
    badge_bg = "#fee2e2" if is_crit else "#fef3c7"
    badge_color = "#991b1b" if is_crit else "#92400e"
    badge_border = "#fca5a5" if is_crit else "#fcd34d"

    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    a[x-apple-data-detectors] {{ color: inherit !important; text-decoration: none !important; }}
  </style>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; padding: 20px 10px; margin: 0;">
  
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width: 580px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden;">
    
    <!-- Header -->
    <tr>
      <td style="background: #00205B; padding: 18px 24px; border-bottom: 3px solid {color_accent};">
        <div style="color: #ffffff; font-size: 1.1rem; font-weight: 700; letter-spacing: 0.3px;">DBTRAC Operational Alert</div>
        <div style="color: #94a3b8; font-size: 0.78rem; margin-top: 3px;">National Medical Stores · Database &amp; Infrastructure Monitoring</div>
      </td>
    </tr>

    <!-- Body -->
    <tr>
      <td style="padding: 24px;">
        
        <!-- Severity Badge -->
        <div style="margin-bottom: 10px;">
          <span style="display: inline-block; background: {badge_bg}; color: {badge_color}; border: 1px solid {badge_border}; font-size: 0.7rem; font-weight: 700; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.5px;">
            {severity}
          </span>
        </div>

        <!-- Title -->
        <div style="color: #0f172a; font-size: 1.18rem; font-weight: 700; line-height: 1.35; margin-bottom: 18px;">
          {title}
        </div>

        <!-- Details Table -->
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="font-size: 0.85rem; margin-bottom: 22px;">
          <tr style="border-top: 1px solid #f1f5f9; border-bottom: 1px solid #f1f5f9;">
            <td style="padding: 10px 0; color: #64748b; font-weight: 600; width: 24%; vertical-align: top;">Target</td>
            <td style="padding: 10px 0; color: #0f172a; font-weight: 700; vertical-align: top;">{target or 'System'}</td>
          </tr>
          <tr style="border-bottom: 1px solid #f1f5f9;">
            <td style="padding: 10px 0; color: #64748b; font-weight: 600; vertical-align: top;">Detected At</td>
            <td style="padding: 10px 0; color: #0f172a; font-weight: 600; vertical-align: top;">
              <span style="color: #0f172a; text-decoration: none;">{now_str} EAT</span>
            </td>
          </tr>
          <tr style="border-bottom: 1px solid #f1f5f9;">
            <td style="padding: 10px 0; color: #64748b; font-weight: 600; vertical-align: top;">Details</td>
            <td style="padding: 10px 0; vertical-align: top;">
              <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px 14px; color: #334155; font-size: 0.82rem; line-height: 1.5; white-space: pre-wrap;">{details}</div>
            </td>
          </tr>
        </table>

        <!-- Action Button -->
        <div style="text-align: center; margin-top: 20px; margin-bottom: 6px;">
          <a href="{tracker_url}" style="background: #00205B; color: #ffffff; text-decoration: none; padding: 11px 26px; font-size: 0.85rem; font-weight: 700; border-radius: 6px; display: inline-block;">
            Open DBTRAC Dashboard &rarr;
          </a>
        </div>

      </td>
    </tr>

    <!-- Footer -->
    <tr>
      <td style="background: #f8fafc; padding: 14px 24px; font-size: 0.72rem; color: #94a3b8; text-align: center; border-top: 1px solid #e2e8f0;">
        National Medical Stores · Automated operational telemetry alert from DBTRAC.
      </td>
    </tr>

  </table>

</body>
</html>"""
    plain_content = (
        f"DBTRAC OPERATIONAL ALERT: {title}\n"
        f"Severity: {severity}\n"
        f"Target: {target or 'System'}\n"
        f"Time: {now_str}\n\n"
        f"Details:\n{details}\n\n"
        f"Open Dashboard: {tracker_url}\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[{severity.upper()}] DBTRAC Alert: {title}"
    msg["From"] = from_email
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(plain_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=6)
        else:
            server = smtplib.SMTP(host, port, timeout=6)

        server.ehlo()
        if use_tls and not use_ssl:
            server.starttls()
            server.ehlo()

        if user and password:
            server.login(user, password)

        server.sendmail(from_email, recipients, msg.as_string())
        server.quit()
        logger.info(f"[DBTRAC Alert] Email notification delivered to {recipients}")
    except Exception as e:
        logger.warning(f"[DBTRAC Alert] SMTP delivery failed to {recipients} via {host}:{port}: {e}")


def _load_env_fallback():
    """Fallback standard-library loader for .env when python-dotenv is absent."""
    for path in [".env", "backend/.env", "../.env", "/opt/Uptime_tracker/.env"]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass


def dispatch_alert_async(title, details, severity="Critical", target=None):
    """
    Fires operational alert notifications across all configured channels
    (Email, Telegram, Webhook) in an isolated background daemon thread.
    Never blocks caller execution or web request cycles.
    Silently no-ops if no alerting channels are configured.
    """
    _load_env_fallback()
    enabled = os.environ.get("ALERT_ENABLED", "true").lower() in ("true", "1", "yes")
    if not enabled:
        return

    email_host = os.environ.get("EMAIL_HOST")
    email_recipients = os.environ.get("ALERT_EMAIL_RECIPIENTS")
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat = os.environ.get("TELEGRAM_CHAT_ID")
    webhook_url = os.environ.get("ALERT_WEBHOOK_URL")

    has_email = bool(email_host and email_recipients)
    has_telegram = bool(telegram_token and telegram_chat)
    has_webhook = bool(webhook_url)

    if not (has_email or has_telegram or has_webhook):
        return  # No alerting endpoints configured; dormant

    def _worker():
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Dispatch Email Alert
        if has_email:
            _send_email_alert(title, details, severity, target, now_str)

        # 2. Dispatch Telegram Alert
        if has_telegram:
            emoji = "🚨" if str(severity).lower() in ("critical", "high", "failed") else "⚠️"
            msg_text = (
                f"{emoji} *DBTRAC NOTIFICATION: {title}*\n"
                f"*Severity:* `{severity}`\n"
                f"*Target:* `{target or 'System'}`\n"
                f"*Time:* `{now_str}`\n"
                f"*Details:* {details}"
            )
            _send_telegram(telegram_token, telegram_chat, msg_text)

        # 3. Dispatch Generic Webhook (Teams / Slack / Automation)
        if has_webhook:
            payload = {
                "title": title,
                "severity": severity,
                "target": target,
                "timestamp": now_str,
                "details": details,
            }
            _post_json(webhook_url, payload)

    try:
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
    except Exception as e:
        logger.warning(f"[DBTRAC Alert] Failed to launch background alert thread: {e}")
