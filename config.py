import os

class Config:
    _db_url = os.environ.get('DATABASE_URL', '')

    # Render gives postgres:// — SQLAlchemy needs postgresql://
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)

    SQLALCHEMY_DATABASE_URI = _db_url or \
        'postgresql://postgres:postgres@localhost:5432/doctrack_db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle':  280,
        'pool_timeout':  20,
        'max_overflow':  5,
    }
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-this-in-render-env-vars')
    DEBUG = False
