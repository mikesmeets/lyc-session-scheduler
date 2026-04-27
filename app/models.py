from datetime import datetime, date, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from . import db, login_manager


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    audit_number = db.Column(db.String(50))
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    sailors = db.relationship('Sailor', backref='parent', lazy=True, cascade='all, delete-orphan')

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Fleet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    color = db.Column(db.String(7), default='#ffffff')
    sessions = db.relationship('Session', backref='fleet', lazy=True)
    sailors = db.relationship('Sailor', backref='fleet', lazy=True)


class Sailor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    birthday = db.Column(db.Date)
    waiver_submitted = db.Column(db.Boolean, default=False)   # parent marks as completed
    waiver_confirmed = db.Column(db.Boolean, default=False)   # admin confirms
    fleet_id = db.Column(db.Integer, db.ForeignKey('fleet.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    signups = db.relationship('Signup', backref='sailor', lazy=True, cascade='all, delete-orphan')

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}"


class Session(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fleet_id = db.Column(db.Integer, db.ForeignKey('fleet.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    session_type = db.Column(db.String(20), default='morning')  # morning, afternoon, full_day
    start_time = db.Column(db.String(10))
    end_time = db.Column(db.String(10))
    notes = db.Column(db.Text)
    commitment_deadline_days = db.Column(db.Integer, default=14)
    min_sailors = db.Column(db.Integer, default=5)
    status = db.Column(db.String(20), default='pending')  # pending, confirmed, cancelled
    signups = db.relationship('Signup', backref='session', lazy=True, cascade='all, delete-orphan')

    @property
    def commitment_deadline(self):
        return self.date - timedelta(days=self.commitment_deadline_days)

    @property
    def is_past_deadline(self):
        return date.today() > self.commitment_deadline

    @property
    def commitment_count(self):
        return sum(1 for s in self.signups if s.signup_type == 'commitment')

    @property
    def interest_count(self):
        return sum(1 for s in self.signups if s.signup_type == 'interest')

    def update_status(self):
        if self.status != 'cancelled':
            self.status = 'confirmed' if self.commitment_count >= self.min_sailors else 'pending'


class Signup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('session.id'), nullable=False)
    sailor_id = db.Column(db.Integer, db.ForeignKey('sailor.id'), nullable=False)
    signup_type = db.Column(db.String(20), nullable=False)  # 'interest' or 'commitment'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('session_id', 'sailor_id', name='unique_session_sailor'),
    )


class WaiverLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    fleet_id = db.Column(db.Integer, db.ForeignKey('fleet.id'), nullable=True)
    fleet = db.relationship('Fleet', backref='waiver_links')
    sort_order = db.Column(db.Integer, default=0)


class PasswordResetToken(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token      = db.Column(db.String(64), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used       = db.Column(db.Boolean, default=False)
    user       = db.relationship('User', backref='reset_tokens')


class AppSetting(db.Model):
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(200), nullable=False, default='')

    @staticmethod
    def get(key, default=''):
        s = AppSetting.query.get(key)
        return s.value if s else default

    @staticmethod
    def set(key, value):
        s = AppSetting.query.get(key)
        if s:
            s.value = str(value)
        else:
            db.session.add(AppSetting(key=key, value=str(value)))
