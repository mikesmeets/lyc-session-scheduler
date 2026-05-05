import calendar as cal_mod
from datetime import date, datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from ..models import Fleet, Session, Sailor, Signup, AppSetting, WaiverLink
from .. import db
from .. import weather as wx

main = Blueprint('main', __name__)


@main.route('/')
def index():
    fleets = Fleet.query.order_by(Fleet.name).all()
    fleet_id = request.args.get('fleet_id', type=int)
    view = request.args.get('view', 'calendar')  # 'cards', 'list', or 'calendar'
    selected_sailor_id = request.args.get('sailor_id', type=int)
    hide_empty = request.args.get('hide_empty') == '1'
    confirmed_only = request.args.get('confirmed_only') == '1'
    today = date.today()

    # For calendar view, scope to the displayed month (may include past sessions)
    if view == 'calendar':
        cal_year  = request.args.get('cal_year',  type=int) or today.year
        cal_month = request.args.get('cal_month', type=int) or today.month
        # clamp month
        if cal_month < 1:  cal_month = 12; cal_year -= 1
        if cal_month > 12: cal_month = 1;  cal_year += 1
        month_start = date(cal_year, cal_month, 1)
        # last day of month
        last_day = cal_mod.monthrange(cal_year, cal_month)[1]
        month_end = date(cal_year, cal_month, last_day)

        query = Session.query.filter(
            Session.date >= month_start,
            Session.date <= month_end,
        ).order_by(Session.date, Session.session_type)
        if fleet_id:
            query = query.filter_by(fleet_id=fleet_id)
        sessions = query.all()

        # Group sessions by date
        sessions_by_date = {}
        for s in sessions:
            sessions_by_date.setdefault(s.date, []).append(s)

        # Build calendar weeks (Mon-start); each cell is a date or None (padding)
        cal_weeks = cal_mod.monthcalendar(cal_year, cal_month)  # 0 = not in month

        # Prev / next month
        if cal_month == 1:
            prev_year, prev_month = cal_year - 1, 12
        else:
            prev_year, prev_month = cal_year, cal_month - 1
        if cal_month == 12:
            next_year, next_month = cal_year + 1, 1
        else:
            next_year, next_month = cal_year, cal_month + 1

    else:
        cal_year = cal_month = cal_weeks = sessions_by_date = None
        prev_year = prev_month = next_year = next_month = None

        query = Session.query.filter(Session.date >= today).order_by(Session.date)
        if fleet_id:
            query = query.filter_by(fleet_id=fleet_id)
        sessions = query.all()

    parent_signups = {}
    if current_user.is_authenticated and not current_user.is_admin:
        for sailor in current_user.sailors:
            for signup in sailor.signups:
                parent_signups.setdefault(signup.session_id, {})[sailor.id] = signup

    # Filter to confirmed sessions only
    if confirmed_only:
        sessions = [s for s in sessions if s.status == 'confirmed']
        if view == 'calendar':
            sessions_by_date = {}
            for s in sessions:
                sessions_by_date.setdefault(s.date, []).append(s)

    # Filter to only sessions with relevant signups
    if hide_empty and current_user.is_authenticated and not current_user.is_admin:
        if selected_sailor_id:
            # Specific sailor selected — show only sessions they're signed up for
            sessions = [s for s in sessions if parent_signups.get(s.id, {}).get(selected_sailor_id)]
        else:
            # All sailors — show only sessions where any of the parent's sailors are signed up
            sessions = [s for s in sessions if parent_signups.get(s.id)]
        if view == 'calendar':
            sessions_by_date = {}
            for s in sessions:
                sessions_by_date.setdefault(s.date, []).append(s)

    # Fetch weather for list/cards views (future sessions only; skip calendar
    # to avoid fetching weather for past months the user is browsing)
    weather = {}
    if view != 'calendar':
        try:
            weather = wx.get_weather_for_sessions(sessions)
        except Exception:
            pass

    return render_template('index.html',
        fleets=fleets,
        sessions=sessions,
        selected_fleet=fleet_id,
        parent_signups=parent_signups,
        today=today,
        view=view,
        selected_sailor_id=selected_sailor_id,
        hide_empty=hide_empty,
        confirmed_only=confirmed_only,
        weather=weather,
        # calendar-specific
        cal_year=cal_year,
        cal_month=cal_month,
        cal_weeks=cal_weeks,
        sessions_by_date=sessions_by_date,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
    )


