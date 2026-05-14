"""
Run this ONCE after first deployment to seed reference data.
Tables are created automatically by build.sh (flask db upgrade).

Usage on Render Shell:
    python init_db.py
"""
import os

from flask import Flask
from app.models import db
from config import Config

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    print("Step 1: Creating all tables...")
    db.create_all()
    print("Tables created OK.")

    print("Step 2: Seeding data...")
    from app import _seed_data
    _seed_data()
    print("SUCCESS: All tables created and seeded!")
