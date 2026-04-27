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
    Every attempt (success or failure) is written to EmailLog.
    """
    from .models import AppSetting, EmailLog
    from . import db

    host       = AppSetting.get('smtp_host')
    port_str   = AppSetting.get('smtp_port', '587')
    encryption = AppSetting.get('smtp_encryption', 'tls')
    username   = AppSetting.get('smtp_username')
    password   = AppSetting.get('smtp_password')
    from_name  = AppSetting.get('smtp_from_name', 'LYC Jr Sailing')

    def _log(success, error_msg=None):
        try:
            db.session.add(EmailLog(
                to_addr   = to_addr,
                subject   = subject,
                success   = success,
                error_msg = error_msg,
            ))
            db.session.commit()
        except Exception:
            pass  # Never let logging break the calling code

    if not host or not username or not password:
        missing = [label for label, val in [('Host', host), ('Username', username), ('Password', password)] if not val]
        err = f'SMTP not fully configured — missing: {", ".join(missing)}.'
        _log(False, err)
        return False, err

    try:
        port = int(port_str)
    except (ValueError, TypeError):
        err = f'Invalid SMTP port "{port_str}".'
        _log(False, err)
        return False, err

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
        _log(True)
        return True, None

    except smtplib.SMTPAuthenticationError:
        err = 'SMTP authentication failed — check your username and password.'
    except smtplib.SMTPConnectError:
        err = f'Could not connect to {host}:{port}.'
    except smtplib.SMTPServerDisconnected:
        err = 'Server disconnected unexpectedly.'
    except smtplib.SMTPSenderRefused:
        err = f'Server refused the sender address ({username}).'
    except smtplib.SMTPRecipientsRefused:
        err = f'Server refused the recipient address ({to_addr}).'
    except smtplib.SMTPException as e:
        err = f'SMTP error: {e}'
    except OSError as e:
        msg_str = str(e)
        if 'Name or service not known' in msg_str or 'getaddrinfo failed' in msg_str:
            err = f'Could not resolve host "{host}". Check the hostname.'
        elif 'Connection refused' in msg_str or 'Errno 111' in msg_str:
            err = f'Connection refused on port {port}. Check the port and encryption setting.'
        elif 'Network is unreachable' in msg_str or 'Errno 101' in msg_str:
            err = 'Network is unreachable — outbound SMTP may be blocked by your hosting provider.'
        elif 'timed out' in msg_str.lower():
            err = f'Connection timed out — port {port} may be blocked or the host is not responding.'
        else:
            err = f'Network error: {e}'
    except Exception as e:
        err = f'Unexpected error: {e}'

    _log(False, err)
    return False, err
