# PDD web app: Django + AI agent. Run with docker-compose so the app can use Docker for runtimes.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install deps (project folder has requirements at repo root)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Django project (project/ is the app root with manage.py)
COPY project/ /app/project/
WORKDIR /app/project

# Create workspace dir so it exists
RUN mkdir -p workspace

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
