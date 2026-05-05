import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Use Postgres on Railway (DATABASE_URL set automatically), SQLite locally
    database_url = os.environ.get('DATABASE_URL', '')
    if database_url.startswith('postgres://'):
        # SQLAlchemy requires postgresql:// not postgres://
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url or (
        'sqlite:///' + os.path.join(app.instance_path, 'sailing.db')
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'

    from .routes.main import main
    from .routes.auth import auth
    from .routes.admin import admin_bp
    from .routes.coach import coach_bp

    app.register_blueprint(main)
    app.register_blueprint(auth)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(coach_bp, url_prefix='/coach')

    with app.app_context():
        # Run any pending Alembic migrations, then create tables not yet tracked
        try:
            from flask_migrate import upgrade as _db_upgrade
            _db_upgrade()
            db.create_all()
            _seed_data()
        except Exception as e:
            app.logger.error('Startup DB setup failed: %s', e)
            raise

    return app


def _seed_data():
    from .models import Fleet, User, WaiverLink
    from werkzeug.security import generate_password_hash

    if not Fleet.query.first():
        db.session.add_all([
            Fleet(name='Opti Green', color='#d4edda'),
            Fleet(name='Opti RWB',   color='#ffffff'),
        ])
        db.session.commit()

    if not WaiverLink.query.first():
        rwb   = Fleet.query.filter_by(name='Opti RWB').first()
        green = Fleet.query.filter_by(name='Opti Green').first()
        db.session.add_all([
            WaiverLink(
                name='Opti RWB Practice Day Waiver',
                url='https://u9592777.ct.sendgrid.net/ls/click?upn=u001.rN7HdRPhtr3-2BA1M4XFagRW5S2zatLVRBsi-2Bo-2FB5T-2FYhTIPFm6rxUwBibJ2hwk6wLCJ4wHEDMJgZpsehFtyeghIoejid-2FKQ6QBI2mhQ6clwPEP26o2xkU9cHsEiK3pkiUr7jbfpCnwv3IKk379QRzzg-3D-3DHuY8_-2FDV5ntRJeKyGvTDMfRqIFSGmEFfmi7ze8U70ZKQUlgqdByPQOoV-2Fe4pPi1pSdr-2FGNwF-2BNI81xgBkI8mehEmHOCSSK2qctOy0g7tnePJsAnsxqv0jmSaBnHUph5H9pqhq9x0nQ3Kx0dEpQRKiH99dQNOWgXEdTKS5NsJyBKO2L2lx0s011uQ8af1X4oBPqfa07ThAWB9mJaxAnPTrCA7sJA-3D-3D',
                fleet_id=rwb.id if rwb else None, sort_order=0,
            ),
            WaiverLink(
                name='Opti Green Fleet Clinics',
                url='https://u9592777.ct.sendgrid.net/ls/click?upn=u001.rN7HdRPhtr3-2BA1M4XFagRW5S2zatLVRBsi-2Bo-2FB5T-2FYhTIPFm6rxUwBibJ2hwk6wLCJ4wHEDMJgZpsehFtyeghIoejid-2FKQ6QBI2mhQ6clwMGLyFxhCBc7P91QC22aBcZ3m1Fw2f4wlM60S1vn-2B2SOA-3D-3D2tiw_-2FDV5ntRJeKyGvTDMfRqIFSGmEFfmi7ze8U70ZKQUlgqdByPQOoV-2Fe4pPi1pSdr-2FGNwF-2BNI81xgBkI8mehEmHOG1RcHOkTcpVIjA9AaCPXJDZyLHr-2FenLHGIGo84N1fgQ-2BxZzcQkMuTGN45jeVWDBSnA1DtMDIOcQCUPLrFXRfXbiJvvCu-2Fz84QpgkhSChRZqoR-2F5YWDPn3sfBp1fSbMBJg-3D-3D',
                fleet_id=green.id if green else None, sort_order=1,
            ),
        ])
        db.session.commit()

    if not User.query.filter_by(is_admin=True).first():
        db.session.add(User(
            email='admin@lycsailing.org',
            first_name='Admin',
            last_name='User',
            password_hash=generate_password_hash('admin'),
            is_admin=True,
        ))
        db.session.commit()
