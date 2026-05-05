from functools import wraps
from datetime import date

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from ..models import Session, Sailor, Signup, Attendance, User
from .. import db
from .. import weather as wx

coach_bp = Blueprint('coach', __name__)


def coach_or_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if not (current_user.is_coach or current_user.is_admin):
            flash('You do not have permission to access that page.', 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated


@coach_bp.route('/')
@login_required
@coach_or_admin_required
def dashboard():
    today = date.today()
    if current_user.is_admin:
        # Admins see all sessions; coaches only see assigned ones
        upcoming = (Session.query
                    .filter(Session.date >= today)
                    .order_by(Session.date)
                    .all())
        past = (Session.query
                .filter(Session.date < today)
                .order_by(Session.date.desc())
                .limit(20)
                .all())
    else:
        upcoming = sorted(
            [s for s in current_user.coached_sessions if s.date >= today],
            key=lambda s: s.date
        )
        past = sorted(
            [s for s in current_user.coached_sessions if s.date < today],
            key=lambda s: s.date, reverse=True
        )[:20]

    # Build a set of session IDs that have any attendance recorded
    attended_ids = {a.session_id for a in Attendance.query.all()}

    weather = {}
    try:
        weather = wx.get_weather_for_sessions(upcoming + past)
    except Exception:
        pass

    return render_template('coach/dashboard.html',
                           upcoming=upcoming,
                           past=past,
                           attended_ids=attended_ids,
                           today=today,
                           weather=weather)


@coach_bp.route('/session/<int:session_id>/attendance', methods=['GET', 'POST'])
@login_required
@coach_or_admin_required
def attendance(session_id):
    sess = Session.query.get_or_404(session_id)

    # Coaches may only access their own sessions
    if not current_user.is_admin and current_user not in sess.coaches:
        flash('You are not assigned to that session.', 'danger')
        return redirect(url_for('coach.dashboard'))

    # Build ordered list: signed-up sailors first, then existing walk-ins
    signup_sailor_ids = {s.sailor_id for s in sess.signups}
    att_by_sailor = {a.sailor_id: a for a in sess.attendances}

    signups_sorted = sorted(sess.signups, key=lambda s: (s.sailor.last_name, s.sailor.first_name))
    walkin_records = [a for a in sess.attendances if a.is_walkin]

    # Sailors available to add as walk-ins (not already signed up or walked in)
    walkin_ids      = {a.sailor_id for a in walkin_records}
    already_present = signup_sailor_ids | walkin_ids
    available_walkins = (Sailor.query
                         .order_by(Sailor.last_name, Sailor.first_name)
                         .all())
    available_walkins = [s for s in available_walkins if s.id not in already_present]

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_walkin':
            walkin_id = request.form.get('walkin_sailor_id', type=int)
            if walkin_id:
                existing = Attendance.query.filter_by(
                    session_id=session_id, sailor_id=walkin_id).first()
                if not existing:
                    db.session.add(Attendance(
                        session_id = session_id,
                        sailor_id  = walkin_id,
                        present    = True,
                        is_walkin  = True,
                    ))
                    db.session.commit()
                    flash('Walk-in added.', 'success')
            return redirect(url_for('coach.attendance', session_id=session_id))

        if action == 'remove_walkin':
            walkin_id = request.form.get('walkin_sailor_id', type=int)
            if walkin_id:
                record = Attendance.query.filter_by(
                    session_id=session_id, sailor_id=walkin_id, is_walkin=True).first()
                if record:
                    db.session.delete(record)
                    db.session.commit()
                    flash('Walk-in removed.', 'success')
            return redirect(url_for('coach.attendance', session_id=session_id))

        if action == 'add_coach':
            coach_id = request.form.get('coach_id', type=int)
            if coach_id:
                coach = User.query.get(coach_id)
                if coach and coach not in sess.coaches:
                    sess.coaches.append(coach)
                    db.session.commit()
                    flash(f'{coach.name} added as coach.', 'success')
            return redirect(url_for('coach.attendance', session_id=session_id))

        if action == 'remove_coach':
            coach_id = request.form.get('coach_id', type=int)
            if coach_id:
                coach = User.query.get(coach_id)
                if coach and coach in sess.coaches:
                    sess.coaches.remove(coach)
                    db.session.commit()
                    flash(f'{coach.name} removed from session.', 'success')
            return redirect(url_for('coach.attendance', session_id=session_id))

        # --- Save attendance + notes ---
        # Collect all sailors being tracked (signups + walk-ins)
        all_sailor_ids = list(signup_sailor_ids | walkin_ids)

        for sid in all_sailor_ids:
            val = request.form.get(f'att_{sid}')   # 'present', 'absent', or ''
            existing = att_by_sailor.get(sid)

            if val == 'present':
                present_val = True
            elif val == 'absent':
                present_val = False
            else:
                present_val = None   # not marked

            if existing:
                existing.present = present_val
                existing.recorded_at = db.func.now()
            else:
                db.session.add(Attendance(
                    session_id = session_id,
                    sailor_id  = sid,
                    present    = present_val,
                    is_walkin  = sid in walkin_ids,
                ))

        sess.coach_notes_public  = request.form.get('coach_notes_public', '').strip() or None
        sess.coach_notes_private = request.form.get('coach_notes_private', '').strip() or None
        db.session.commit()
        flash('Attendance and notes saved.', 'success')
        return redirect(url_for('coach.attendance', session_id=session_id))

    all_coaches = User.query.filter_by(is_coach=True).order_by(User.last_name, User.first_name).all()
    unassigned_coaches = [c for c in all_coaches if c not in sess.coaches]

    session_wx = {}
    try:
        session_wx = wx.get_weather_for_sessions([sess])
    except Exception:
        pass
    session_weather = session_wx.get(sess.id)

    return render_template('coach/attendance.html',
                           sess=sess,
                           signups_sorted=signups_sorted,
                           walkin_records=walkin_records,
                           att_by_sailor=att_by_sailor,
                           available_walkins=available_walkins,
                           today=date.today(),
                           all_coaches=all_coaches,
                           unassigned_coaches=unassigned_coaches,
                           session_weather=session_weather)
