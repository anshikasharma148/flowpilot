"""Email plugin - SMTP when configured, otherwise a simulated outbox stored in SQL."""
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import markdown

from .. import config
from ..db import EmailLog
from .base import Plugin, PluginContext, action, register

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _recipients(to) -> list[str]:
    if isinstance(to, str):
        to = re.split(r"[,;\s]+", to)
    return [t.strip() for t in (to or []) if t and t.strip()]


@register
class EmailPlugin(Plugin):
    name = "email"
    description = "Send email notifications (SMTP when configured; simulated outbox otherwise)."
    category = "integration"

    def mode(self) -> str:
        return "live" if config.SMTP_HOST else "simulated"

    @action("send", "Send an email (Markdown body is rendered to HTML).",
            inputs={"to": "recipient(s), comma separated or list", "subject": "subject line",
                    "body": "Markdown body"},
            outputs={"status": "sent|simulated", "to": "list", "email_id": "int"},
            required=("to", "body"), side_effect=True)
    def send(self, ctx: PluginContext, to=None, subject="Workflow notification", body="") -> dict:
        recipients = _recipients(to)
        if not recipients:
            raise ValueError("email.send requires at least one recipient")
        bad = [r for r in recipients if not EMAIL_RE.match(r)]
        if bad:
            raise ValueError(f"invalid email address(es): {', '.join(bad)}")
        status, error = "simulated", None
        if config.SMTP_HOST:
            msg = MIMEMultipart("alternative")
            msg["Subject"], msg["From"], msg["To"] = subject, config.SMTP_FROM, ", ".join(recipients)
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(markdown.markdown(body, extensions=["tables"]), "html"))
            try:
                with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
                    s.starttls()
                    if config.SMTP_USER:
                        s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                    s.sendmail(config.SMTP_FROM, recipients, msg.as_string())
                status = "sent"
            except Exception as e:  # noqa: BLE001
                status, error = "failed", str(e)
        log = EmailLog(to=", ".join(recipients), subject=subject, body=body, status=status,
                       error=error, run_id=ctx.run_id)
        ctx.db.add(log)
        ctx.db.commit()
        if status == "failed":
            raise RuntimeError(f"SMTP send failed: {error}")
        return {"status": status, "to": recipients, "email_id": log.id}
