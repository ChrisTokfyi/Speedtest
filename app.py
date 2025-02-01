from flask import Flask, render_template_string, request, redirect, url_for, jsonify
import subprocess
import sqlite3
import json
from datetime import datetime
import os
app = Flask(__name__)

# SQLite database setup
# DATABASE = 'speedtest.db'
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'speedtest.db')

def init_db():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS speedtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME,
                download REAL,
                upload REAL,
                ping REAL,
                url TEXT
            )
        ''')
        conn.commit()

init_db()

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
                    <button type="submit" class="btn btn-success">Run Speedtest</button>
                </form>
                <div id="testingMessage" class="mt-3 hidden">
                    <div class="alert alert-info">Testing... Please wait.</div>
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

            // Show the "Testing..." message
            document.getElementById('testingMessage').classList.remove('hidden');

            // Submit the form data using Fetch API
            fetch("{{ url_for('speedtest') }}", {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: new URLSearchParams(new FormData(event.target))
            })
            .then(response => response.json())
            .then(data => {
                if (data.redirect) {
                    window.location.href = data.redirect;  // Redirect to the results page
                }
            })
            .catch(error => {
                console.error('Error:', error);
            });
        });
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

@app.route("/", methods=["GET", "POST"])
def speedtest():
    if request.method == "POST":
        try:
            output = subprocess.check_output(["speedtest", "--accept-gdpr","--accept-license", "--format=json"], text=True)
            data = json.loads(output)
            url = data['result']['url']
            download_mbps = data['download']['bandwidth'] / 125000
            upload_mbps = data['upload']['bandwidth'] / 125000
            ping_ms = data['ping']['latency']
            with sqlite3.connect(DATABASE) as conn:
                conn.execute('''
                    INSERT INTO speedtest_results (timestamp, download, upload, ping, url)
                    VALUES (?, ?, ?, ?, ?)
                ''', (datetime.now(), download_mbps, upload_mbps, ping_ms, url))
                conn.commit()
            return jsonify({"redirect": url_for('results'),"output": data})
        except Exception as e:
            print(f"Error: {e}")
            return jsonify({"error": str(e)}), 500
    return render_template_string(HTML)

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
