from werkzeug.middleware.proxy_fix import ProxyFix
import os
import time
from flask import Flask, render_template_string, request, jsonify, url_for, redirect
import subprocess
import sqlite3
import json
import requests
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import logging
from apscheduler.executors.pool import ThreadPoolExecutor

def clear_and_reinitialize_executor():
    if 'default' in scheduler._executors:
        executor = scheduler._executors['default']
        if executor._pool is not None:
            executor.shutdown(wait=False)
    new_executor = ThreadPoolExecutor(max_workers=1) 
    scheduler._executors['default'] = new_executor

    if not scheduler.running:
        scheduler.start(paused=False)

    print("Executor cleared and reinitialized.")
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'speedtest.db')
LOCKFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'speedtest.lock')

scheduler = BackgroundScheduler()
scheduler.start()

DEFAULT_INTERVAL = 3600 

def init_db():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS speedtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME,
                download REAL,
                upload REAL,
                ping REAL,
                url TEXT,
                server_id INTEGER,
                server_name TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interval INTEGER,
                server_id INTEGER
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS global_cooldown (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                last_test_time DATETIME
            )
        ''')
        conn.execute('''
            INSERT OR IGNORE INTO settings (id, interval, server_id)
            VALUES (1, ?, ?)
        ''', (DEFAULT_INTERVAL, None))
        conn.execute('''
            INSERT OR IGNORE INTO global_cooldown (id, last_test_time)
            VALUES (1, ?)
        ''', (datetime.now(),))
        conn.commit()


init_db()

COOLDOWN_PERIOD = 300
def get_last_test_time():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT last_test_time FROM global_cooldown WHERE id = 1')
        row = cursor.fetchone()
        if row and row[0]:
            try:
                return datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                return None
        return None

def update_last_test_time():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('''
            UPDATE global_cooldown
            SET last_test_time = ?
            WHERE id = 1
        ''', (datetime.now(),))
        conn.commit()

def create_lockfile():
    with open(LOCKFILE, 'w') as f:
        f.write('locked')

def remove_lockfile():
    if os.path.exists(LOCKFILE):
        os.remove(LOCKFILE)

def is_locked():
    return os.path.exists(LOCKFILE)

def get_servers():
    url = "https://www.speedtest.net/api/js/servers?engine=js&limit=10&https_functional=true"
    try:
        response = requests.get(url)
        response.raise_for_status()
        servers = response.json() 
        return servers 
    except requests.RequestException as e:
        return {"error": str(e)}

def speed_test(server_id=None):
    if is_locked():
        return
    create_lockfile()
    
    try:
        command = [
            "speedtest",
            "--accept-gdpr",
            "--accept-license",
            "--format=json"
        ]
        
        if server_id:
            command.extend(["-s", str(server_id)])

        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        if result.returncode != 0:
            try:
                error_data = json.loads(result.stderr)
                error_message = error_data.get("message", "Speedtest failed: Unknown error")
            except json.JSONDecodeError:
                error_message = result.stderr.strip()

            print(f"Speedtest failed: {error_message}")
            return

        data = json.loads(result.stdout)
        
        server_info = data.get('server', {})
        server_id = server_info.get('id')
        server_name = server_info.get('name')

        with sqlite3.connect(DATABASE) as conn:
            conn.execute('''
                INSERT INTO speedtest_results 
                (timestamp, download, upload, ping, url, server_id, server_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                datetime.now(),
                data['download']['bandwidth'] / 125000,
                data['upload']['bandwidth'] / 125000,
                data['ping']['latency'],
                data['result']['url'],
                server_id,
                server_name
            ))
            conn.commit()
            
        print("Automatic speedtest completed and results saved to the database.")
        
    except Exception as e:
        print(f"An error occurred during automatic speedtest: {e}")
    finally:
        remove_lockfile()


def get_next_run_time():
    jobs = scheduler.get_jobs()
    if jobs:
        next_run_time = jobs[0].next_run_time
        return next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    return "Automatic tests are disabled."

def print_scheduler_state():
    print(f"Scheduler running: {scheduler.running}")
    print(f"Executor running: {scheduler._executors['default']._pool is not None}")
    for job in scheduler.get_jobs():
        print(f"Job ID: {job.id}, Next Run: {job.next_run_time}, Interval: {job.trigger.interval}")
logging.basicConfig()
logging.getLogger('apscheduler').setLevel(logging.DEBUG)


