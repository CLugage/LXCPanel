import subprocess
import json
import logging
from flask import Flask, request, jsonify, Response
from threading import Thread, Lock
import time
import os
from flask_cors import CORS 

app = Flask(__name__)
CORS(app)

# Set up logging
logging.basicConfig(level=logging.DEBUG)

# A global dictionary to store container statuses
container_statuses = {}
status_lock = Lock()  # For thread safety
terminal_processes = {}  # To store subprocess references for terminals
terminal_process_lock = Lock()  # Lock for terminal process management

def update_container_status():
    """Function to periodically check the status of all containers."""
    while True:
        with status_lock:
            for container_name in container_statuses.keys():
                try:
                    output = subprocess.check_output(['lxc-info', '-n', container_name]).decode()
                    if "RUNNING" in output:
                        container_statuses[container_name] = 'RUNNING'
                    elif "STOPPED" in output:
                        container_statuses[container_name] = 'STOPPED'
                    else:
                        container_statuses[container_name] = 'UNKNOWN'
                except subprocess.CalledProcessError:
                    container_statuses[container_name] = 'UNKNOWN'
        time.sleep(10)  # Check every 10 seconds

@app.route('/create', methods=['POST'])
def create_container():
    data = request.json
    instance_name = data.get('name')
    ram = data.get('ram').replace("MB", "")  # Remove the 'MB' part for correct command usage
    disk = data.get('disk')
    node = data.get('node')

    try:
        # Create LXC container
        subprocess.run(['lxc-create', '-n', instance_name, '-t', 'ubuntu'], check=True)
        
        # Set resource limits after container creation
        subprocess.run(['lxc-cgroup', '-n', instance_name, 'memory.limit_in_bytes', f'{ram}M'], check=True)

        # Initialize the container status
        with status_lock:
            container_statuses[instance_name] = 'STOPPED'

        return jsonify({'status': 'success', 'message': f'Container {instance_name} created successfully!'}), 201
    except subprocess.CalledProcessError as e:
        logging.error(f"Error creating container: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'An unexpected error occurred: ' + str(e)}), 500

@app.route('/status', methods=['GET'])
def container_status():
    instance_name = request.args.get('name')
    
    if not instance_name:
        return jsonify({'status': 'error', 'message': 'Instance name is required.'}), 400

    try:
        with status_lock:
            if instance_name in container_statuses:
                return jsonify({'status': 'success', 'instance_status': container_statuses[instance_name]}), 200
            else:
                return jsonify({'status': 'error', 'message': 'Instance not found.'}), 404
    except Exception as e:
        logging.error(f"Error fetching status: {str(e)}")
        return jsonify({'status': 'error', 'message': 'An unexpected error occurred: ' + str(e)}), 500

@app.route('/start', methods=['POST'])
def start_container():
    data = request.json
    instance_name = data.get('name')

    try:
        subprocess.run(['lxc-start', '-n', instance_name], check=True)
        with status_lock:
            container_statuses[instance_name] = 'RUNNING'
        return jsonify({'status': 'success', 'message': f'Container {instance_name} started successfully!'}), 200
    except subprocess.CalledProcessError as e:
        logging.error(f"Error starting container: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_container():
    data = request.json
    instance_name = data.get('name')

    current_status = container_statuses.get(instance_name)

    if current_status == 'STOPPED':
        return jsonify({'status': 'success', 'message': f'Container {instance_name} is already stopped.'}), 200

    try:
        subprocess.run(['lxc-stop', '-n', instance_name], check=True)
        with status_lock:
            container_statuses[instance_name] = 'STOPPED'
        return jsonify({'status': 'success', 'message': f'Container {instance_name} stopped successfully!'}), 200
    except subprocess.CalledProcessError as e:
        logging.error(f"Error stopping container {instance_name}: {str(e)}")
        return jsonify({'status': 'error', 'message': f'Failed to stop the container: {str(e)}'}), 500

@app.route('/delete', methods=['POST'])
def delete_container():
    data = request.json
    instance_name = data.get('name')

    try:
        subprocess.run(['lxc-destroy', '-n', instance_name], check=True)
        with status_lock:
            container_statuses.pop(instance_name, None)
            # Clean up terminal process if it exists
            with terminal_process_lock:
                terminal_processes.pop(instance_name, None)
        return jsonify({'status': 'success', 'message': f'Container {instance_name} deleted successfully!'}), 200
    except subprocess.CalledProcessError as e:
        logging.error(f"Error deleting container: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

def generate_output(instance_name):
    """Generator to stream the terminal output."""
    process = terminal_processes[instance_name]
    while True:
        output = process.stdout.readline()  # Read output line by line
        if output:
            yield f"data: {output}\n\n"  # Format for server-sent events
        if process.poll() is not None:  # Check if the process has terminated
            break
        time.sleep(0.1)  # Small delay to prevent busy-waiting


@app.route('/terminal/<instance_name>', methods=['POST'])
def start_terminal(instance_name):
    if instance_name not in container_statuses:
        return jsonify({'status': 'error', 'message': 'Instance not found.'}), 404
    
    if container_statuses[instance_name] != 'RUNNING':
        return jsonify({'status': 'error', 'message': 'Container must be running to access the terminal.'}), 400
    
    if instance_name not in terminal_processes:
        # Start the terminal process with the correct container name
        process = subprocess.Popen(
            ['lxc-attach', '-n', instance_name, '--', 'bash'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        terminal_processes[instance_name] = process
    
    return Response(generate_output(instance_name), content_type='text/event-stream')

if __name__ == '__main__':
    # Start the thread for checking container status
    status_thread = Thread(target=update_container_status)
    status_thread.daemon = True
    status_thread.start()
    app.run(host='0.0.0.0', port=8080)  # Bind to all interfaces
