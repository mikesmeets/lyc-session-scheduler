from functools import wraps
from datetime import date

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from ..models import Session, Sailor, Signup, Attendance, User, AppSetting
from .. import db
from .. import weather as wx
from ..email_utils import send_email_multi

coach_bp = Blueprint('coach', __name__)


def _build_summary_email(sess, weather=None):
    """Return (subject, body_text, body_html) for a session summary email."""
    type_label = sess.session_type.replace('_', ' ').title()
    date_str   = sess.date.strftime('%A, %B %-d, %Y') if hasattr(sess.date, 'strftime') else str(sess.date)
    time_str   = sess.start_time or ''
    if time_str and sess.end_time:
        time_str += f' – {sess.end_time}'

    coaches_str = ', '.join(c.name for c in sess.coaches) if sess.coaches else 'Not assigned'

    present = sorted(
        [a for a in sess.attendances if a.present],
        key=lambda a: (a.sailor.last_name, a.sailor.first_name)
    )
    attendee_names = [a.sailor.name for a in present]

    # Weather line
    wx_line = ''
    if weather:
        parts = []
        if weather.get('wind_speed') is not None:
            w = f"{weather['wind_speed']} kn {weather['wind_dir']}"
            if weather.get('wind_gust') and weather['wind_gust'] > weather['wind_speed']:
                w += f" (gusts {weather['wind_gust']})"
            parts.append(w)
        if weather.get('description'):
            parts.append(weather['description'])
        if weather.get('temp') is not None:
            parts.append(f"{weather['temp']}°F")
        source = 'NOAA GFS' if weather.get('is_forecast') else 'ERA5'
        wx_line = ' · '.join(parts) + f' (Open-Meteo / {source})'

    notes = sess.coach_notes_public or ''

    subject = f"Session Summary: {sess.fleet.name} {type_label} — {sess.date.strftime('%b %-d, %Y')}"

    # ── Plain text ─────────────────────────────────────────────────────────────
    lines = [
        f"LYC Junior Sailing — Session Summary",
        f"{'=' * 40}",
        f"{sess.fleet.name} {type_label}",
        f"Date:  {date_str}",
    ]
    if time_str:
        lines.append(f"Time:  {time_str}")
    if wx_line:
        lines.append(f"Conditions: {wx_line}")
    lines.append(f"Coach(es): {coaches_str}")
    lines += [
        '',
        f"Attendance ({len(attendee_names)} present)",
        '-' * 30,
    ]
    for name in attendee_names:
        lines.append(f"  • {name}")
    if notes:
        lines += ['', "Coach's Notes", '-' * 30, notes]
    lines += ['', '—', 'LYC Junior Sailing Training App']
    body_text = '\n'.join(lines)

    # ── HTML ───────────────────────────────────────────────────────────────────
    def li(name):
        return f'<li style="padding:2px 0">{name}</li>'

    attendee_items = '\n'.join(li(n) for n in attendee_names) or '<li><em>None recorded</em></li>'
    notes_block = (
        f'<h3 style="color:#0d6efd">Coach\'s Notes</h3>'
        f'<p style="white-space:pre-wrap;background:#f8f9fa;padding:12px;border-radius:6px">{notes}</p>'
        if notes else ''
    )
    wx_block = (
        f'<p><strong>Conditions:</strong> {wx_line}</p>'
        if wx_line else ''
    )
    time_block = f'<p><strong>Time:</strong> {time_str}</p>' if time_str else ''

    body_html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333;padding:0">
  <div style="background:#0d6efd;color:#fff;padding:20px 24px;border-radius:8px 8px 0 0">
    <div style="font-size:18px;font-weight:bold">LYC Junior Sailing</div>
    <div style="opacity:.85;font-size:13px;margin-top:2px">Session Summary</div>
  </div>
  <div style="border:1px solid #dee2e6;border-top:none;padding:24px;border-radius:0 0 8px 8px">
    <h2 style="margin-top:0;color:#0d6efd">{sess.fleet.name} {type_label}</h2>
    <p><strong>Date:</strong> {date_str}</p>
    {time_block}
    {wx_block}
    <p><strong>Coach(es):</strong> {coaches_str}</p>
    <hr style="border:none;border-top:1px solid #dee2e6;margin:16px 0">
    <h3 style="color:#0d6efd">Attendance <span style="font-weight:normal;color:#6c757d">({len(attendee_names)} present)</span></h3>
    <ul style="padding-left:20px;margin:0">
      {attendee_items}
    </ul>
    {('<hr style="border:none;border-top:1px solid #dee2e6;margin:16px 0">' + notes_block) if notes_block else ''}
    <hr style="border:none;border-top:1px solid #dee2e6;margin:24px 0 12px">
    <p style="font-size:11px;color:#adb5bd;margin:0">Sent by LYC Junior Sailing Training App</p>
  </div>
</body></html>"""

    return subject, body_text, body_html


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

        if action == 'publish':
            # Save notes first
            sess.coach_notes_public  = request.form.get('coach_notes_public',  '').strip() or None
            sess.coach_notes_private = request.form.get('coach_notes_private', '').strip() or None
            db.session.commit()

            # Build recipient lists
            to_addrs  = []   # visible in To: header
            bcc_addrs = []   # hidden (parents, accounting)

            # Coaches attached to this session always receive the summary
            to_addrs.extend(c.email for c in sess.coaches)

            if request.form.get('include_admins') == '1':
                to_addrs.extend(u.email for u in User.query.filter_by(is_admin=True).all())

            if request.form.get('include_accounting') == '1':
                acct = AppSetting.get('accounting_email', '')
                if acct:
                    bcc_addrs.append(acct)

            if request.form.get('include_parents') == '1':
                seen_parents = set()
                for att in sess.attendances:
                    if att.present and att.sailor.parent_id not in seen_parents:
                        seen_parents.add(att.sailor.parent_id)
                        bcc_addrs.append(att.sailor.parent.email)

            # Deduplicate
            to_addrs  = list(dict.fromkeys(a for a in to_addrs  if a))
            bcc_addrs = list(dict.fromkeys(a for a in bcc_addrs if a and a not in to_addrs))

            if not to_addrs and not bcc_addrs:
                flash('No recipients — select at least one group or add coaches/admins first.', 'warning')
                return redirect(url_for('coach.attendance', session_id=session_id))

            # Build email content
            subject, body_text, body_html = _build_summary_email(sess, session_weather)

            ok, err = send_email_multi(to_addrs, bcc_addrs, subject, body_text, body_html)
            n = len(to_addrs) + len(bcc_addrs)
            if ok:
                flash(f'Summary sent to {n} recipient{"s" if n != 1 else ""}.', 'success')
            else:
                flash(f'Email failed: {err}', 'danger')
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
                           session_weather=session_weather,
                           accounting_email=AppSetting.get('accounting_email', ''))
