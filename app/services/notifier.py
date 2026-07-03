"""
app/services/notifier.py — outbound email notifications.

Purpose
-------
When someone files a "Report a Concern" from the dashboard, notify the fixed
list of programme owners so the issue is picked up quickly. Everything else
in the app (data ingestion, sync, dashboards) is read-only from a comms
standpoint — this module is intentionally small and single-purpose.

Design
------
* **Fail-soft.** If SMTP env vars aren't configured, or the send raises, the
  caller's HTTP request must still succeed. The report is already persisted
  to Postgres — email is a secondary channel. We log a warning and move on.
* **Blocking send in a background task.** SMTP is I/O with a variable ~1 s
  latency. Callers wrap this in ``BackgroundTasks`` so the POST returns
  immediately.
* **Recipient list is baked in.** The default TO addresses live in this
  module. Env var ``REPORT_RECIPIENTS`` (comma-separated) overrides for staging
  / testing, but production intentionally uses the code-owned list so a stray
  env change can't silently redirect concerns.

Environment (all optional — missing → no-op)
--------------------------------------------
* ``SMTP_HOST``          — e.g. ``smtp.office365.com`` / ``email-smtp.us-east-1.amazonaws.com``
* ``SMTP_PORT``          — default ``587``
* ``SMTP_USER``          — SMTP auth username
* ``SMTP_PASSWORD``      — SMTP auth password
* ``SMTP_FROM``          — the From: header; defaults to ``SMTP_USER``
* ``SMTP_USE_TLS``       — ``"true"`` (default) or ``"false"``
* ``REPORT_RECIPIENTS``  — comma-separated override for the default recipients
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# Default programme-owner recipients. Only override via REPORT_RECIPIENTS
# for staging / drills — production ships with this list.
DEFAULT_REPORT_RECIPIENTS: tuple[str, ...] = (
    "gushie@sightsavers.org",
    "cnwosu@sightsavers.org",
    "benjamin.shaibu@ehealthnigeria.org",
    "godsgift.olomu@ehealthnigeria.org",
    "fashoto.busayo@ehealthnigeria.org",
    "abubakar.abdulkareem@ehealthnigeria.org",
)


def _env(k: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(k)
    return v if v not in (None, "") else default


def get_report_recipients() -> list[str]:
    """Resolve the effective recipient list.

    Falls back to the code-owned default when REPORT_RECIPIENTS is unset.
    """
    override = _env("REPORT_RECIPIENTS")
    if override:
        return [addr.strip() for addr in override.split(",") if addr.strip()]
    return list(DEFAULT_REPORT_RECIPIENTS)


def smtp_configured() -> bool:
    """Return True iff we have at minimum a host to talk to.

    A missing host means "email is disabled in this environment" — the caller
    should not treat that as an error.
    """
    return _env("SMTP_HOST") is not None


def _send(msg: EmailMessage) -> None:
    """Deliver a fully-formed EmailMessage via SMTP.

    Wraps SMTP handshake + STARTTLS + login. Raises on any protocol error;
    the caller in ``notify_new_report`` swallows and logs so the HTTP path
    stays healthy even when the mail server is having a bad day.
    """
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "587") or 587)
    user = _env("SMTP_USER")
    pw   = _env("SMTP_PASSWORD")
    use_tls = (_env("SMTP_USE_TLS", "true") or "true").lower() == "true"

    if not host:
        raise RuntimeError("SMTP_HOST is not configured")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=15) as s:
        s.ehlo()
        if use_tls:
            s.starttls(context=ctx)
            s.ehlo()
        if user and pw:
            s.login(user, pw)
        s.send_message(msg)


def notify_new_report(report: dict) -> None:
    """Send an email announcing a new user report.

    Called from FastAPI ``BackgroundTasks`` after the POST /api/reports insert
    commits. Never raises — logs on failure so the request path is unaffected.

    ``report`` is the row dict returned by the RETURNING clause of the insert
    (id, category, subject, message, reporter_email, reporter_name,
    reporter_role, page_url, status, created_at).
    """
    if not smtp_configured():
        logger.info(
            "Report %s created (no SMTP configured — email notification skipped)",
            report.get("id"),
        )
        return

    recipients = get_report_recipients()
    if not recipients:
        logger.warning("No REPORT_RECIPIENTS configured — email skipped")
        return

    try:
        subject_line = (
            f"[SARMAAN MDA Dashboard] New {report.get('category', 'general')}"
            f" report #{report.get('id')}"
        )
        if report.get("subject"):
            subject_line += f" — {report['subject']}"

        # Plain-text body — short and scannable. If SMTP relay strips HTML
        # or the recipients read on mobile, this format still reads well.
        role = report.get("reporter_role") or "(unknown role)"
        name = report.get("reporter_name")  or "(anonymous)"
        email = report.get("reporter_email") or "(none provided)"
        page  = report.get("page_url")       or "(not captured)"
        body = (
            "A new concern has been filed on the SARMAAN MDA dashboard.\n"
            "\n"
            f"Category   : {report.get('category', 'general')}\n"
            f"Subject    : {report.get('subject') or '(none)'}\n"
            f"Filed by   : {name} <{email}>\n"
            f"Role       : {role}\n"
            f"Page       : {page}\n"
            f"When       : {report.get('created_at')}\n"
            f"Report ID  : {report.get('id')}\n"
            "\n"
            "----- MESSAGE -----\n"
            f"{report.get('message', '').strip()}\n"
            "-------------------\n"
            "\n"
            "You can view all reports via the admin portal or the /api/reports API.\n"
        )

        msg = EmailMessage()
        msg["Subject"] = subject_line
        msg["From"]    = _env("SMTP_FROM") or _env("SMTP_USER") or "noreply@sarmaan.local"
        msg["To"]      = ", ".join(recipients)
        msg.set_content(body)

        _send(msg)
        logger.info(
            "Report %s emailed to %d recipient(s)",
            report.get("id"),
            len(recipients),
        )
    except Exception as e:  # noqa: BLE001 — email is best-effort
        logger.warning("Report %s email FAILED: %s", report.get("id"), e)
