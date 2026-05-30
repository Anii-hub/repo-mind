# RepoMind AI

Minimal Django RAG app for repository intelligence.

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Create environment variables or copy `.env.example` values into your shell:

```bash
set GROQ_API_KEY=your-groq-api-key
```

Run database setup:

```bash
python manage.py migrate
python manage.py createsuperuser
```

Start the app:

```bash
python manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

## Notes

- Upload repositories as ZIP files.
- Supported files: `.py`, `.js`, `.java`, `.html`, `.css`, `.sql`.
- Ignored folders: `.git`, `node_modules`, `__pycache__`, `build`, `dist`.
- ChromaDB data is stored in `chroma_db/`.
- SQLite database is created as `db.sqlite3`.
