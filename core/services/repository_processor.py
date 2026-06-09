import logging
import os
import shutil
import zipfile
from collections import Counter
from pathlib import Path

from django.conf import settings

from core.models import RepositoryFile
from core.services.chroma_service import (
    chunk_text,
    delete_repository_chunks,
    get_collection,
    get_embedding_model,
)


logger = logging.getLogger(__name__)

IGNORED_DIRS = {".git", "node_modules", "__pycache__", "build", "dist"}
SUPPORTED_EXTENSIONS = {".py", ".js", ".java", ".html", ".css", ".sql"}
LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".java": "Java",
    ".html": "HTML",
    ".css": "CSS",
    ".sql": "SQL",
}

MAX_FILE_BYTES = 500 * 1024  # 500 KB per file
MAX_FILES_PER_REPO = 300     # cap total files indexed


def process_repository(repository):
    # Set status to "processing" immediately so the UI can show a spinner
    repository.status = "processing"
    repository.error_message = ""
    repository.save(update_fields=["status", "error_message", "updated_at"])

    extract_root = _extract_repository(repository)
    repository.extracted_path = str(extract_root)

    # Bug 14: remove stale ChromaDB vectors from any previous run before re-indexing
    delete_repository_chunks(repository.id)
    RepositoryFile.objects.filter(repository=repository).delete()

    language_counts = Counter()
    main_folders = set()

    # ── Pass 1: read files, save to DB, collect chunks ────────────────────────
    # We accumulate all chunks across all files so we can encode them in a
    # single model.encode() call (2-5x faster than encoding file-by-file).
    all_chunks = []          # flat list of text chunks across all files
    chunk_to_file = []       # parallel list: which RepositoryFile each chunk belongs to
    chunk_index_in_file = [] # parallel list: chunk position within that file
    file_chunk_counts = {}   # repo_file.id → number of chunks (for ID generation)
    saved_files = []

    supported_files = list(_iter_supported_files(extract_root))[:MAX_FILES_PER_REPO]

    for file_path in supported_files:
        relative_path = file_path.relative_to(extract_root).as_posix()
        content = _read_text_file(file_path)
        extension = file_path.suffix.lower()

        if not content.strip():
            continue

        parts = relative_path.split("/")
        if len(parts) > 1:
            main_folders.add(parts[0])

        repo_file = RepositoryFile.objects.create(
            repository=repository,
            path=relative_path,
            extension=extension,
            content=content,
            size=file_path.stat().st_size,
        )
        saved_files.append(repo_file)
        language_counts[LANGUAGE_MAP.get(extension, extension)] += 1

        chunks = chunk_text(content)
        if chunks:
            for idx, chunk in enumerate(chunks):
                all_chunks.append(chunk)
                chunk_to_file.append(repo_file)
                chunk_index_in_file.append(idx)

    # ── Pass 2: batch-encode all chunks at once, then upsert to ChromaDB ─────
    index_errors = []
    if all_chunks:
        try:
            model = get_embedding_model()

            # Encode all chunks in one call — sentence-transformers parallelises
            # internally; batch_size=32 is optimal for CPU (avoids OOM spikes).
            embeddings = model.encode(
                all_chunks,
                batch_size=32,
                show_progress_bar=False,
            ).tolist()

            collection = get_collection()
            ids = [
                f"repo-{chunk_to_file[i].repository_id}-file-{chunk_to_file[i].id}-chunk-{chunk_index_in_file[i]}"
                for i in range(len(all_chunks))
            ]
            metadatas = [
                {
                    "repository_id": chunk_to_file[i].repository_id,
                    "file_id": chunk_to_file[i].id,
                    "path": chunk_to_file[i].path,
                    "chunk_index": chunk_index_in_file[i],
                }
                for i in range(len(all_chunks))
            ]

            # Upsert in batches of 200 — ChromaDB’s SQLite backend handles
            # smaller transactions much faster than one giant write.
            UPSERT_BATCH = 200
            for start in range(0, len(all_chunks), UPSERT_BATCH):
                end = start + UPSERT_BATCH
                collection.upsert(
                    ids=ids[start:end],
                    documents=all_chunks[start:end],
                    embeddings=embeddings[start:end],
                    metadatas=metadatas[start:end],
                )

        except Exception as exc:
            logger.error("Batch indexing failed for repository %s: %s", repository.id, exc)
            index_errors.append("(batch embedding failed)")

    repository.total_files = len(saved_files)
    repository.languages = ", ".join(sorted(language_counts.keys()))
    repository.main_folders = ", ".join(sorted(main_folders))
    repository.overview = _build_overview(repository, language_counts)
    repository.status = "processed"

    warnings = []
    if len(supported_files) == MAX_FILES_PER_REPO and len(supported_files) < sum(
        1 for _ in _iter_supported_files(extract_root)
    ):
        warnings.append(f"Only the first {MAX_FILES_PER_REPO} files were indexed.")
    if index_errors:
        warnings.append(f"Indexing warning: {'; '.join(index_errors)}")

    repository.error_message = " ".join(warnings)
    repository.save()
    return repository



def _extract_repository(repository):
    upload_path = Path(repository.zip_file.path)

    # Bug 5: validate that the uploaded file is actually a ZIP before extracting
    if not zipfile.is_zipfile(upload_path):
        raise ValueError(
            "The uploaded file is not a valid ZIP archive. Please upload a .zip file."
        )

    extract_root = (
        Path(settings.MEDIA_ROOT) / "repositories" / "extracted" / str(repository.id)
    )

    if extract_root.exists():
        shutil.rmtree(extract_root)

    extract_root.mkdir(parents=True, exist_ok=True)

    # Bug 4: zip-slip path traversal protection — check every member before extracting
    resolved_root = extract_root.resolve()
    with zipfile.ZipFile(upload_path, "r") as zip_ref:
        for member in zip_ref.infolist():
            member_dest = (extract_root / member.filename).resolve()
            if not str(member_dest).startswith(str(resolved_root)):
                raise ValueError(
                    f"ZIP contains an unsafe (path-traversal) entry: {member.filename}"
                )
        zip_ref.extractall(extract_root)

    return extract_root


def _iter_supported_files(root):
    for current_root, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]

        for filename in files:
            file_path = Path(current_root) / filename
            if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                yield file_path


def _read_text_file(file_path):
    """Read a source file safely.

    Bug 12: skip files larger than MAX_FILE_BYTES to avoid OOM on minified bundles.
    Bug 5 (partial): also catch OSError/PermissionError, not just UnicodeDecodeError.
    """
    try:
        if file_path.stat().st_size > MAX_FILE_BYTES:
            logger.warning(
                "Skipping oversized file (%d bytes): %s",
                file_path.stat().st_size,
                file_path,
            )
            return ""
        try:
            return file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return file_path.read_text(encoding="latin-1", errors="ignore")
    except OSError as exc:
        logger.warning("Could not read file %s: %s", file_path, exc)
        return ""


def _build_overview(repository, language_counts):
    languages = ", ".join(sorted(language_counts.keys())) or "No supported languages detected"
    folders = repository.main_folders or "No top-level folders detected"
    return (
        f"{repository.name} contains {repository.total_files} supported code files. "
        f"Languages detected: {languages}. Main folders: {folders}."
    )
