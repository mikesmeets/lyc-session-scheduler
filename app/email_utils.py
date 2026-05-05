"""Shared SMTP email helper — reads config from AppSetting at call time."""
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _smtp_config():
    from .models import AppSetting
    return {
        'host':       AppSetting.get('smtp_host'),
        'port':       AppSetting.get('smtp_port', '587'),
        'encryption': AppSetting.get('smtp_encryption', 'tls'),
        'username':   AppSetting.get('smtp_username'),
        'password':   AppSetting.get('smtp_password'),
        'from_name':  AppSetting.get('smtp_from_name', 'LYC Jr Sailing'),
    }


def _send_raw(cfg, all_recipients, msg_str):
    """Open SMTP connection and send.  Returns None on success, error str on failure."""
    port = int(cfg['port'])
    try:
        if cfg['encryption'] == 'ssl':
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg['host'], port, context=ctx, timeout=10) as srv:
                srv.login(cfg['username'], cfg['password'])
                srv.sendmail(cfg['username'], all_recipients, msg_str)
        else:
            with smtplib.SMTP(cfg['host'], port, timeout=10) as srv:
                if cfg['encryption'] == 'tls':
                    srv.starttls(context=ssl.create_default_context())
                srv.login(cfg['username'], cfg['password'])
                srv.sendmail(cfg['username'], all_recipients, msg_str)
        return None
    except smtplib.SMTPAuthenticationError:
        return 'SMTP authentication failed — check your username and password.'
    except smtplib.SMTPConnectError:
        return f"Could not connect to {cfg['host']}:{port}."
    except smtplib.SMTPServerDisconnected:
        return 'Server disconnected unexpectedly.'
    except smtplib.SMTPSenderRefused:
        return f"Server refused the sender address ({cfg['username']})."
    except smtplib.SMTPRecipientsRefused:
        return 'Server refused one or more recipient addresses.'
    except smtplib.SMTPException as e:
        return f'SMTP error: {e}'
    except OSError as e:
        s = str(e)
        if 'Name or service not known' in s or 'getaddrinfo failed' in s:
            return f"Could not resolve host \"{cfg['host']}\"."
        if 'Connection refused' in s or 'Errno 111' in s:
            return f"Connection refused on port {port}."
        if 'Network is unreachable' in s or 'Errno 101' in s:
            return 'Network is unreachable — outbound SMTP may be blocked.'
        if 'timed out' in s.lower():
            return f'Connection timed out on port {port}.'
        return f'Network error: {e}'
    except Exception as e:
        return f'Unexpected error: {e}'


def send_email(to_addr, subject, body_text, body_html=None):
    """Send a single email. Returns (True, None) or (False, error_message)."""
    from .models import EmailLog
    from . import db

    cfg = _smtp_config()

    def _log(success, error_msg=None):
        try:
            db.session.add(EmailLog(to_addr=to_addr, subject=subject,
                                    success=success, error_msg=error_msg))
            db.session.commit()
        except Exception:
            pass

    if not cfg['host'] or not cfg['username'] or not cfg['password']:
        missing = [k for k in ('host', 'username', 'password') if not cfg[k]]
        err = f'SMTP not fully configured — missing: {", ".join(missing)}.'
        _log(False, err)
        return False, err

    try:
        int(cfg['port'])
    except (ValueError, TypeError):
        err = f"Invalid SMTP port \"{cfg['port']}\"."
        _log(False, err)
        return False, err

    if body_html:
        msg = MIMEMultipart('alternative')
        msg.attach(MIMEText(body_text, 'plain'))
        msg.attach(MIMEText(body_html, 'html'))
    else:
        msg = MIMEText(body_text, 'plain')

    msg['Subject'] = subject
    msg['From']    = f"{cfg['from_name']} <{cfg['username']}>"
    msg['To']      = to_addr

    err = _send_raw(cfg, [to_addr], msg.as_string())
    if err is None:
        _log(True)
        return True, None
    _log(False, err)
    return False, err


def send_email_multi(to_addrs, bcc_addrs, subject, body_text, body_html=None):
    """Send one email to multiple recipients.

    to_addrs  — list of visible To: addresses
    bcc_addrs — list of BCC addresses (omitted from headers for privacy)

    Returns (True, None) or (False, error_message).
    Logs a single EmailLog entry.
    """
    from .models import EmailLog
    from . import db

    to_addrs  = [a for a in (to_addrs  or []) if a]
    bcc_addrs = [a for a in (bcc_addrs or []) if a and a not in to_addrs]
    all_recipients = to_addrs + bcc_addrs

    if not all_recipients:
        return False, 'No recipients specified.'

    if len(all_recipients) == 1:
        log_to = all_recipients[0]
    else:
        log_to = f'{all_recipients[0]} + {len(all_recipients) - 1} more'

    cfg = _smtp_config()

    def _log(success, error_msg=None):
        try:
            db.session.add(EmailLog(to_addr=log_to, subject=subject,
                                    success=success, error_msg=error_msg))
            db.session.commit()
        except Exception:
            pass

    if not cfg['host'] or not cfg['username'] or not cfg['password']:
        missing = [k for k in ('host', 'username', 'password') if not cfg[k]]
        err = f'SMTP not fully configured — missing: {", ".join(missing)}.'
        _log(False, err)
        return False, err

    try:
        int(cfg['port'])
    except (ValueError, TypeError):
        err = f"Invalid SMTP port \"{cfg['port']}\"."
        _log(False, err)
        return False, err

    if body_html:
        msg = MIMEMultipart('alternative')
        msg.attach(MIMEText(body_text, 'plain'))
        msg.attach(MIMEText(body_html, 'html'))
    else:
        msg = MIMEText(body_text, 'plain')

    msg['Subject'] = subject
    msg['From']    = f"{cfg['from_name']} <{cfg['username']}>"
    msg['To']      = ', '.join(to_addrs) if to_addrs else cfg['username']
    # BCC addresses are intentionally NOT added to headers

    err = _send_raw(cfg, all_recipients, msg.as_string())
    if err is None:
        _log(True)
        return True, None
    _log(False, err)
    return False, err
