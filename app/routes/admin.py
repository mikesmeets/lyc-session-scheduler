from datetime import date, datetime
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from ..models import Fleet, Session, Signup, Sailor, User
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
            db.session.add(Fleet(name=name))
            db.session.commit()
            flash(f'Fleet "{name}" created.', 'success')
        return redirect(url_for('admin.fleets'))

    return render_template('admin/fleets.html', fleets=Fleet.query.order_by(Fleet.name).all())


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


def _session_from_form(session=None):
    """Parse session form fields; returns (session_obj, error_msg)."""
    fleet_id = request.form.get('fleet_id', type=int)
    date_str = request.form.get('date', '')
    start_time = request.form.get('start_time', '').strip() or None
    end_time = request.form.get('end_time', '').strip() or None
    notes = request.form.get('notes', '').strip() or None
    deadline_days = request.form.get('commitment_deadline_days', type=int) or 14
    min_sailors = request.form.get('min_sailors', type=int) or 5

    if not fleet_id or not date_str:
        return None, 'Fleet and date are required.'
    try:
        session_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return None, 'Invalid date format.'

    if session is None:
        session = Session()
    session.fleet_id = fleet_id
    session.date = session_date
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
        session, error = _session_from_form()
        if error:
            flash(error, 'danger')
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


@admin_bp.route('/sessions/<int:session_id>/restore', methods=['POST'])
@login_required
@admin_required
def restore_session(session_id):
    session = Session.query.get_or_404(session_id)
    session.update_status()
    db.session.commit()
    flash('Session restored.', 'success')
    return redirect(url_for('admin.session_detail', session_id=session_id))