@main.route('/session/<int:session_id>')
def session_detail(session_id):
    session = Session.query.get_or_404(session_id)
    fleets = Fleet.query.order_by(Fleet.name).all()

    my_sailors = []
    my_other_sailors = []
    my_signups = {}
    if current_user.is_authenticated and not current_user.is_admin:
        for sailor in current_user.sailors:
            if sailor.fleet_id == session.fleet_id:
                my_sailors.append(sailor)
            else:
                my_other_sailors.append(sailor)
        for sailor in my_sailors + my_other_sailors:
            for signup in sailor.signups:
                if signup.session_id == session_id:
                    my_signups[sailor.id] = signup

    session_weather = None
    try:
        session_weather = wx.get_weather_for_sessions([session]).get(session.id)
    except Exception:
        pass

    return render_template('session.html',
        session=session,
        fleets=fleets,
        my_sailors=my_sailors,
        my_other_sailors=my_other_sailors,
        my_signups=my_signups,
        today=date.today(),
        session_weather=session_weather,
    )


@main.route('/session/<int:session_id>/signup', methods=['POST'])
@login_required
def session_signup(session_id):
    session = Session.query.get_or_404(session_id)

    if session.status == 'cancelled':
        flash('This session has been cancelled.', 'danger')
        return redirect(url_for('main.session_detail', session_id=session_id))

    if session.is_past_deadline:
        flash('The signup deadline for this session has passed.', 'danger')
        return redirect(url_for('main.session_detail', session_id=session_id))

    sailor_id = request.form.get('sailor_id', type=int)
    signup_type = request.form.get('signup_type')

    if signup_type not in ('interest', 'commitment'):
        flash('Invalid signup type.', 'danger')
        return redirect(url_for('main.session_detail', session_id=session_id))

    sailor = Sailor.query.get_or_404(sailor_id)
    if sailor.parent_id != current_user.id:
        flash('Not authorized.', 'danger')
        return redirect(url_for('main.session_detail', session_id=session_id))

    if sailor.fleet_id != session.fleet_id:
        if request.form.get('confirm_cross_fleet') != '1':
            flash('Please confirm that you want to register this sailor outside their fleet.', 'warning')
            return redirect(url_for('main.session_detail', session_id=session_id))

    existing = Signup.query.filter_by(session_id=session_id, sailor_id=sailor_id).first()
    if existing:
        existing.signup_type = signup_type
        flash(f'Updated {sailor.name} to {signup_type}.', 'success')
    else:
        db.session.add(Signup(session_id=session_id, sailor_id=sailor_id, signup_type=signup_type))
        flash(f'Signed up {sailor.name} as {signup_type}.', 'success')

    session.update_status()
    db.session.commit()
    next_url = request.form.get('next')
    return redirect(next_url or url_for('main.session_detail', session_id=session_id))


@main.route('/session/<int:session_id>/unsignup/<int:sailor_id>', methods=['POST'])
@login_required
def session_unsignup(session_id, sailor_id):
    session = Session.query.get_or_404(session_id)
    sailor = Sailor.query.get_or_404(sailor_id)

    if sailor.parent_id != current_user.id:
        flash('Not authorized.', 'danger')
        return redirect(url_for('main.session_detail', session_id=session_id))

    if session.is_past_deadline:
        flash('The signup deadline has passed — changes are no longer allowed.', 'danger')
        return redirect(url_for('main.session_detail', session_id=session_id))

    signup = Signup.query.filter_by(session_id=session_id, sailor_id=sailor_id).first()
    if signup:
        db.session.delete(signup)
        session.update_status()
        db.session.commit()
        flash(f'Removed signup for {sailor.name}.', 'success')

    next_url = request.form.get('next')
    return redirect(next_url or url_for('main.session_detail', session_id=session_id))


def _parent_only():
    """Return a redirect if the current user is not a plain parent, else None."""
    if current_user.is_admin or current_user.is_coach:
        flash('That page is for parent accounts only.', 'warning')
        return redirect(url_for('main.index'))
    return None


@main.route('/my-sailors')
@login_required
def my_sailors():
    denied = _parent_only()
    if denied:
        return denied
    fleets = Fleet.query.order_by(Fleet.name).all()
    waiver_links = WaiverLink.query.order_by(WaiverLink.sort_order, WaiverLink.name).all()
    return render_template('my_sailors.html', fleets=fleets, waiver_links=waiver_links, today=date.today())