def update_scheduler_interval(new_interval, server_id=None):
    scheduler.shutdown(wait=False) 

    try:
        for job in scheduler.get_jobs():
            scheduler.remove_job(job_id=job.id)
    except:
        pass
    if scheduler.get_job(job_id="speed_test"):
        scheduler.remove_job(job_id="speed_test") 
    time.sleep(1)
    clear_and_reinitialize_executor()
    scheduler.add_job(
        speed_test,
        'interval',
        seconds=new_interval,
        id='speed_test',
        args=[server_id] if server_id else []
    )
    print(f"Modified job with interval {new_interval} seconds and server_id {server_id}")
    print("Current jobs after modification:")
    for job in scheduler.get_jobs():
        print(f"Job ID: {job.id}, Next Run: {job.next_run_time}, Interval: {job.trigger.interval}")
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('''
            UPDATE settings
            SET interval = ?, server_id = ?
            WHERE id = 1
        ''', (new_interval, server_id))
        conn.commit()
    print("Updated settings in the database")

def load_settings_from_db():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT interval, server_id FROM settings WHERE id = 1')
        row = cursor.fetchone()
        return {
            "interval": row[0] if row else DEFAULT_INTERVAL,
            "server_id": row[1] if row else None
        }

settings = load_settings_from_db()

scheduler.add_job(
            speed_test,
            'interval',
            seconds=settings["interval"],
            id='speed_test',
            args=[settings["server_id"]] if settings["server_id"] else []
        )

