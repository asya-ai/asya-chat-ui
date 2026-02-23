import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def _smtp_port() -> int | None:
    try:
        return int(settings.smtp_port)
    except (TypeError, ValueError):
        return None


def _smtp_configured() -> bool:
    return bool(settings.smtp_host and _smtp_port() and (settings.smtp_email or settings.smtp_user))


def send_invite_email(*, to_email: str, org_name: str, invite_url: str) -> None:
    if not _smtp_configured():
        logger.info("SMTP not configured; skipping invite email.")
        return
    port = _smtp_port()
    if not port:
        logger.info("SMTP port missing/invalid; skipping invite email.")
        return
    sender = settings.smtp_email or settings.smtp_user
    message = EmailMessage()
    message["Subject"] = f"You're invited to {org_name}"
    message["From"] = sender
    message["To"] = to_email
    message.set_content(
        "\n".join(
            [
                f"You have been invited to join {org_name}.",
                "",
                f"Accept your invite: {invite_url}",
                "",
                "If you did not expect this email, you can ignore it.",
            ]
        )
    )
    try:
        if port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, port) as smtp:
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(message)
            return
        with smtplib.SMTP(settings.smtp_host, port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except Exception as exc:
        logger.exception("Failed to send invite email: %s", exc)
        raise


def send_password_reset_email(*, to_email: str, reset_url: str) -> None:
    if not _smtp_configured():
        logger.info("SMTP not configured; skipping password reset email.")
        return
    port = _smtp_port()
    if not port:
        logger.info("SMTP port missing/invalid; skipping password reset email.")
        return
    sender = settings.smtp_email or settings.smtp_user
    message = EmailMessage()
    message["Subject"] = "Password reset"
    message["From"] = sender
    message["To"] = to_email
    message.set_content(
        "\n".join(
            [
                "We received a request to reset your password.",
                "",
                f"Reset your password: {reset_url}",
                "",
                "If you did not request this, you can ignore this email.",
            ]
        )
    )
    try:
        if port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, port) as smtp:
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(message)
            return
        with smtplib.SMTP(settings.smtp_host, port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except Exception as exc:
        logger.exception("Failed to send password reset email: %s", exc)
        raise
