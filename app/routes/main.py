from datetime import date
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from ..models import Fleet, Session, Sailor, Signup
from .. import db

main = Blueprint('main', __name__)


@main.route('/')
def index():
    fleets = Fleet.query.order_by(Fleet.name).all()
    fleet_id = request.args.get('fleet_id', type=int)

    query = Session.query.filter(Session.date >= date.today()).order_by(Session.date)
    if fleet_id:
        query = query.filter_by(fleet_id=fleet_id)
    sessions = query.all()

    # Map session_id -> {sailor_id -> signup} for the current parent
    parent_signups = {}
    if current_user.is_authenticated and not current_user.is_admin:
        for sailor in current_user.sailors:
            for signup in sailor.signups:
                parent_signups.setdefault(signup.session_id, {})[sailor.id] = signup

    return render_template('index.html',
        fleets=fleets,
        sessions=sessions,
        selected_fleet=fleet_id,
        parent_signups=parent_signups,
        today=date.today(),
    )


@main.route('/session/<int:session_id>')
def session_detail(session_id):
    session = Session.query.get_or_404(session_id)
    fleets = Fleet.query.order_by(Fleet.name).all()

    my_sailors = []
    my_signups = {}  # sailor_id -> signup
    if current_user.is_authenticated and not current_user.is_admin:
        my_sailors = [s for s in current_user.sailors if s.fleet_id == session.fleet_id]
        for sailor in my_sailors:
            for signup in sailor.signups:
                if signup.session_id == session_id:
                    my_signups[sailor.id] = signup

    return render_template('session.html',
        session=session,
        fleets=fleets,
        my_sailors=my_sailors,
        my_signups=my_signups,
        today=date.today(),
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
        flash('This sailor is not in the correct fleet for this session.', 'danger')
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
    return redirect(url_for('main.session_detail', session_id=session_id))


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

    return redirect(url_for('main.session_detail', session_id=session_id))


@main.route('/my-sailors')
@login_required
def my_sailors():
    fleets = Fleet.query.order_by(Fleet.name).all()
    return render_template('my_sailors.html', fleets=fleets)


@main.route('/my-sailors/add', methods=['POST'])
@login_required
def add_sailor():
    name = request.form.get('name', '').strip()
    fleet_id = request.form.get('fleet_id', type=int)

    if not name or not fleet_id:
        flash('Name and fleet are required.', 'danger')
        return redirect(url_for('main.my_sailors'))

    fleet = Fleet.query.get_or_404(fleet_id)
    db.session.add(Sailor(name=name, fleet_id=fleet.id, parent_id=current_user.id))
    db.session.commit()
    flash(f'Added {name} to {fleet.name}.', 'success')
    return redirect(url_for('main.my_sailors'))


@main.route('/my-sailors/<int:sailor_id>/delete', methods=['POST'])
@login_required
def delete_sailor(sailor_id):
    sailor = Sailor.query.get_or_404(sailor_id)
    if sailor.parent_id != current_user.id:
        flash('Not authorized.', 'danger')
        return redirect(url_for('main.my_sailors'))
    db.session.delete(sailor)
    db.session.commit()
    flash(f'Removed {sailor.name}.', 'success')
    return redirect(url_for('main.my_sailors'))
