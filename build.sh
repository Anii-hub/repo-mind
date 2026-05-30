#!/usr/bin/env bash
set -o errexit

# Install Python dependencies
pip install -r requirements.txt

# Collect static assets
python manage.py collectstatic --no-input

# Apply any pending database migrations
python manage.py migrate