@main.route('/my-sailors/add', methods=['POST'])
@login_required
def add_sailor():
    denied = _parent_only()
    if denied:
        return denied
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    fleet_id = request.form.get('fleet_id', type=int)
    birthday_str = request.form.get('birthday', '').strip()

    if not first_name or not last_name or not fleet_id:
        flash('First name, last name, and fleet are required.', 'danger')
        return redirect(url_for('main.my_sailors'))

    # Fleet lock: prevent same child from being in multiple fleets
    if AppSetting.get('fleet_lock', 'false') == 'true':
        duplicate = Sailor.query.filter_by(
            parent_id=current_user.id,
            first_name=first_name,
            last_name=last_name,
        ).first()
        if duplicate:
            flash(
                f'{first_name} {last_name} is already registered in {duplicate.fleet.name}. '
                'Fleet lock is enabled — contact the admin to change fleets.',
                'danger',
            )
            return redirect(url_for('main.my_sailors'))

    fleet = Fleet.query.get_or_404(fleet_id)
    birthday = None
    if birthday_str:
        try:
            birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid birthday format.', 'danger')
            return redirect(url_for('main.my_sailors'))

    waiver_submitted = request.form.get('waiver_submitted') == '1'
    db.session.add(Sailor(
        first_name=first_name,
        last_name=last_name,
        birthday=birthday,
        fleet_id=fleet.id,
        parent_id=current_user.id,
        waiver_submitted=waiver_submitted,
    ))
    db.session.commit()
    flash(f'Added {first_name} {last_name} to {fleet.name}.', 'success')
    return redirect(url_for('main.my_sailors'))


@main.route('/my-sailors/<int:sailor_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_sailor(sailor_id):
    denied = _parent_only()
    if denied:
        return denied
    sailor = Sailor.query.get_or_404(sailor_id)
    if sailor.parent_id != current_user.id:
        flash('Not authorized.', 'danger')
        return redirect(url_for('main.my_sailors'))

    fleets = Fleet.query.order_by(Fleet.name).all()

    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        fleet_id = request.form.get('fleet_id', type=int)
        birthday_str = request.form.get('birthday', '').strip()

        if not first_name or not last_name or not fleet_id:
            flash('First name, last name, and fleet are required.', 'danger')
            return render_template('edit_sailor.html', sailor=sailor, fleets=fleets)

        # Fleet lock: prevent changing to a different fleet if another record exists
        if AppSetting.get('fleet_lock', 'false') == 'true' and fleet_id != sailor.fleet_id:
            conflict = Sailor.query.filter_by(
                parent_id=current_user.id,
                first_name=first_name,
                last_name=last_name,
            ).filter(Sailor.id != sailor.id).first()
            if conflict:
                flash(
                    'Fleet lock is enabled — contact the admin to change fleets.',
                    'danger',
                )
                return render_template('edit_sailor.html', sailor=sailor, fleets=fleets)

        birthday = None
        if birthday_str:
            try:
                birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid birthday format.', 'danger')
                return render_template('edit_sailor.html', sailor=sailor, fleets=fleets)

        sailor.first_name = first_name
        sailor.last_name = last_name
        sailor.fleet_id = fleet_id
        sailor.birthday = birthday
        db.session.commit()
        flash(f'Updated {sailor.name}.', 'success')
        return redirect(url_for('main.my_sailors'))

    return render_template('edit_sailor.html', sailor=sailor, fleets=fleets)


@main.route('/my-sailors/<int:sailor_id>/submit-waiver', methods=['POST'])
@login_required
def submit_waiver(sailor_id):
    denied = _parent_only()
    if denied:
        return denied
    sailor = Sailor.query.get_or_404(sailor_id)
    if sailor.parent_id != current_user.id:
        flash('Not authorized.', 'danger')
        return redirect(url_for('main.my_sailors'))
    sailor.waiver_submitted = not sailor.waiver_submitted
    db.session.commit()
    return redirect(url_for('main.my_sailors'))


@main.route('/my-sailors/<int:sailor_id>/delete', methods=['POST'])
@login_required
def delete_sailor(sailor_id):
    denied = _parent_only()
    if denied:
        return denied
    sailor = Sailor.query.get_or_404(sailor_id)
    if sailor.parent_id != current_user.id:
        flash('Not authorized.', 'danger')
        return redirect(url_for('main.my_sailors'))
    name = sailor.name
    db.session.delete(sailor)
    db.session.commit()
    flash(f'Removed {name}.', 'success')
    return redirect(url_for('main.my_sailors'))