@app.route("/", methods=["GET", "POST"])
def speedtest():
    next_run_time = get_next_run_time()

    if request.method == "POST":
        last_test_time = get_last_test_time()
        current_time = datetime.now()

        print(f"Last test time: {last_test_time}")
        print(f"Current time: {current_time}")

        if last_test_time:
            time_since_last_test = (current_time - last_test_time).total_seconds()
            print(f"Time since last test: {time_since_last_test} seconds")
            if time_since_last_test < COOLDOWN_PERIOD:
                cooldown_remaining = int(COOLDOWN_PERIOD - time_since_last_test)
                return jsonify({"error": f"Please wait {cooldown_remaining} seconds before running another test."}), 429
                
        if is_locked():
            return jsonify({"waiting": True})
        create_lockfile()

        try:
            server_id = request.form.get('server_id')
            command = [
                "speedtest",
                "--accept-gdpr",
                "--accept-license",
                "--format=json"
            ]
            
            if server_id and server_id.isdigit():
                command.extend(["-s", str(server_id)])

            result = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            if result.returncode != 0:
                try:
                    error_data = json.loads(result.stderr)
                    error_message = error_data.get("message", "Speedtest failed: Unknown error")
                except json.JSONDecodeError:
                    error_message = result.stderr.strip()

                print(f"Speedtest failed: {error_message}")
                return jsonify({"error": error_message}), 500

            data = json.loads(result.stdout)

            server_info = data.get('server', {})
            server_id = server_info.get('id')
            server_name = server_info.get('name')

            with sqlite3.connect(DATABASE) as conn:
                conn.execute('''
                    INSERT INTO speedtest_results 
                    (timestamp, download, upload, ping, url, server_id, server_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    current_time,
                    data['download']['bandwidth'] / 125000,
                    data['upload']['bandwidth'] / 125000,
                    data['ping']['latency'],
                    data['result']['url'],
                    server_id,
                    server_name
                ))
                conn.commit()
            update_last_test_time()
                
            return jsonify({"redirect": "/results", "output": data})
            
        except subprocess.CalledProcessError as e:
            error_message = f"Speedtest failed: {e.stderr}"
            print(error_message)
            return jsonify({"error": error_message}), 500
        except json.JSONDecodeError as e:
            error_message = f"Failed to parse speedtest output: {str(e)}"
            print(error_message)
            return jsonify({"error": error_message}), 500
        except Exception as e:
            error_message = f"An unexpected error occurred: {str(e)}"
            print(error_message)
            return jsonify({"error": error_message}), 500
        finally:
            remove_lockfile()

    servers = get_servers() 
    current_settings = load_settings_from_db()
    return render_template_string(HTML, servers=servers, current_server_id=current_settings["server_id"], next_run_time=next_run_time)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    client_ip = request.remote_addr
    app.logger.info(f"Real client IP: {client_ip}")
    next_run_time = get_next_run_time()
    jobs = scheduler.get_jobs()
    if not jobs:
        print("No jobs scheduled.")
    else:
        for job in jobs:
            print(f"Job ID: {job.id}, Next Run: {job.next_run_time}, Interval: {job.trigger.interval}")
    if request.method == "POST":
        if client_ip != "192.168.1.100":
            error_message = "Error: Not authorised"
            current_settings = load_settings_from_db()

            return render_template_string(
                SETTINGS_HTML,
                current_interval=current_settings["interval"],
                current_server_id=current_settings["server_id"],
                next_run_time=next_run_time,
                error_message=error_message
            )
        new_interval = int(request.form.get("interval"))
        server_id = request.form.get("server_id")
        next_run_time = get_next_run_time()


        if new_interval < 300:
            error_message = "Interval must be at least 5 minutes (300 seconds)."
            current_settings = load_settings_from_db()
            return render_template_string(SETTINGS_HTML, current_interval=current_settings["interval"], current_server_id=current_settings["server_id"], next_run_time=next_run_time, error_message=error_message)
        update_scheduler_interval(new_interval, server_id)
        return redirect(f"{url_for('settings')}")
    
    current_settings = load_settings_from_db()
    return render_template_string(SETTINGS_HTML, current_interval=current_settings["interval"], current_server_id=current_settings["server_id"], next_run_time=next_run_time)


@app.route("/results")
def results():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM speedtest_results ORDER BY timestamp DESC LIMIT 10')
        rows = cursor.fetchall()
    next_run_time = get_next_run_time()
    labels = [row[1] for row in rows]
    download = [row[2] for row in rows]
    upload = [row[3] for row in rows]
    ping = [row[4] for row in rows]
    return render_template_string(RESULTS_HTML, labels=labels, download=download, upload=upload, ping=ping, all_results=rows,next_run_time=next_run_time)

@app.route("/check_lock")
def check_lock():
    return jsonify({"locked": is_locked()})


HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Speedtest CLI Web GUI</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .hidden { display: none; }
        .server-select { display: flex; gap: 10px; margin-bottom: 15px; }
        .custom-server { flex-grow: 1; }
    </style>
</head>
<body class="bg-light">
    <div class="container mt-5">
        <div class="card shadow">
            <div class="card-header bg-primary text-white">
                <h1 class="card-title">Speedtest CLI Web GUI</h1>
            </div>
            <div class="card-body">
                <form id="speedtestForm" method="POST">
                    <div class="server-select">
                        <select class="form-select" id="serverSelect">
                            <option value="">Select a server...</option>
                            {% for server in servers %}
                            <option value="{{ server.id }}" {% if current_server_id == server.id %}selected{% endif %}>{{ server.sponsor }} ({{ server.name }}, {{ server.country }})</option>
                            {% endfor %}
                        </select>
                        <input 
                               class="form-control custom-server" 
                               placeholder="Or enter custom Server ID" 
                               id="customServer"
                               value="{{ current_server_id if current_server_id else '' }}">
                    </div>
                    <button type="submit" class="btn btn-success">Run Speedtest</button>
                </form>
                <div id="testingMessage" class="mt-3 hidden">
                    <div class="alert alert-info">Testing... Please wait.</div>
                </div>
                <div id="error" class="mt-3 hidden">
                <div id="ErrorMessage" class="alert alert-danger" role="alert">
                    <!-- Error message will be inserted here -->
                </div>
                
                            </div>
                <div id="waitingMessage" class="mt-3 hidden">
                    <div class="alert alert-warning">A speedtest is already in progress. Waiting for it to finish...</div>
                </div>
            </div>
            <div class="card-footer">
                <a href="{{ url_for('results') }}" class="btn btn-secondary">View Results</a>
                <a href="{{ url_for('settings') }}" class="btn btn-secondary">Settings</a>
                    <div class="mt-3">
        <strong>Next Automatic Test:</strong> {{ next_run_time }}
    </div>
            </div>
        </div>
    </div>
    
    <script>
        document.getElementById('speedtestForm').addEventListener('submit', function(event) {
    event.preventDefault();  

    document.getElementById('testingMessage').classList.remove('hidden');
    document.getElementById('waitingMessage').classList.add('hidden');
    document.getElementById('error').classList.add('hidden');

    const serverSelect = document.getElementById('serverSelect');
    const customServer = document.getElementById('customServer');
    const serverId = customServer.value || serverSelect.value;

    fetch("{{ url_for('speedtest') }}", {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({ server_id: serverId })
    })
    .then(response => response.json())
    .then(data => {
        if (data.redirect) {
            window.location.href = data.redirect; 
        } else if (data.waiting) {
            document.getElementById('testingMessage').classList.add('hidden');
            document.getElementById('waitingMessage').classList.remove('hidden');
            checkLock();
        } else if (data.error) {
            document.getElementById('testingMessage').classList.add('hidden');
            document.getElementById('ErrorMessage').innerText = data.error;
            document.getElementById('error').classList.remove('hidden');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        document.getElementById('testingMessage').classList.add('hidden');
        document.getElementById('ErrorMessage').innerText = "An unexpected error occurred."; 
        document.getElementById('error').classList.remove('hidden');  
    });
});

        function checkLock() {
            fetch("{{ url_for('check_lock') }}")
            .then(response => response.json())
            .then(data => {
                if (!data.locked) {
                window.location.href = "/results";
                } else {
                    setTimeout(checkLock, 1000);  
                }
            });
        }
    </script>
</body>
</html>
"""

SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Settings</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
    <div class="container mt-5">
        <div class="card shadow">
            <div class="card-header bg-primary text-white">
                <h1 class="card-title">Settings</h1>
            </div>
            <div class="card-body">
                {% if error_message %}
                <div class="alert alert-danger" role="alert">
                    {{ error_message }}
                </div>
                {% endif %}
                <form id="settingsForm" method="POST" action="{{ url_for('settings') }}">
                    <div class="form-group">
                        <label for="interval">Interval between tests (in seconds):</label>
                        <input type="number" class="form-control" id="interval" name="interval" value="{{ current_interval }}" required>
                    </div>
                    <div class="form-group">
                        <label for="server_id">Predefined Server ID (optional):</label>
                        <input type="number" class="form-control" id="server_id" name="server_id" placeholder="Enter Server ID" value="{{ current_server_id if current_server_id else '' }}">
                    </div>
                    <button type="submit" class="btn btn-success mt-3">Save</button>
                </form>
            </div>
            <div class="card-footer">
                <a href="{{ url_for('speedtest') }}" class="btn btn-secondary">Back to Speedtest</a>
                <a href="{{ url_for('results') }}" class="btn btn-secondary">Results</a>
                <div class="mt-3">
                    <strong>Next Automatic Test:</strong> {{ next_run_time }}
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

RESULTS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Speedtest Results</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-light">
    <div class="container mt-5">
        <div class="card shadow">
            <div class="card-header bg-primary text-white">
                <h1 class="card-title">Speedtest Results</h1>
            </div>
            <div class="card-body">
                <canvas id="speedtestChart" width="400" height="200"></canvas>
                <script>
                    var ctx = document.getElementById('speedtestChart').getContext('2d');
                    var chart = new Chart(ctx, {
                        type: 'line',
                        data: {
                            labels: {{ labels|tojson }},
                            datasets: [{
                                label: 'Download (Mbps)',
                                backgroundColor: 'rgba(255, 99, 132, 0.2)',
                                borderColor: 'rgba(255, 99, 132, 1)',
                                data: {{ download|tojson }},
                            }, {
                                label: 'Upload (Mbps)',
                                backgroundColor: 'rgba(54, 162, 235, 0.2)',
                                borderColor: 'rgba(54, 162, 235, 1)',
                                data: {{ upload|tojson }},
                            }, {
                                label: 'Ping (ms)',
                                backgroundColor: 'rgba(75, 192, 192, 0.2)',
                                borderColor: 'rgba(75, 192, 192, 1)',
                                data: {{ ping|tojson }},
                            }]
                        },
                        options: {
                            scales: {
                                y: {
                                    beginAtZero: true
                                }
                            }
                        }
                    });
                </script>
                <h2 class="mt-4">All Results</h2>
                                <div class="table-responsive">

                <table class="table table-striped">
                    <thead>
                        <tr>
                            <th>Timestamp</th>
                            <th>Download (Mbps)</th>
                            <th>Upload (Mbps)</th>
                            <th>Ping (ms)</th>
                            <th>Server ID</th>
                            <th>Server Name</th>
                            <th>URL</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for row in all_results %}
                        <tr>
                            <td>{{ row[1] }}</td>
                            <td>{{ "%.2f"|format(row[2]) }}</td>
                            <td>{{ "%.2f"|format(row[3]) }}</td>
                            <td>{{ "%.2f"|format(row[4]) }}</td>
                            <td>{{ row[6] }}</td>
                            <td>{{ row[7] }}</td>
                            <td><a href="{{ row[5] }}" target="_blank" class="btn btn-link">View</a></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            </div>
            <div class="card-footer">
                <a href="{{ url_for('speedtest') }}" class="btn btn-secondary">Back to Speedtest</a>
                <a href="{{ url_for('settings') }}" class="btn btn-secondary">Settings</a>
                    <div class="mt-3">
        <strong>Next Automatic Test:</strong> {{ next_run_time }}
    </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5008, debug=True)
