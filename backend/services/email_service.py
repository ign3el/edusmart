"""
Email service for sending verification and password reset emails.
Supports both SMTP and SendGrid for flexibility.
"""
import os
import logging
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

logger = logging.getLogger(__name__)

# Email configuration from environment (uses your existing MAIL_* variables)
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "smtp")  # console, smtp, sendgrid
SMTP_HOST = os.getenv("MAIL_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("MAIL_PORT", "587"))
SMTP_USER = os.getenv("MAIL_USERNAME", "")
SMTP_PASSWORD = os.getenv("MAIL_PASSWORD", "")
FROM_EMAIL = os.getenv("MAIL_FROM", "EduSmart <noreply@edusmart.com>")
# Use APP_URL for production, fallback to FRONTEND_URL for dev
FRONTEND_URL = os.getenv("APP_URL") or os.getenv("FRONTEND_URL", "http://localhost:5173")
MAIL_STARTTLS = os.getenv("MAIL_STARTTLS", "True").lower() == "true"

# Recipients on these domains are logged and dropped, never handed to SMTP.
# Test scripts that exercise the real signup endpoint would otherwise make the
# production mail account send genuine messages - which is exactly how three
# probe emails ended up in the account holder's Sent folder on 2026-07-26.
# EMAIL_BACKEND=console cannot cover this: it is all-or-nothing and would
# silence real users' mail too.
MAIL_SUPPRESS_DOMAINS = {
    d.strip().lower()
    for d in os.getenv("MAIL_SUPPRESS_DOMAINS", "probe.edusmart.internal").split(",")
    if d.strip()
}

# Log configuration on startup
logger.info(f"Email Service Configuration:")
logger.info(f"  Backend: {EMAIL_BACKEND}")
logger.info(f"  SMTP Host: {SMTP_HOST}:{SMTP_PORT}")
logger.info(f"  From Email: {FROM_EMAIL}")
logger.info(f"  Frontend URL: {FRONTEND_URL}")
logger.info(f"  STARTTLS: {MAIL_STARTTLS}")

def send_email(to_email: str, subject: str, html_content: str, text_content: Optional[str] = None) -> bool:
    """
    Send an email using configured backend (console, SMTP, or SendGrid).
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        html_content: HTML email body
        text_content: Plain text fallback (optional)
    
    Returns:
        True if email sent successfully, False otherwise
    """
    # Hard stop before any backend is chosen: a suppressed domain must never
    # reach a real relay, whatever EMAIL_BACKEND says.
    if to_email.rsplit("@", 1)[-1].strip().lower() in MAIL_SUPPRESS_DOMAINS:
        logger.info(f"✉️  Suppressed (test domain), not sent: {to_email} | {subject}")
        return True

    if EMAIL_BACKEND == "console":
        logger.info(f"""
{'='*80}
📧 EMAIL (Console Mode - Not Actually Sent)
{'='*80}
To: {to_email}
Subject: {subject}

{text_content or 'HTML content only'}

HTML Preview:
{html_content[:500]}{'...' if len(html_content) > 500 else ''}
{'='*80}
""")
        return True
    
    elif EMAIL_BACKEND == "smtp":
        return _send_smtp_email(to_email, subject, html_content, text_content)
    
    elif EMAIL_BACKEND == "sendgrid":
        return _send_sendgrid_email(to_email, subject, html_content, text_content)
    
    else:
        logger.error(f"Unknown email backend: {EMAIL_BACKEND}")
        return False

def _send_smtp_email(to_email: str, subject: str, html_content: str, text_content: Optional[str]) -> bool:
    """Send email via SMTP"""
    try:
        if not SMTP_USER or not SMTP_PASSWORD:
            logger.error("SMTP credentials not configured. Set MAIL_USERNAME and MAIL_PASSWORD in .env")
            return False
        
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = FROM_EMAIL
        msg['To'] = to_email
        
        # NOTE: Gmail SMTP overrides the FROM address with the authenticated user's email
        # To use a custom FROM address (like edusmart@ign3el.com):
        # 1. Add the custom email as an alias in Gmail Settings > Accounts > "Send mail as"
        # 2. OR use a different SMTP provider (SendGrid, AWS SES, Mailgun, etc.)
        # 3. OR use your domain's SMTP server if available
        
        # Attach plain text and HTML
        if text_content:
            part1 = MIMEText(text_content, 'plain')
            msg.attach(part1)
        
        part2 = MIMEText(html_content, 'html')
        msg.attach(part2)
        
        # Send via SMTP
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"✅ Email sent to {to_email} via SMTP")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email via SMTP: {e}")
        return False

