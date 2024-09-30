from flask import Flask, render_template, redirect, url_for, flash, request
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from models import db, User, Instance, Node, UserCredits
import os
from forms import RegistrationForm, LoginForm
import subprocess
import re
import requests
import logging
from flask_socketio import SocketIO, emit
import eventlet




app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

socketio = SocketIO(app, cors_allowed_origins='*')



PLANS = {
    'Basic': {'ram': '512MB', 'disk': '10GB'},
    'Standard': {'ram': '1GB', 'disk': '20GB'},
    'Premium': {'ram': '2GB', 'disk': '40GB'},
    'Pro': {'ram': '4GB', 'disk': '80GB'},
}

def create_admin_account():
    with app.app_context():
        admin_user = User.query.filter_by(username='admin').first()
        if not admin_user:
            new_admin = User(username='admin')
            new_admin.set_password('Allexander01')
            new_admin.is_admin = True  # Set is_admin to True
            db.session.add(new_admin)
            db.session.commit()


def add_credits(user_id, amount):
    user_credits = UserCredits.query.filter_by(user_id=user_id).first()
    if user_credits:
        user_credits.balance += amount
        db.session.commit()

        # Unsuspend instances if user now has enough credits
        instances = Instance.query.filter_by(user_id=user_id, suspended=True).all()
        for instance in instances:
            plans = load_plans()
            plan_details = next((plan for plan in plans if plan['name'] == instance.plan), None)
            if plan_details and user_credits.balance >= plan_details['cost']:
                instance.suspended = False  # Unsuspend the instance
                db.session.commit()

        return True
    return False

def deduct_credits(user_id, amount):
    user_credits = UserCredits.query.filter_by(user_id=user_id).first()
    if user_credits and user_credits.balance >= amount:
        user_credits.balance -= amount
        db.session.commit()
        return True
    return False



