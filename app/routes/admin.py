import smtplib
import ssl
import socket
from email.mime.text import MIMEText
from datetime import date, datetime, timedelta
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from ..models import Fleet, Session, Signup, Sailor, User, AppSetting, WaiverLink, EmailLog
from .. import db
from ..email_utils import send_email

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Admin access required.', 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/')
@login_required
@admin_required
def dashboard():
    today = date.today()
    upcoming = Session.query.filter(
        Session.date >= today, Session.status != 'cancelled'
    ).order_by(Session.date).all()
    fleets = Fleet.query.order_by(Fleet.name).all()
    total_sailors = Sailor.query.count()
    total_parents = User.query.filter_by(is_admin=False).count()
    return render_template('admin/dashboard.html',
        upcoming=upcoming,
        fleets=fleets,
        total_sailors=total_sailors,
        total_parents=total_parents,
        today=today,
    )


# ── Fleets ──────────────────────────────────────────────────────────────────

@admin_bp.route('/fleets', methods=['GET', 'POST'])
@login_required
@admin_required
def fleets():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Fleet name is required.', 'danger')
        elif Fleet.query.filter_by(name=name).first():
            flash('A fleet with that name already exists.', 'danger')
        else:
            color = request.form.get('color', '#ffffff').strip()
            db.session.add(Fleet(name=name, color=color))
            db.session.commit()
            flash(f'Fleet "{name}" created.', 'success')
        return redirect(url_for('admin.fleets'))

    return render_template('admin/fleets.html', fleets=Fleet.query.order_by(Fleet.name).all())


@admin_bp.route('/fleets/<int:fleet_id>/color', methods=['POST'])
@login_required
@admin_required
def update_fleet_color(fleet_id):
    fleet = Fleet.query.get_or_404(fleet_id)
    color = request.form.get('color', '#ffffff').strip()
    if len(color) == 7 and color.startswith('#'):
        fleet.color = color
        db.session.commit()
    return redirect(url_for('admin.fleets'))


