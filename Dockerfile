# Use official Python runtime as a parent image
FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install system dependencies for MySQL client and general build tools
RUN apt-get update && apt-get install -y \
    pkg-config \
    default-libmysqlclient-dev \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the Django project
# (When running locally, this will be overridden by the docker-compose volume,
# but it's good practice to have it in the image for pure Docker deployments)
COPY backend/ /app/
COPY backend/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Expose port 8000
EXPOSE 8000

# Run the application with Gunicorn
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "dba_tracker.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