@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegistrationForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        
        # Check if the username already exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists. Please choose a different one.', 'danger')
            return redirect(url_for('register'))
        
        # Create new user and set password
        new_user = User(username=username)
        new_user.set_password(password)
        
        # Add the user to the database
        db.session.add(new_user)
        db.session.commit()

        # Create a new UserCredits entry with a starting balance of 5000
        user_credits = UserCredits(user_id=new_user.id, balance=5000)
        db.session.add(user_credits)
        db.session.commit()
        
        flash('Your account has been created! You can now log in.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):  # Check hashed password
            login_user(user)
            return redirect(url_for('manage_instances'))
        flash('Login unsuccessful. Please check username and password.', 'danger')
    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/plans', methods=['GET'])
@login_required
def view_plans():
    plans = load_plans()  # Fetch plans from the JSON file
    return render_template('view_plans.html', plans=plans)


from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import subprocess
import json




def monthly_billing_job():
    with app.app_context():
        instances = Instance.query.all()
        for instance in instances:
            if instance.last_billed_date:
                days_since_last_billing = (datetime.utcnow() - instance.last_billed_date).days
                if days_since_last_billing >= 30:
                    # Get plan details from plans.json
                    plans = load_plans()
                    plan_details = next((plan for plan in plans if plan['name'] == instance.plan), None)

                    if plan_details:
                        cost = plan_details['cost']
                        
                        # Deduct credits for the monthly cost
                        if deduct_credits(instance.user_id, cost):
                            instance.last_billed_date = datetime.utcnow()
                            instance.suspended = False  # Ensure it's active if they can pay
                            db.session.commit()
                        else:
                            # Suspend the instance if there aren't enough credits
                            instance.suspended = True
                            db.session.commit()
                            flash(f'Instance {instance.name} suspended due to insufficient credits.', 'warning')
                    else:
                        flash(f'Plan {instance.plan} not found for billing instance {instance.name}.', 'danger')



# Initialize the scheduler and start the job
scheduler = BackgroundScheduler()
scheduler.add_job(func=monthly_billing_job, trigger="interval", days=1)  # Check every day
scheduler.start()

# Shut down the scheduler when exiting the app
import atexit
atexit.register(lambda: scheduler.shutdown())




# Load plans from JSON file
def load_plans():
    plans_file_path = os.path.join(os.path.dirname(__file__), 'plans.json')
    with open(plans_file_path) as plans_file:
        plans = json.load(plans_file)
    return plans

@app.route('/create', methods=['GET', 'POST'])
@login_required
def create_instance():
    nodes = Node.query.all()  # Fetch nodes for selection
    plans = load_plans()  # Load plans from JSON

    if request.method == 'POST':
        instance_name = request.form['name']
        selected_plan = request.form['plan']
        selected_node_id = request.form['node']  # Get selected node
        
        # Validate instance name and find the selected plan
        if not re.match("^[a-zA-Z0-9_-]*$", instance_name):
            flash('Invalid instance name. Only alphanumeric characters, dashes, and underscores are allowed.', 'danger')
            return redirect(url_for('create_instance'))

        plan_details = next((plan for plan in plans if plan['name'] == selected_plan), None)
        if not plan_details:
            flash('Selected plan is invalid.', 'danger')
            return redirect(url_for('create_instance'))

        ram = plan_details['ram']
        disk = plan_details['disk']
        cost = plan_details['cost']

        # Check if the user has enough credits
        if not deduct_credits(current_user.id, cost):
            flash('You do not have enough credits to create this instance.', 'danger')
            return redirect(url_for('create_instance'))

        selected_node = Node.query.get(selected_node_id)
        if not selected_node.is_active:
            flash(f'The selected node ({selected_node.name}) is not active. Please choose another node.', 'danger')
            return redirect(url_for('create_instance'))

        # Create the container and attempt to add the instance to the database
        instance_created = False
        try:
            url = f'http://{selected_node.ip_address}:8080/create'
            response = requests.post(url, json={'name': instance_name, 'ram': ram, 'disk': disk, 'node': selected_node.name})

            if response.status_code == 201:
                instance_created = True
                flash(f'Container {instance_name} created successfully!', 'success')
            else:
                flash(f'Failed to create the container: {response.json().get("message")}', 'danger')

        except Exception as e:
            flash(f'Error connecting to the daemon: {str(e)}', 'danger')

        # Regardless of success, add the instance to the database with relevant details
        new_instance = Instance(
            name=instance_name,
            user_id=current_user.id,
            plan=selected_plan,
            node_id=selected_node_id,
            creation_date=datetime.utcnow(),
            last_billed_date=datetime.utcnow(),  # Initialize with creation date
        )
        db.session.add(new_instance)
        db.session.commit()

        return redirect(url_for('manage_instances'))

    return render_template('create_instance.html', plans=plans, nodes=nodes)






@app.route('/manage')
@login_required
def manage_instances():
    # Fetch user instances from the database
    instances = Instance.query.filter_by(user_id=current_user.id).all()

    # Fetch user's credit balance
    user_credits = UserCredits.query.filter_by(user_id=current_user.id).first()
    balance = user_credits.balance if user_credits else 0  # Default balance if no entry found

    # Fetch statuses for each instance
    for instance in instances:
        # Fetch the status using the API endpoint
        try:
            status_response = requests.get(f'http://45.137.70.53:8080/status?name={instance.name}')
            if status_response.status_code == 200:
                instance.status = status_response.json().get('instance_status', 'UNKNOWN')
            else:
                instance.status = 'UNKNOWN'
        except Exception as e:
            logging.error(f"Failed to fetch status for {instance.name}: {str(e)}")
            instance.status = 'UNKNOWN'  # Fallback status

    # Render the manage instances template, passing instances and balance
    return render_template('manage_instances.html', instances=instances, balance=balance)




@app.route('/start/<name>')
@login_required
def start_instance(name):
    instance = Instance.query.filter_by(name=name, user_id=current_user.id).first()

    if instance.suspended:
        flash(f'Instance {name} is suspended. Please top up your credits to unsuspend it.', 'danger')
        return redirect(url_for('manage_instances'))

    try:
        response = requests.post('http://45.137.70.53:8080/start', json={'name': name})
        if response.status_code == 200:
            flash(f'Instance {name} started successfully!', 'success')
        else:
            flash('Failed to start the container: ' + response.json().get('message'), 'danger')
    except Exception as e:
        flash('Error connecting to the daemon: ' + str(e), 'danger')

    return redirect(url_for('manage_instances'))


@app.route('/stop/<name>')
@login_required
def stop_instance(name):
    try:
        response = requests.post('http://45.137.70.53:8080/stop', json={'name': name})
        if response.status_code == 200:
            flash(f'Instance {name} stopped successfully!', 'success')
        else:
            flash('Failed to stop the container: ' + response.json().get('message'), 'danger')
    except Exception as e:
        flash('Error connecting to the daemon: ' + str(e), 'danger')

    return redirect(url_for('manage_instances'))

@app.route('/delete/<name>')
@login_required
def delete_instance(name):
    try:
        response = requests.post('http://localhost:8080/delete', json={'name': name})
        if response.status_code == 200:
            flash(f'Instance {name} deleted successfully!', 'success')
        else:
            flash('Failed to delete the container: ' + response.json().get('message'), 'danger')
    except Exception as e:
        flash('Error connecting to the daemon: ' + str(e), 'danger')

    return redirect(url_for('manage_instances'))




@app.route('/admin/nodes', methods=['GET', 'POST'])
@login_required
def manage_nodes():
    if not current_user.is_admin:
        return redirect(url_for('index'))

    if request.method == 'POST':
        node_name = request.form['name']
        node_ip = request.form['ip_address']
        new_node = Node(name=node_name, ip_address=node_ip)
        db.session.add(new_node)
        db.session.commit()
        flash('Node added successfully!', 'success')
        return redirect(url_for('manage_nodes'))

    nodes = Node.query.all()
    return render_template('manage_nodes.html', nodes=nodes)


@app.route('/admin/nodes/toggle/<int:id>')
@login_required
def toggle_node(id):
    if not current_user.is_admin:
        return redirect(url_for('index'))

    node = Node.query.get(id)
    if node:
        node.is_active = not node.is_active  # Toggle active state
        db.session.commit()
        flash(f'Node {"activated" if node.is_active else "deactivated"} successfully!', 'success')
    return redirect(url_for('manage_nodes'))


@app.route('/admin/nodes/delete/<int:id>')
@login_required
def delete_node(id):
    if not current_user.is_admin:
        return redirect(url_for('index'))

    node = Node.query.get(id)
    if node:
        db.session.delete(node)
        db.session.commit()
        flash('Node deleted successfully!', 'success')
    return redirect(url_for('manage_nodes'))

@app.route('/admin/manage_credits', methods=['GET', 'POST'])
@login_required
def manage_credits():
    if not current_user.is_admin:
        return redirect(url_for('dashboard'))  # Redirect if not admin

    if request.method == 'POST':
        username = request.form.get('username')
        action = request.form.get('action')
        amount = request.form.get('amount', type=int)

        # Find user by username
        user = User.query.filter_by(username=username).first()
        if user:
            user_credits = UserCredits.query.filter_by(user_id=user.id).first()
            if action == 'give':
                user_credits.balance += amount
            elif action == 'deduct':
                user_credits.balance -= amount
                if user_credits.balance < 0:
                    user_credits.balance = 0  # Prevent negative balance
            db.session.commit()
            flash(f'Successfully {action}ed {amount} credits to {username}.', 'success')
        else:
            flash('User not found.', 'danger')

    return render_template('admin/manage_credits.html')




@socketio.on('execute_command')
def handle_command(data):
    command = data['command']
    user_id = current_user.id

    # Ensure that only the logged-in user can execute commands
    if not user_id:
        emit('command_output', {'output': 'Unauthorized'}, room=request.sid)
        return

    try:
        # Execute the command securely
        output = subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
        emit('command_output', {'output': output}, room=request.sid)
    except subprocess.CalledProcessError as e:
        emit('command_output', {'output': e.output}, room=request.sid)


@app.route('/terminal/<name>')
@login_required
def terminal(name):
    # Set your daemon IP address here
    daemon_ip = "45.137.70.53"  # Replace with your actual daemon IP
    instance = Instance.query.filter_by(name=name, user_id=current_user.id).first()
    
    if not instance:
        flash('Instance not found or you do not have access to it.', 'danger')
        return redirect(url_for('manage_instances'))

    # Pass the instance and daemon IP to the template
    return render_template('terminal.html', instance=instance, DAEMON_IP=daemon_ip)




socketio = SocketIO(app)


@socketio.on('command')
def handle_command(data):
    name = data['name']
    command = data['command'].strip()

    # Here, use subprocess or a library to execute commands on the LXC container
    try:
        # Example: executing command via LXC
        process = subprocess.Popen(['lxc-execute', '-n', name, '--', 'bash', '-c', command],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)

        output, error = process.communicate()

        if output:
            emit('output', output.decode())
        if error:
            emit('output', error.decode())
    except Exception as e:
        emit('output', f'Error: {str(e)}')


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_admin_account()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

