"""Outgoing email over SMTP (stdlib only)."""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from html import escape

from app.config import get_settings

log = logging.getLogger(__name__)


def mail_enabled() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.mail_from)


def send_email(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Send one email. Returns False (and logs) instead of raising, so callers never fail on mail."""
    s = get_settings()
    if not mail_enabled():
        log.warning("SMTP not configured; email '%s' to %s not sent", subject, to)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((s.mail_from_name, s.mail_from))
    msg["To"] = to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    try:
        context = ssl.create_default_context()
        if s.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds, context=context)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds)
        with server:
            if s.smtp_security == "starttls":
                server.starttls(context=context)
            if s.smtp_username:
                server.login(s.smtp_username, s.smtp_password or "")
            server.send_message(msg)
    except Exception:
        log.exception("Failed to send email '%s' to %s", subject, to)
        return False
    log.info("Sent email '%s' to %s", subject, to)
    return True


def send_psp_welcome_email(*, to: str, psp_name: str, psp_code: str, password: str) -> bool:
    """Email a new PSP its portal login and how to change the temporary password."""
    login_url = get_settings().portal_login_url
    subject = f"Your PSP Portal account is ready ({psp_code})"

    text = f"""Hello {psp_name},

Your account on the PSP Portal has been created. Use the details below to sign in.

  Portal:    {login_url}
  PSP code:  {psp_code}
  Email:     {to}
  Password:  {password}

This password was set by the admin. Please change it as soon as you sign in:

  1. Open {login_url} and sign in with the email and password above.
  2. Open your profile menu (top right) and choose "Change Password".
  3. Enter the current password from this email.
  4. Enter a new password (10 to 72 characters, different from the current one) and confirm it.
  5. Click "Update Password". You stay signed in here; any other open sessions are signed out.

Keep your password private. We will never ask you for it by email or phone.
If you did not expect this email, contact the portal admin.

PSP Portal Team
"""

    e = escape
    steps = [
        f'Open <a href="{e(login_url)}">{e(login_url)}</a> and sign in with the email and password above.',
        "Open your profile menu (top right) and choose <b>Change Password</b>.",
        "Enter the current password from this email.",
        "Enter a new password (10 to 72 characters, different from the current one) and confirm it.",
        "Click <b>Update Password</b>. You stay signed in here; any other open sessions are signed out.",
    ]
    rows = [("Portal", f'<a href="{e(login_url)}">{e(login_url)}</a>'), ("PSP code", e(psp_code)),
            ("Email", e(to)), ("Password", f"<code style=\"font-size:15px\">{e(password)}</code>")]
    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;color:#1f2937">
  <table role="presentation" width="100%" style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;padding:28px">
    <tr><td>
      <h2 style="margin:0 0 16px">Welcome to the PSP Portal</h2>
      <p>Hello {e(psp_name)},</p>
      <p>Your account on the PSP Portal has been created. Use the details below to sign in.</p>
      <table role="presentation" style="width:100%;border-collapse:collapse;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;margin:16px 0">
        {"".join(f'<tr><td style="padding:10px 14px;color:#6b7280;width:110px">{k}</td><td style="padding:10px 14px;font-weight:bold">{v}</td></tr>' for k, v in rows)}
      </table>
      <p style="text-align:center;margin:24px 0">
        <a href="{e(login_url)}" style="background:#2563eb;color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:6px;font-weight:bold">Sign in to the portal</a>
      </p>
      <h3 style="margin:24px 0 8px">Change your password</h3>
      <p style="margin:0 0 8px">This password was set by the admin. Please change it as soon as you sign in:</p>
      <ol style="padding-left:20px;line-height:1.6">{"".join(f"<li>{s}</li>" for s in steps)}</ol>
      <p style="background:#fef3c7;border-left:4px solid #f59e0b;padding:10px 14px;font-size:13px">
        Keep your password private. We will never ask you for it by email or phone.
        If you did not expect this email, contact the portal admin.
      </p>
      <p style="margin-top:24px">PSP Portal Team</p>
    </td></tr>
  </table>
</body></html>"""
    return send_email(to, subject, text, html)
