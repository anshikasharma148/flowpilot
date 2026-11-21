"""Email plugin - delivers real email.

Providers (EMAIL_PROVIDER, default "auto"):
* brevo - Brevo transactional email HTTPS API (BREVO_API_KEY). Works on hosts that block outgoing SMTP.
* smtp  - any SMTP server, e.g. Gmail (smtp.gmail.com:587 + an app password). Port 465 uses implicit TLS.
* dry_run - records the email without sending it (used by the automated tests only).

"auto" picks Brevo when BREVO_API_KEY is set, otherwise SMTP when SMTP_HOST is set. With no provider
configured, sending FAILS with a clear message - it is never silently simulated.
"""
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

import httpx
import markdown

from .. import config
from ..db import EmailLog
from .base import Plugin, PluginContext, action, register

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


class EmailNotConfigured(RuntimeError):
    pass


def provider() -> str | None:
    p = config.EMAIL_PROVIDER
    if p == "auto":
        if config.BREVO_API_KEY:
            return "brevo"
        if config.SMTP_HOST:
            return "smtp"
        return None
    return p if p in ("brevo", "smtp", "dry_run") else None


def status() -> dict:
    p = provider()
    return {"provider": p, "configured": p in ("brevo", "smtp"), "from": config.EMAIL_FROM or None,
            "smtp_host": config.SMTP_HOST or None if p == "smtp" else None}


def _recipients(to) -> list[str]:
    if isinstance(to, str):
        to = re.split(r"[,;\s]+", to)
    return [t.strip() for t in (to or []) if t and t.strip()]


_TABLE_STYLE = {
    "<table>": '<table style="border-collapse:collapse;margin:8px 0;font-size:13px">',
    "<th>": '<th style="border:1px solid #d0d4dc;background:#f3f4f8;padding:6px 10px;text-align:left">',
    "<td>": '<td style="border:1px solid #d0d4dc;padding:6px 10px">',
}


def render_html(body_md: str) -> str:
    html = markdown.markdown(body_md, extensions=["tables"])
    for tag, styled in _TABLE_STYLE.items():
        html = html.replace(tag, styled)
    return ('<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.5;color:#1c2330">'
            f'{html}<hr style="border:0;border-top:1px solid #e2e5ea;margin:20px 0"/>'
            '<p style="color:#667085;font-size:12px">Sent automatically by FlowPilot workflow automation.</p></div>')


def _send_smtp(recipients: list[str], subject: str, text: str, html: str) -> str:
    if not config.EMAIL_FROM:
        raise EmailNotConfigured("SMTP_FROM (or EMAIL_FROM) is not set")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((config.EMAIL_FROM_NAME, config.EMAIL_FROM))
    msg["To"] = ", ".join(recipients)
    msg_id = make_msgid(domain=config.EMAIL_FROM.split("@")[-1])
    msg["Message-ID"] = msg_id
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    if config.SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
    with server as s:
        if config.SMTP_PORT != 465:
            s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.sendmail(config.EMAIL_FROM, recipients, msg.as_string())
    return msg_id


def _send_brevo(recipients: list[str], subject: str, text: str, html: str) -> str:
    if not config.EMAIL_FROM:
        raise EmailNotConfigured("EMAIL_FROM is not set (use the sender address verified in Brevo)")
    resp = httpx.post(
        BREVO_URL,
        headers={"api-key": config.BREVO_API_KEY, "accept": "application/json"},
        json={"sender": {"name": config.EMAIL_FROM_NAME, "email": config.EMAIL_FROM},
              "to": [{"email": r} for r in recipients],
              "subject": subject, "htmlContent": html, "textContent": text},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Brevo API {resp.status_code}: {resp.text[:300]}")
    return str(resp.json().get("messageId", ""))


def deliver(recipients: list[str], subject: str, body_md: str) -> tuple[str, str]:
    """Send for real. Returns (provider, message_id). Raises on any failure."""
    p = provider()
    if p is None:
        raise EmailNotConfigured(
            "No email provider is configured. Set BREVO_API_KEY + EMAIL_FROM, or SMTP_HOST/SMTP_USER/"
            "SMTP_PASSWORD/SMTP_FROM in backend/.env (see README).")
    if p == "dry_run":
        return p, ""
    html = render_html(body_md)
    send = _send_brevo if p == "brevo" else _send_smtp
    return p, send(recipients, subject, body_md, html)


@register
class EmailPlugin(Plugin):
    name = "email"
    description = "Send real email (Brevo HTTPS API or SMTP, e.g. Gmail). Markdown body is rendered to HTML."
    category = "integration"

    def mode(self) -> str:
        p = provider()
        return "live" if p in ("brevo", "smtp") else ("dry-run" if p == "dry_run" else "not configured")

    @action("send", "Send an email (Markdown body is rendered to HTML).",
            inputs={"to": "recipient(s), comma separated or list", "subject": "subject line",
                    "body": "Markdown body"},
            outputs={"status": "sent|dry_run", "to": "list", "email_id": "int", "provider": "str",
                     "message_id": "str"},
            required=("to", "body"), side_effect=True)
    def send(self, ctx: PluginContext, to=None, subject="Workflow notification", body="") -> dict:
        recipients = _recipients(to)
        if not recipients:
            raise ValueError("email.send requires at least one recipient")
        bad = [r for r in recipients if not EMAIL_RE.match(r)]
        if bad:
            raise ValueError(f"invalid email address(es): {', '.join(bad)}")
        prov, message_id, error = provider(), None, None
        try:
            prov, message_id = deliver(recipients, subject, body)
            status = "dry_run" if prov == "dry_run" else "sent"
        except Exception as e:  # noqa: BLE001 - recorded, then re-raised so the step fails visibly
            status, error = "failed", f"{type(e).__name__}: {e}"
        log = EmailLog(to=", ".join(recipients), subject=subject, body=body, status=status, error=error,
                       provider=prov, message_id=message_id or None, run_id=ctx.run_id)
        ctx.db.add(log)
        ctx.db.commit()
        if status == "failed":
            raise RuntimeError(f"Email not sent: {error}")
        return {"status": status, "to": recipients, "email_id": log.id, "provider": prov,
                "message_id": message_id or ""}
