from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import UserMixin
from datetime import datetime  # Correct import

db = SQLAlchemy()
bcrypt = Bcrypt()

class User(db.Model, UserMixin):  # Inherit from UserMixin
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False, unique=True)
    password = db.Column(db.String(150), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    def set_password(self, password):
        self.password = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password, password)

class Instance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan = db.Column(db.String(50), nullable=False)
    node_id = db.Column(db.Integer, db.ForeignKey('node.id'), nullable=False)
    status = db.Column(db.String(20), default='Stopped')
    creation_date = db.Column(db.DateTime, default=datetime.utcnow) 
    last_billed_date = db.Column(db.DateTime, nullable=True) 
    suspended = db.Column(db.Boolean, default=False)

    user = db.relationship('User', backref=db.backref('instances', lazy=True))

class Node(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    ip_address = db.Column(db.String(15), unique=True, nullable=False)  # IPv4 address
    instances = db.relationship('Instance', backref='node', lazy=True)
    is_active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f'<Node {self.name}>'

class UserCredits(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True)  # Assuming you have a User model
    balance = db.Column(db.Integer, default=0)  # Credit balance

    def __repr__(self):
        return f'<UserCredits {self.user_id}: {self.balance}>'

class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_name = db.Column(db.String(50), nullable=False)
    start_date = db.Column(db.DateTime, default=datetime.utcnow)  # Correct reference
    next_billing_date = db.Column(db.DateTime, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    user = db.relationship('User', backref=db.backref('subscriptions', lazy=True))
