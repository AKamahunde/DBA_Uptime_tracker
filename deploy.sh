#!/bin/bash
set -e

# Run this from the repo root on the server.
# Example: /opt/atlas/deploy.sh

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# Pull the latest code.
git pull origin main

# Create or activate the virtualenv.
if [ ! -d "$REPO_ROOT/venv" ]; then
  python3 -m venv "$REPO_ROOT/venv"
fi
source "$REPO_ROOT/venv/bin/activate"

# Install dependencies.
pip install --upgrade pip
pip install -r requirements.txt

# Run Django maintenance tasks.
cd "$REPO_ROOT/backend"
python manage.py migrate
python manage.py collectstatic --noinput

# Restart the service.
sudo systemctl restart atlas

echo "Deployment complete."
