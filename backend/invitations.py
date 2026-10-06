"""Email-bound invitations and optional Gmail delivery, without a paid provider."""
import hashlib
import os
import smtplib
import ssl
from email.message import EmailMessage


def invitation_digest(token, email):
    # Domain separation invalidates the old generic invitation codes.
    return hashlib.sha256(f'email-invite:{email}:{token}'.encode()).hexdigest()


def email_configured():
    return bool(os.getenv('SMTP_GMAIL_USER') and os.getenv('SMTP_GMAIL_APP_PASSWORD'))


def send_invitation(email, link):
    if not email_configured():
        return 'not_configured'
    message = EmailMessage()
    message['From'] = os.environ['SMTP_GMAIL_USER']
    message['To'] = email
    message['Subject'] = 'Your invitation to Product Image Finder'
    message.set_content(
        'Siddique has invited you to your own private Product Image Finder workspace.\n\n'
        f'Create your account here:\n{link}\n\n'
        f'This link is only for {email}, works once, and expires in seven days. '
        'You will choose your own password. Your catalogue stays private.\n\n'
        'If you did not expect this invitation, you can ignore this email.'
    )
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15, context=ssl.create_default_context()) as smtp:
            smtp.login(os.environ['SMTP_GMAIL_USER'], os.environ['SMTP_GMAIL_APP_PASSWORD'].replace(' ', ''))
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException):
        # Do not expose provider responses or credentials to the browser/logs.
        return 'failed'
    return 'sent'
