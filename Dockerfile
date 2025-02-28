FROM ubuntu:22.04
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y curl gnupg python3 python3-pip python3-requests && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
ENV FLASK_APP=app.py
ENV FLASK_ENV=production
RUN curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash && \
    apt install -y speedtest
CMD ["flask", "run", "--host=0.0.0.0"]
