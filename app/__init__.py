import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.instance_path, 'sailing.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'

    from .routes.main import main
    from .routes.auth import auth
    from .routes.admin import admin_bp

    app.register_blueprint(main)
    app.register_blueprint(auth)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    with app.app_context():
        db.create_all()
        _seed_data()

    return app


def _seed_data():
    from .models import Fleet, User
    from werkzeug.security import generate_password_hash

    if not Fleet.query.first():
        db.session.add_all([
            Fleet(name='Opti Green'),
            Fleet(name='Opti RWB'),
        ])
        db.session.commit()

    if not User.query.filter_by(is_admin=True).first():
        admin_user = User(
            email='admin@lycsailing.org',
            name='Admin',
            password_hash=generate_password_hash('changeme'),
            is_admin=True,
        )
        db.session.add(admin_user)
        db.session.commit()
