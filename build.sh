#!/usr/bin/env bash
set -o errexit

# Install Python dependencies
pip install -r requirements.txt

# Pre-download the sentence-transformer embedding model at build time.
# This caches the model (~90 MB) in the build image so it is available
# immediately at startup instead of being downloaded on the first upload request,
# which would block the worker for 30+ seconds and risk a timeout.
python -c "
from sentence_transformers import SentenceTransformer
print('Downloading sentence-transformer model...')
SentenceTransformer('all-MiniLM-L6-v2')
print('Model downloaded and cached successfully.')
"

# Collect static assets
python manage.py collectstatic --no-input

# Apply any pending database migrations
python manage.py migrate
