from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash
from ..models import User
from .. import db

auth = Blueprint('auth', __name__)


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
