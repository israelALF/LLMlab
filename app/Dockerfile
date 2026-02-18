FROM python:3.12-slim

WORKDIR /app

# Minimal OS deps (curl only for optional debugging)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8080

# ddtrace-run auto-instruments supported libs, and we add manual spans too.
CMD ["ddtrace-run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
