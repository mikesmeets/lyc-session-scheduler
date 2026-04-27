import secrets
import logging
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash

from ..models import User, PasswordResetToken
from .. import db
from ..email_utils import send_email

auth = Blueprint('auth', __name__)
log  = logging.getLogger(__name__)


# ── Login / Logout / Register ────────────────────────────────────────────────

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.index'))
        flash('Invalid email or password.', 'danger')
    return render_template('auth/login.html')


@auth.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        audit_number = request.form.get('audit_number', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not first_name or not last_name or not audit_number or not email or not password:
            flash('All fields are required.', 'danger')
        elif password != confirm:
            flash('Passwords do not match.', 'danger')
        elif User.query.filter_by(email=email).first():
            flash('An account with that email already exists.', 'danger')
        else:
            user = User(
                first_name=first_name,
                last_name=last_name,
                audit_number=audit_number,
                email=email,
                password_hash=generate_password_hash(password),
            )
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            flash(f'Welcome, {user.first_name}! Add your sailors to get started.', 'success')
            return redirect(url_for('main.my_sailors'))
    return render_template('auth/register.html')


@auth.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.index'))


# ── Password Reset ────────────────────────────────────────────────────────────

@auth.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user  = User.query.filter_by(email=email).first()

        if not user:
            flash('No account found with that email address.', 'danger')
            return render_template('auth/forgot_password.html', email=email)

        # Invalidate any existing unused tokens for this user
            PasswordResetToken.query.filter_by(user_id=user.id, used=False).delete()

            token_value = secrets.token_urlsafe(32)
            expires_at  = datetime.utcnow() + timedelta(hours=1)
            token = PasswordResetToken(
                user_id    = user.id,
                token      = token_value,
                expires_at = expires_at,
            )
            db.session.add(token)
            db.session.commit()

            reset_url = url_for('auth.reset_password', token=token_value, _external=True)

            body_text = (
                f"Hi {user.first_name},\n\n"
                "Someone requested a password reset for your LYC Jr Sailing account.\n\n"
                "Click the link below to set a new password (valid for 1 hour):\n\n"
                f"{reset_url}\n\n"
                "If you didn't request this, you can safely ignore this email.\n\n"
                "— LYC Jr Sailing"
            )
            body_html = f"""
<p>Hi {user.first_name},</p>
<p>Someone requested a password reset for your LYC Jr Sailing account.</p>
<p>
  <a href="{reset_url}" style="
    display:inline-block;
    padding:10px 20px;
    background:#0d6efd;
    color:#fff;
    text-decoration:none;
    border-radius:5px;
    font-weight:bold;
  ">Reset My Password</a>
</p>
<p>Or copy this link into your browser:<br>
   <a href="{reset_url}">{reset_url}</a></p>
<p><small>This link expires in 1&nbsp;hour.
If you didn't request this, you can safely ignore this email.</small></p>
<p>— LYC Jr Sailing</p>
"""
            ok, err = send_email(
                to_addr   = user.email,
                subject   = 'LYC Jr Sailing — Reset Your Password',
                body_text = body_text,
                body_html = body_html,
            )
            if not ok:
                log.error('Password reset email failed for %s: %s', user.email, err)

        flash(
            "If that email is registered you'll receive a reset link shortly. "
            "Check your spam folder if it doesn't arrive within a few minutes.",
            'info'
        )
        return redirect(url_for('auth.login'))

    return render_template('auth/forgot_password.html')


@auth.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    record = PasswordResetToken.query.filter_by(token=token, used=False).first()

    if not record or record.expires_at < datetime.utcnow():
        flash('That reset link is invalid or has expired. Please request a new one.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return render_template('auth/reset_password.html', token=token)
        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('auth/reset_password.html', token=token)

        record.user.set_password(password)
        record.used = True
        db.session.commit()

        flash('Your password has been updated. You can now log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', token=token)
