"""Shared SMTP email helper — reads config from AppSetting at call time."""
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_email(to_addr, subject, body_text, body_html=None):
    """Send an email using the stored SMTP configuration.

    Returns (True, None) on success or (False, error_message) on failure.
    Reads SMTP settings fresh from the database on every call so that
    config changes take effect without restarting the app.
    """
    # Import here to avoid circular imports at module load time
    from .models import AppSetting

    host       = AppSetting.get('smtp_host')
    port_str   = AppSetting.get('smtp_port', '587')
    encryption = AppSetting.get('smtp_encryption', 'tls')
    username   = AppSetting.get('smtp_username')
    password   = AppSetting.get('smtp_password')
    from_name  = AppSetting.get('smtp_from_name', 'LYC Jr Sailing')

    if not host or not username or not password:
        missing = [label for label, val in [('Host', host), ('Username', username), ('Password', password)] if not val]
        return False, f'SMTP not fully configured — missing: {", ".join(missing)}.'

    try:
        port = int(port_str)
    except (ValueError, TypeError):
        return False, f'Invalid SMTP port "{port_str}".'

    # Build message
    if body_html:
        msg = MIMEMultipart('alternative')
        msg.attach(MIMEText(body_text, 'plain'))
        msg.attach(MIMEText(body_html, 'html'))
    else:
        msg = MIMEText(body_text, 'plain')

    msg['Subject'] = subject
    msg['From']    = f'{from_name} <{username}>'
    msg['To']      = to_addr

    try:
        if encryption == 'ssl':
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=10) as server:
                server.login(username, password)
                server.sendmail(username, to_addr, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=10) as server:
                if encryption == 'tls':
                    server.starttls(context=ssl.create_default_context())
                server.login(username, password)
                server.sendmail(username, to_addr, msg.as_string())
        return True, None

    except smtplib.SMTPAuthenticationError:
        return False, 'SMTP authentication failed — check your username and password.'
    except smtplib.SMTPConnectError:
        return False, f'Could not connect to {host}:{port}.'
    except smtplib.SMTPServerDisconnected:
        return False, 'Server disconnected unexpectedly.'
    except smtplib.SMTPSenderRefused:
        return False, f'Server refused the sender address ({username}).'
    except smtplib.SMTPRecipientsRefused:
        return False, f'Server refused the recipient address ({to_addr}).'
    except smtplib.SMTPException as e:
        return False, f'SMTP error: {e}'
    except OSError as e:
        msg_str = str(e)
        if 'Name or service not known' in msg_str or 'getaddrinfo failed' in msg_str:
            return False, f'Could not resolve host "{host}". Check the hostname.'
        if 'Connection refused' in msg_str or 'Errno 111' in msg_str:
            return False, f'Connection refused on port {port}. Check the port and encryption setting.'
        if 'Network is unreachable' in msg_str or 'Errno 101' in msg_str:
            return False, 'Network is unreachable — outbound SMTP may be blocked by your hosting provider.'
        if 'timed out' in msg_str.lower():
            return False, f'Connection timed out — port {port} may be blocked or the host is not responding.'
        return False, f'Network error: {e}'
    except Exception as e:
        return False, f'Unexpected error: {e}'
