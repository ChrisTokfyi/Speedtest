import os
import time
from flask import Flask, render_template_string, request, jsonify, url_for
import subprocess
import sqlite3
import json
import requests
from datetime import datetime

app = Flask(__name__)

# Configuration
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'speedtest.db')
LOCKFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'speedtest.lock')

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
        conn.commit()

init_db()

# Lockfile functions
def create_lockfile():
    with open(LOCKFILE, 'w') as f:
        f.write('locked')

def remove_lockfile():
    if os.path.exists(LOCKFILE):
        os.remove(LOCKFILE)

def is_locked():
    return os.path.exists(LOCKFILE)

# Fetch nearby servers
def get_servers():
    url = "https://www.speedtest.net/api/js/servers?engine=js&limit=10&https_functional=true"
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an error for bad responses (4xx, 5xx)
        servers = response.json()  # Parse JSON response
        return servers  # Return the list of servers, not wrapped in jsonify
    except requests.RequestException as e:
        return {"error": str(e)}  # Return error message if request fails


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
                            <option value="{{ server.id }}">{{ server.sponsor }} ({{ server.name }}, {{ server.country }})</option>
                            {% endfor %}
                        </select>
                        <input 
                               class="form-control custom-server" 
                               placeholder="Or enter custom Server ID" 
                               id="customServer">
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
            </div>
        </div>
    </div>
    <script>
        document.getElementById('speedtestForm').addEventListener('submit', function(event) {
    event.preventDefault();  // Prevent the form from submitting normally

    // Show the "Testing..." message and hide others
    document.getElementById('testingMessage').classList.remove('hidden');
    document.getElementById('waitingMessage').classList.add('hidden');
    document.getElementById('error').classList.add('hidden');

    // Get server ID
    const serverSelect = document.getElementById('serverSelect');
    const customServer = document.getElementById('customServer');
    const serverId = customServer.value || serverSelect.value;

    // Submit the form data using Fetch API
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
            window.location.href = data.redirect;  // Redirect to the results page
        } else if (data.waiting) {
            document.getElementById('testingMessage').classList.add('hidden');
            document.getElementById('waitingMessage').classList.remove('hidden');
            checkLock();
        } else if (data.error) {
            // Display the error message
            document.getElementById('testingMessage').classList.add('hidden');
            document.getElementById('ErrorMessage').innerText = data.error;  // Set the error message
            document.getElementById('error').classList.remove('hidden');  // Show the error message
        }
    })
    .catch(error => {
        console.error('Error:', error);
        document.getElementById('testingMessage').classList.add('hidden');
        document.getElementById('ErrorMessage').innerText = "An unexpected error occurred.";  // Set a generic error message
        document.getElementById('error').classList.remove('hidden');  // Show the error message
    });
});

        function checkLock() {
            fetch("{{ url_for('check_lock') }}")
            .then(response => response.json())
            .then(data => {
                if (!data.locked) {
                    window.location.href = "{{ url_for('results') }}";
                } else {
                    setTimeout(checkLock, 1000);  // Check again after 1 second
                }
            });
        }

        // Load servers dynamically

    </script>
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
            <div class="card-footer">
                <a href="{{ url_for('speedtest') }}" class="btn btn-secondary">Back to Speedtest</a>
            </div>
        </div>
    </div>
</body>
</html>
"""

'''@app.route("/get_serversx")
def server_list():
    servers = get_servers()
    return jsonify(servers)
'''
@app.route("/", methods=["GET", "POST"])
def speedtest():
    if request.method == "POST":
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

            # Check if the command failed
            if result.returncode != 0:
                try:
                    error_data = json.loads(result.stderr)
                    error_message = error_data.get("message", "Speedtest failed: Unknown error")
                except json.JSONDecodeError:
                    # If stderr is not JSON, use the raw error message
                    error_message = result.stderr.strip()

                print(f"Speedtest failed: {error_message}")
                return jsonify({"error": error_message}), 500

            # Parse the JSON output
            data = json.loads(result.stdout)
            
            # Extract server info
            server_info = data.get('server', {})
            server_id = server_info.get('id')
            server_name = server_info.get('name')

            # Update database insert
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
                
            return jsonify({"redirect": url_for('results'), "output": data})
            
        except subprocess.CalledProcessError as e:
            # Handle subprocess errors (e.g., invalid server ID)
            error_message = f"Speedtest failed: {e.stderr}"
            print(error_message)
            return jsonify({"error": error_message}), 500
        except json.JSONDecodeError as e:
            # Handle JSON parsing errors
            error_message = f"Failed to parse speedtest output: {str(e)}"
            print(error_message)
            return jsonify({"error": error_message}), 500
        except Exception as e:
            # Handle all other exceptions
            error_message = f"An unexpected error occurred: {str(e)}"
            print(error_message)
            return jsonify({"error": error_message}), 500
        finally:
            remove_lockfile()
    servers = get_servers()  # Get the list of servers
    return render_template_string(HTML, servers=servers)

@app.route("/results")
def results():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM speedtest_results ORDER BY timestamp DESC LIMIT 10')
        rows = cursor.fetchall()
    labels = [row[1] for row in rows]
    download = [row[2] for row in rows]
    upload = [row[3] for row in rows]
    ping = [row[4] for row in rows]
    return render_template_string(RESULTS_HTML, labels=labels, download=download, upload=upload, ping=ping, all_results=rows)

@app.route("/check_lock")
def check_lock():
    return jsonify({"locked": is_locked()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)