def _send_sendgrid_email(to_email: str, subject: str, html_content: str, text_content: Optional[str]) -> bool:
    """Send email via SendGrid API"""
    try:
        import sendgrid  # type: ignore
        from sendgrid.helpers.mail import Mail, Email, To, Content  # type: ignore
        
        sg_api_key = os.getenv("SENDGRID_API_KEY")
        if not sg_api_key:
            logger.error("SENDGRID_API_KEY not configured in .env")
            return False
        
        sg = sendgrid.SendGridAPIClient(api_key=sg_api_key)
        from_email = Email(FROM_EMAIL)
        to_email_obj = To(to_email)
        content = Content("text/html", html_content)
        
        mail = Mail(from_email, to_email_obj, subject, content)
        response = sg.client.mail.send.post(request_body=mail.get())
        
        if response.status_code in [200, 201, 202]:
            logger.info(f"✅ Email sent to {to_email} via SendGrid")
            return True
        else:
            logger.error(f"SendGrid error: {response.status_code} - {response.body}")
            return False
            
    except ImportError:
        logger.error("sendgrid package not installed. Run: pip install sendgrid")
        return False
    except Exception as e:
        logger.error(f"Failed to send email via SendGrid: {e}")
        return False

def send_verification_email(email: str, token: str) -> bool:
    """Send email verification link"""
    verification_url = f"{FRONTEND_URL}/verify-email?token={token}"
    
    subject = "EduSmart - Verify Your Email Address"
    
    text_content = f"""
Welcome to EduSmart!

Please verify your email address by clicking the link below:

{verification_url}

This link will expire in 24 hours.

If you didn't create an account, please ignore this email.

Best regards,
The EduSmart Team
"""
    
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
        .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
        .button {{ display: inline-block; padding: 15px 30px; background: #667eea; color: white; text-decoration: none; border-radius: 5px; margin: 20px 0; }}
        .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #999; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Welcome to EduSmart! 🎓</h1>
        </div>
        <div class="content">
            <p>Hi there!</p>
            <p>Thank you for signing up for EduSmart. We're excited to have you on board!</p>
            <p>Please verify your email address by clicking the button below:</p>
            <p style="text-align: center;">
                <a href="{verification_url}" class="button">Verify Email Address</a>
            </p>
            <p>Or copy and paste this link into your browser:</p>
            <p style="word-break: break-all; background: #e9e9e9; padding: 10px; border-radius: 5px;">
                {verification_url}
            </p>
            <p><strong>This link will expire in 24 hours.</strong></p>
            <p>If you didn't create an account, please ignore this email.</p>
        </div>
        <div class="footer">
            <p>&copy; 2026 EduSmart. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
    
    return send_email(email, subject, html_content, text_content)

def send_password_reset_email(email: str, token: str) -> bool:
    """Send password reset link"""
    reset_url = f"{FRONTEND_URL}/reset-password?token={token}"
    
    subject = "EduSmart - Password Reset Request"
    
    text_content = f"""
Password Reset Request

We received a request to reset your password for your EduSmart account.

Click the link below to reset your password:

{reset_url}

This link will expire in 1 hour.

If you didn't request a password reset, please ignore this email or contact support if you have concerns.

Best regards,
The EduSmart Team
"""
    
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
        .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
        .button {{ display: inline-block; padding: 15px 30px; background: #667eea; color: white; text-decoration: none; border-radius: 5px; margin: 20px 0; }}
        .warning {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 10px; margin: 20px 0; }}
        .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #999; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Password Reset Request 🔐</h1>
        </div>
        <div class="content">
            <p>Hi there!</p>
            <p>We received a request to reset the password for your EduSmart account.</p>
            <p>Click the button below to reset your password:</p>
            <p style="text-align: center;">
                <a href="{reset_url}" class="button">Reset Password</a>
            </p>
            <p>Or copy and paste this link into your browser:</p>
            <p style="word-break: break-all; background: #e9e9e9; padding: 10px; border-radius: 5px;">
                {reset_url}
            </p>
            <p><strong>This link will expire in 1 hour.</strong></p>
            <div class="warning">
                <strong>⚠️ Security Notice:</strong> If you didn't request a password reset, please ignore this email. Your password will remain unchanged.
            </div>
        </div>
        <div class="footer">
            <p>&copy; 2026 EduSmart. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
    
    return send_email(email, subject, html_content, text_content)
