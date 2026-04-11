from datetime import date, datetime, timedelta
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from ..models import Fleet, Session, Signup, Sailor, User, AppSetting, WaiverLink
from .. import db

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
    return render_template('admin/session_detail.html', session=session, today=date.today())


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