@admin_bp.route('/fleets/<int:fleet_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_fleet(fleet_id):
    fleet = Fleet.query.get_or_404(fleet_id)
    if fleet.sessions or fleet.sailors:
        flash('Cannot delete a fleet that has sessions or registered sailors.', 'danger')
    else:
        db.session.delete(fleet)
        db.session.commit()
        flash(f'Fleet "{fleet.name}" deleted.', 'success')
    return redirect(url_for('admin.fleets'))


# ── Sessions ─────────────────────────────────────────────────────────────────

@admin_bp.route('/sessions')
@login_required
@admin_required
def sessions():
    fleet_id = request.args.get('fleet_id', type=int)
    query = Session.query.order_by(Session.date.desc())
    if fleet_id:
        query = query.filter_by(fleet_id=fleet_id)
    return render_template('admin/sessions.html',
        sessions=query.all(),
        fleets=Fleet.query.order_by(Fleet.name).all(),
        selected_fleet=fleet_id,
        today=date.today(),
    )


def _session_from_form(session=None, require_date=True):
    """Parse session form fields; returns (session_obj, error_msg)."""
    fleet_id = request.form.get('fleet_id', type=int)
    date_str = request.form.get('date', '')
    session_type = request.form.get('session_type', 'morning')
    start_time = request.form.get('start_time', '').strip() or None
    end_time = request.form.get('end_time', '').strip() or None
    notes = request.form.get('notes', '').strip() or None
    deadline_days = request.form.get('commitment_deadline_days', type=int) or 14
    min_sailors = request.form.get('min_sailors', type=int) or 5

    if not fleet_id:
        return None, 'Fleet is required.'
    if require_date and not date_str:
        return None, 'Date is required.'
    if session_type not in ('morning', 'afternoon', 'full_day'):
        return None, 'Invalid session type.'

    session_date = None
    if date_str:
        try:
            session_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return None, 'Invalid date format.'

    if session is None:
        session = Session()
    session.fleet_id = fleet_id
    if session_date is not None:
        session.date = session_date
    session.session_type = session_type
    session.start_time = start_time
    session.end_time = end_time
    session.notes = notes
    session.commitment_deadline_days = deadline_days
    session.min_sailors = min_sailors
    return session, None


@admin_bp.route('/sessions/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_session():
    fleets = Fleet.query.order_by(Fleet.name).all()
    if request.method == 'POST':
        recurring = request.form.get('recurring') == '1'
        recurring_mode = request.form.get('recurring_mode', 'interval')
        # In "Pick Specific Dates" mode the date field is hidden, so don't require it
        require_date = not (recurring and recurring_mode == 'dates')

        session, error = _session_from_form(require_date=require_date)
        if error:
            flash(error, 'danger')
            return render_template('admin/new_session.html', fleets=fleets)

        if recurring:
            if recurring_mode == 'interval':
                # Need a start date from the (hidden) single date field
                date_str = request.form.get('date', '').strip()
                if not date_str:
                    flash('Please enter a start date for the recurring sessions.', 'danger')
                    return render_template('admin/new_session.html', fleets=fleets)
                try:
                    start_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    flash('Invalid start date.', 'danger')
                    return render_template('admin/new_session.html', fleets=fleets)
                repeat_weeks = request.form.get('repeat_weeks', type=int) or 1
                num_occurrences = request.form.get('num_occurrences', type=int) or 4
                num_occurrences = max(2, min(52, num_occurrences))
                sessions = []
                for i in range(num_occurrences):
                    s, _ = _session_from_form(require_date=False)
                    s.date = start_date + timedelta(weeks=repeat_weeks * i)
                    sessions.append(s)
                db.session.add_all(sessions)
                db.session.commit()
                flash(f'{len(sessions)} session(s) created.', 'success')
                return redirect(url_for('admin.sessions'))

            else:  # dates mode
                date_strings = request.form.getlist('selected_dates')
                if not date_strings:
                    flash('Please select at least one date on the calendar.', 'danger')
                    return render_template('admin/new_session.html', fleets=fleets)
                sessions = []
                for ds in date_strings:
                    try:
                        s, _ = _session_from_form(require_date=False)
                        s.date = datetime.strptime(ds, '%Y-%m-%d').date()
                        sessions.append(s)
                    except ValueError:
                        continue
                db.session.add_all(sessions)
                db.session.commit()
                flash(f'{len(sessions)} session(s) created.', 'success')
                return redirect(url_for('admin.sessions'))
        else:
            db.session.add(session)
            db.session.commit()
            flash('Session created.', 'success')
            return redirect(url_for('admin.session_detail', session_id=session.id))

    return render_template('admin/new_session.html', fleets=fleets)


@admin_bp.route('/sessions/<int:session_id>')
@login_required
@admin_required
def session_detail(session_id):
    session = Session.query.get_or_404(session_id)
    # Sailors in the same fleet who aren't already signed up
    signed_up_ids = {s.sailor_id for s in session.signups}
    eligible = (Sailor.query
                .filter_by(fleet_id=session.fleet_id)
                .order_by(Sailor.last_name, Sailor.first_name)
                .all())
    available = [s for s in eligible if s.id not in signed_up_ids]
    return render_template('admin/session_detail.html',
                           session=session,
                           today=date.today(),
                           available_sailors=available)


@admin_bp.route('/sessions/<int:session_id>/add-signup', methods=['POST'])
@login_required
@admin_required
def admin_add_signup(session_id):
    session = Session.query.get_or_404(session_id)
    sailor_id   = request.form.get('sailor_id', type=int)
    signup_type = request.form.get('signup_type', 'commitment')

    if not sailor_id:
        flash('Please select a sailor.', 'danger')
        return redirect(url_for('admin.session_detail', session_id=session_id))

    sailor = Sailor.query.get_or_404(sailor_id)

    existing = Signup.query.filter_by(session_id=session_id, sailor_id=sailor_id).first()
    if existing:
        existing.signup_type = signup_type
        flash(f'Updated {sailor.name} to {signup_type}.', 'success')
    else:
        db.session.add(Signup(session_id=session_id, sailor_id=sailor_id, signup_type=signup_type))
        flash(f'Added {sailor.name} as {signup_type}.', 'success')

    db.session.commit()
    session.update_status()
    db.session.commit()
    return redirect(url_for('admin.session_detail', session_id=session_id))


@admin_bp.route('/sessions/<int:session_id>/remove-signup/<int:sailor_id>', methods=['POST'])
@login_required
@admin_required
def admin_remove_signup(session_id, sailor_id):
    signup = Signup.query.filter_by(session_id=session_id, sailor_id=sailor_id).first_or_404()
    sess        = signup.session
    sailor_name = signup.sailor.name
    db.session.delete(signup)
    db.session.commit()
    sess.update_status()
    db.session.commit()
    flash(f'Removed {sailor_name} from this session.', 'success')
    return redirect(url_for('admin.session_detail', session_id=session_id))


@admin_bp.route('/sessions/<int:session_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_session(session_id):
    session = Session.query.get_or_404(session_id)
    fleets = Fleet.query.order_by(Fleet.name).all()
    if request.method == 'POST':
        updated, error = _session_from_form(session)
        if error:
            flash(error, 'danger')
        else:
            updated.update_status()
            db.session.commit()
            flash('Session updated.', 'success')
            return redirect(url_for('admin.session_detail', session_id=session_id))
    return render_template('admin/edit_session.html', session=session, fleets=fleets)


@admin_bp.route('/sessions/<int:session_id>/cancel', methods=['POST'])
@login_required
@admin_required
def cancel_session(session_id):
    session = Session.query.get_or_404(session_id)
    session.status = 'cancelled'
    db.session.commit()
    flash('Session cancelled.', 'warning')
    return redirect(url_for('admin.session_detail', session_id=session_id))


@admin_bp.route('/sessions/<int:session_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_session(session_id):
    session = Session.query.get_or_404(session_id)
    db.session.delete(session)
    db.session.commit()
    flash('Session deleted.', 'success')
    return redirect(url_for('admin.sessions'))


@admin_bp.route('/sessions/<int:session_id>/restore', methods=['POST'])
@login_required
@admin_required
def restore_session(session_id):
    session = Session.query.get_or_404(session_id)
    session.status = 'pending'   # clear cancelled so update_status() can run
    session.update_status()      # auto-confirm if threshold already met
    db.session.commit()
    flash('Session restored.', 'success')
    return redirect(url_for('admin.session_detail', session_id=session_id))


@admin_bp.route('/sessions/<int:session_id>/force-confirm', methods=['POST'])
@login_required
@admin_required
def force_confirm_session(session_id):
    session = Session.query.get_or_404(session_id)
    if session.status == 'cancelled':
        flash('Cannot confirm a cancelled session.', 'danger')
    else:
        session.status = 'confirmed'
        db.session.commit()
        flash('Session manually confirmed.', 'success')
    return redirect(url_for('admin.session_detail', session_id=session_id))


# ── Sailors ───────────────────────────────────────────────────────────────────

@admin_bp.route('/sailors')
@login_required
@admin_required
def sailors():
    all_sailors = Sailor.query.order_by(Sailor.last_name, Sailor.first_name).all()
    fleets = Fleet.query.order_by(Fleet.name).all()
    fleet_id = request.args.get('fleet_id', type=int)
    if fleet_id:
        all_sailors = [s for s in all_sailors if s.fleet_id == fleet_id]
    return render_template('admin/sailors.html',
        sailors=all_sailors,
        fleets=fleets,
        selected_fleet=fleet_id,
    )


@admin_bp.route('/sailors/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_sailor_admin():
    fleets = Fleet.query.order_by(Fleet.name).all()
    users  = User.query.filter_by(is_admin=False).order_by(User.last_name, User.first_name).all()

    if request.method == 'POST':
        first_name       = request.form.get('first_name', '').strip()
        last_name        = request.form.get('last_name', '').strip()
        fleet_id         = request.form.get('fleet_id', type=int)
        parent_id        = request.form.get('parent_id', type=int)
        birthday_str     = request.form.get('birthday', '').strip()
        waiver_submitted = request.form.get('waiver_submitted') == '1'
        waiver_confirmed = request.form.get('waiver_confirmed') == '1'

        if not first_name or not last_name or not fleet_id or not parent_id:
            flash('First name, last name, fleet, and parent are required.', 'danger')
            return render_template('admin/new_sailor.html', fleets=fleets, users=users,
                                   form=request.form)

        birthday = None
        if birthday_str:
            try:
                birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid birthday format.', 'danger')
                return render_template('admin/new_sailor.html', fleets=fleets, users=users,
                                       form=request.form)

        sailor = Sailor(
            first_name       = first_name,
            last_name        = last_name,
            fleet_id         = fleet_id,
            parent_id        = parent_id,
            birthday         = birthday,
            waiver_submitted = waiver_submitted,
            waiver_confirmed = waiver_confirmed,
        )
        db.session.add(sailor)
        db.session.commit()
        flash(f'Created sailor {sailor.name}.', 'success')
        return redirect(url_for('admin.sailors'))

    return render_template('admin/new_sailor.html', fleets=fleets, users=users, form={})


@admin_bp.route('/sailors/<int:sailor_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_sailor_admin(sailor_id):
    sailor = Sailor.query.get_or_404(sailor_id)
    fleets = Fleet.query.order_by(Fleet.name).all()
    users  = User.query.filter_by(is_admin=False).order_by(User.last_name, User.first_name).all()

    if request.method == 'POST':
        first_name   = request.form.get('first_name', '').strip()
        last_name    = request.form.get('last_name', '').strip()
        fleet_id     = request.form.get('fleet_id', type=int)
        parent_id    = request.form.get('parent_id', type=int)
        birthday_str = request.form.get('birthday', '').strip()
        waiver_submitted = request.form.get('waiver_submitted') == '1'
        waiver_confirmed = request.form.get('waiver_confirmed') == '1'

        if not first_name or not last_name or not fleet_id or not parent_id:
            flash('First name, last name, fleet, and parent are required.', 'danger')
            return render_template('admin/edit_sailor.html', sailor=sailor, fleets=fleets, users=users)

        birthday = None
        if birthday_str:
            try:
                birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid birthday format.', 'danger')
                return render_template('admin/edit_sailor.html', sailor=sailor, fleets=fleets, users=users)

        sailor.first_name       = first_name
        sailor.last_name        = last_name
        sailor.fleet_id         = fleet_id
        sailor.parent_id        = parent_id
        sailor.birthday         = birthday
        sailor.waiver_submitted = waiver_submitted
        sailor.waiver_confirmed = waiver_confirmed
        db.session.commit()
        flash(f'Updated {sailor.name}.', 'success')
        return redirect(url_for('admin.sailors'))

    return render_template('admin/edit_sailor.html', sailor=sailor, fleets=fleets, users=users)


@admin_bp.route('/sailors/<int:sailor_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_sailor_admin(sailor_id):
    sailor = Sailor.query.get_or_404(sailor_id)
    name = sailor.name
    db.session.delete(sailor)
    db.session.commit()
    flash(f'Deleted {name}.', 'success')
    return redirect(url_for('admin.sailors'))


@admin_bp.route('/sailors/<int:sailor_id>/toggle-waiver', methods=['POST'])
@login_required
@admin_required
def toggle_waiver(sailor_id):
    sailor = Sailor.query.get_or_404(sailor_id)
    sailor.waiver_confirmed = not sailor.waiver_confirmed
    db.session.commit()
    status = 'confirmed' if sailor.waiver_confirmed else 'unconfirmed'
    flash(f'Waiver {status} for {sailor.name}.', 'success')
    return redirect(url_for('admin.sailors'))


# ── Waiver Links ──────────────────────────────────────────────────────────────

@admin_bp.route('/waiver-links', methods=['GET', 'POST'])
@login_required
@admin_required
def waiver_links():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        url = request.form.get('url', '').strip()
        fleet_id = request.form.get('fleet_id', type=int) or None
        sort_order = request.form.get('sort_order', type=int) or 0
        if not name or not url:
            flash('Name and URL are required.', 'danger')
        else:
            db.session.add(WaiverLink(name=name, url=url, fleet_id=fleet_id, sort_order=sort_order))
            db.session.commit()
            flash(f'Waiver link "{name}" added.', 'success')
        return redirect(url_for('admin.waiver_links'))

    links = WaiverLink.query.order_by(WaiverLink.sort_order, WaiverLink.name).all()
    fleets = Fleet.query.order_by(Fleet.name).all()
    return render_template('admin/waiver_links.html', links=links, fleets=fleets)


@admin_bp.route('/waiver-links/<int:link_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_waiver_link(link_id):
    link = WaiverLink.query.get_or_404(link_id)
    db.session.delete(link)
    db.session.commit()
    flash(f'Removed "{link.name}".', 'success')
    return redirect(url_for('admin.waiver_links'))


# ── User Management ───────────────────────────────────────────────────────────

@admin_bp.route('/users')
@login_required
@admin_required
def users():
    all_users = User.query.order_by(User.is_admin.desc(), User.last_name, User.first_name).all()
    return render_template('admin/users.html', users=all_users)


@admin_bp.route('/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_user():
    if request.method == 'POST':
        first_name  = request.form.get('first_name', '').strip()
        last_name   = request.form.get('last_name', '').strip()
        email       = request.form.get('email', '').strip().lower()
        audit_number = request.form.get('audit_number', '').strip()
        password    = request.form.get('password', '')
        is_admin    = request.form.get('is_admin') == '1'

        if not first_name or not last_name or not email or not password:
            flash('First name, last name, email, and password are required.', 'danger')
            return render_template('admin/user_form.html', action='new')

        if User.query.filter_by(email=email).first():
            flash('An account with that email already exists.', 'danger')
            return render_template('admin/user_form.html', action='new')

        user = User(
            first_name=first_name,
            last_name=last_name,
            email=email,
            audit_number=audit_number or None,
            is_admin=is_admin,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f'Account created for {user.name}.', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/user_form.html', action='new')


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        user.first_name   = request.form.get('first_name', '').strip()
        user.last_name    = request.form.get('last_name', '').strip()
        user.audit_number = request.form.get('audit_number', '').strip() or None
        new_email = request.form.get('email', '').strip().lower()
        is_admin  = request.form.get('is_admin') == '1'

        if not user.first_name or not user.last_name or not new_email:
            flash('First name, last name, and email are required.', 'danger')
            return render_template('admin/user_form.html', action='edit', user=user)

        if new_email != user.email and User.query.filter_by(email=new_email).first():
            flash('That email is already in use.', 'danger')
            return render_template('admin/user_form.html', action='edit', user=user)

        # Prevent removing your own admin
        if user.id == current_user.id and not is_admin:
            flash('You cannot remove your own admin access.', 'danger')
            return render_template('admin/user_form.html', action='edit', user=user)

        user.email    = new_email
        user.is_admin = is_admin

        new_password = request.form.get('password', '').strip()
        if new_password:
            user.set_password(new_password)

        db.session.commit()
        flash(f'Updated {user.name}.', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/user_form.html', action='edit', user=user)


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin.users'))
    name = user.name
    db.session.delete(user)
    db.session.commit()
    flash(f'Deleted account for {name}.', 'success')
    return redirect(url_for('admin.users'))


# ── Settings ──────────────────────────────────────────────────────────────────

@admin_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def settings():
    if request.method == 'POST':
        fleet_lock = 'true' if request.form.get('fleet_lock') == 'on' else 'false'
        AppSetting.set('fleet_lock', fleet_lock)
        db.session.commit()
        flash('Settings saved.', 'success')
        return redirect(url_for('admin.settings'))

    fleet_lock = AppSetting.get('fleet_lock', 'false') == 'true'
    return render_template('admin/settings.html', fleet_lock=fleet_lock)


# ── Email Configuration ────────────────────────────────────────────────────────

@admin_bp.route('/email-settings', methods=['GET', 'POST'])
@login_required
@admin_required
def email_settings():
    if request.method == 'POST':
        AppSetting.set('smtp_host',       request.form.get('smtp_host', '').strip())
        AppSetting.set('smtp_port',       request.form.get('smtp_port', '587').strip())
        AppSetting.set('smtp_encryption', request.form.get('smtp_encryption', 'tls'))
        AppSetting.set('smtp_username',   request.form.get('smtp_username', '').strip())
        # Only update password if a new one was entered
        new_password = request.form.get('smtp_password', '').strip()
        if new_password:
            AppSetting.set('smtp_password', new_password)
        AppSetting.set('smtp_from_name',  request.form.get('smtp_from_name', '').strip())
        db.session.commit()
        flash('Email settings saved.', 'success')
        return redirect(url_for('admin.email_settings'))

    config = {
        'smtp_host':       AppSetting.get('smtp_host'),
        'smtp_port':       AppSetting.get('smtp_port', '587'),
        'smtp_encryption': AppSetting.get('smtp_encryption', 'tls'),
        'smtp_username':   AppSetting.get('smtp_username'),
        'smtp_from_name':  AppSetting.get('smtp_from_name', 'LYC Jr Sailing'),
        'smtp_password_set': bool(AppSetting.get('smtp_password')),
    }
    logs = EmailLog.query.order_by(EmailLog.sent_at.desc()).limit(50).all()
    return render_template('admin/email_settings.html', config=config, logs=logs)


def _friendly_smtp_error(e):
    """Convert raw SMTP/network exceptions into actionable messages."""
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return ('Authentication failed',
                'The username or password was rejected by the server. '
                'Double-check your credentials and try again.')
    if isinstance(e, smtplib.SMTPConnectError):
        return ('Connection failed',
                'Could not connect to the SMTP server. '
                'Check that the host and port are correct and that the server is reachable.')
    if isinstance(e, smtplib.SMTPServerDisconnected):
        return ('Server disconnected',
                'The server closed the connection unexpectedly. '
                'This often means the wrong port or encryption type is selected.')
    if isinstance(e, smtplib.SMTPSenderRefused):
        return ('Sender refused',
                f'The server rejected the From address ({e.sender}). '
                'Make sure the username matches an authorised sending address.')
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return ('Recipient refused',
                'The server rejected the recipient address. '
                'Try a different test address.')
    if isinstance(e, smtplib.SMTPException):
        return ('SMTP error', str(e))
    if isinstance(e, ssl.SSLError):
        return ('SSL/TLS error',
                'The SSL handshake failed. '
                'Try switching between STARTTLS and SSL/TLS, or check that the port matches the encryption type.')
    if isinstance(e, (socket.gaierror, socket.herror)):
        return ('Host not found',
                f'Could not resolve "{AppSetting.get("smtp_host")}". '
                'Check that the SMTP host name is spelled correctly.')
    if isinstance(e, (TimeoutError, socket.timeout)):
        return ('Connection timed out',
                'The server did not respond in time. '
                'The host may be wrong, the port may be blocked, or the server may be down.')
    if isinstance(e, ConnectionRefusedError):
        return ('Connection refused',
                f'Port {AppSetting.get("smtp_port")} was refused. '
                'Check the port number and that the server allows connections from this host.')
    return ('Unexpected error', str(e))


@admin_bp.route('/email-settings/test', methods=['POST'])
@login_required
@admin_required
def test_email():
    to_addr = request.form.get('test_to', '').strip()

    config = {
        'smtp_host':       AppSetting.get('smtp_host'),
        'smtp_port':       AppSetting.get('smtp_port', '587'),
        'smtp_encryption': AppSetting.get('smtp_encryption', 'tls'),
        'smtp_username':   AppSetting.get('smtp_username'),
        'smtp_from_name':  AppSetting.get('smtp_from_name', 'LYC Jr Sailing'),
        'smtp_password_set': bool(AppSetting.get('smtp_password')),
    }

    def render_with_error(title, detail):
        logs = EmailLog.query.order_by(EmailLog.sent_at.desc()).limit(50).all()
        return render_template('admin/email_settings.html',
                               config=config,
                               test_error_title=title,
                               test_error_detail=detail,
                               test_to=to_addr,
                               logs=logs), 200

    if not to_addr:
        return render_with_error('Missing recipient',
                                 'Please enter an email address to send the test to.')

    ok, err = send_email(
        to_addr   = to_addr,
        subject   = 'LYC Jr Sailing — Test Email',
        body_text = 'This is a test email from your LYC Jr Sailing app. SMTP is configured correctly!',
    )
    if ok:
        flash(f'Test email sent successfully to {to_addr}.', 'success')
        return redirect(url_for('admin.email_settings'))

    # Split the flat error string into a short title + detail for display
    parts = err.split(' — ', 1)
    title  = parts[0]
    detail = parts[1] if len(parts) > 1 else err
    return render_with_error(title, detail)
