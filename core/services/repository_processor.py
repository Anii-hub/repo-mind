import logging
import os
import shutil
import threading
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

IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "site-packages",
    "venv",
}
SUPPORTED_EXTENSIONS = {".py", ".js", ".java", ".html", ".css", ".sql"}
LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".java": "Java",
    ".html": "HTML",
    ".css": "CSS",
    ".sql": "SQL",
}

MAX_FILE_BYTES = int(os.getenv("REPOMIND_MAX_FILE_BYTES", 250 * 1024))
MAX_FILES_PER_REPO = int(os.getenv("REPOMIND_MAX_FILES", 150))
MAX_ZIP_MEMBERS = int(os.getenv("REPOMIND_MAX_ZIP_MEMBERS", 5000))
MAX_EXTRACTED_BYTES = int(os.getenv("REPOMIND_MAX_EXTRACTED_BYTES", 75 * 1024 * 1024))
EMBEDDING_BATCH_SIZE = int(os.getenv("REPOMIND_EMBEDDING_BATCH_SIZE", 16))
PROCESSING_LOCK = threading.Lock()


def process_repository(repository):
    with PROCESSING_LOCK:
        return _process_repository(repository)


def _process_repository(repository):
    repository.status = "processing"
    repository.error_message = ""
    repository.save(update_fields=["status", "error_message", "updated_at"])

    extract_root = None
    try:
        extract_root = _extract_repository(repository)
        repository.extracted_path = str(extract_root)

        delete_repository_chunks(repository.id)
        RepositoryFile.objects.filter(repository=repository).delete()

        language_counts = Counter()
        main_folders = set()
        pending_chunks = []
        saved_files = []
        skipped_file_limit = False
        index_errors = []

        for file_path in _iter_supported_files(extract_root):
            if len(saved_files) >= MAX_FILES_PER_REPO:
                skipped_file_limit = True
                break

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

            for idx, chunk in enumerate(chunk_text(content)):
                pending_chunks.append((repo_file, idx, chunk))
                if len(pending_chunks) >= EMBEDDING_BATCH_SIZE:
                    if not _index_chunk_batch(pending_chunks):
                        index_errors.append(f"indexing failed near {relative_path}")
                    pending_chunks.clear()

        if pending_chunks and not _index_chunk_batch(pending_chunks):
            index_errors.append("indexing failed in final batch")

        repository.total_files = len(saved_files)
        repository.languages = ", ".join(sorted(language_counts.keys()))
        repository.main_folders = ", ".join(sorted(main_folders))
        repository.overview = _build_overview(repository, language_counts)
        repository.status = "processed"

        warnings = []
        if skipped_file_limit:
            warnings.append(f"Only the first {MAX_FILES_PER_REPO} files were indexed.")
        if index_errors:
            warnings.append(f"Indexing warning: {'; '.join(index_errors)}")

        repository.error_message = " ".join(warnings)
        repository.save()
        return repository
    finally:
        if extract_root is not None:
            _cleanup_extracted_repository(extract_root)


def _extract_repository(repository):
    upload_path = Path(repository.zip_file.path)

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

    resolved_root = extract_root.resolve()
    with zipfile.ZipFile(upload_path, "r") as zip_ref:
        members = zip_ref.infolist()
        if len(members) > MAX_ZIP_MEMBERS:
            raise ValueError(
                f"ZIP contains too many entries ({len(members)}). "
                f"Maximum allowed is {MAX_ZIP_MEMBERS}."
            )

        total_uncompressed = 0
        members_to_extract = []
        for member in members:
            member_dest = (extract_root / member.filename).resolve()
            common_path = os.path.commonpath([str(resolved_root), str(member_dest)])
            if common_path != str(resolved_root):
                raise ValueError(
                    f"ZIP contains an unsafe path entry: {member.filename}"
                )
            if _is_ignored_archive_member(member.filename):
                continue
            if not member.is_dir():
                total_uncompressed += member.file_size
                if total_uncompressed > MAX_EXTRACTED_BYTES:
                    max_mb = MAX_EXTRACTED_BYTES // (1024 * 1024)
                    raise ValueError(
                        "ZIP expands to too much data. "
                        f"Maximum uncompressed size is {max_mb} MB."
                    )
            members_to_extract.append(member)

        for member in members_to_extract:
            zip_ref.extract(member, extract_root)

    return extract_root


def _iter_supported_files(root):
    for current_root, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS]

        for filename in files:
            file_path = Path(current_root) / filename
            if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                yield file_path


def _is_ignored_archive_member(filename):
    parts = Path(filename.replace("\\", "/")).parts
    return any(part.lower() in IGNORED_DIRS for part in parts)


def _read_text_file(file_path):
    try:
        size = file_path.stat().st_size
        if size > MAX_FILE_BYTES:
            logger.warning("Skipping oversized file (%d bytes): %s", size, file_path)
            return ""
        try:
            return file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return file_path.read_text(encoding="latin-1", errors="ignore")
    except OSError as exc:
        logger.warning("Could not read file %s: %s", file_path, exc)
        return ""


def _index_chunk_batch(chunk_batch):
    if not chunk_batch:
        return True

    try:
        model = get_embedding_model()
        collection = get_collection()

        documents = [chunk for _, _, chunk in chunk_batch]
        embeddings = model.encode(
            documents,
            batch_size=EMBEDDING_BATCH_SIZE,
            show_progress_bar=False,
        ).tolist()
        ids = [
            f"repo-{repo_file.repository_id}-file-{repo_file.id}-chunk-{chunk_index}"
            for repo_file, chunk_index, _ in chunk_batch
        ]
        metadatas = [
            {
                "repository_id": repo_file.repository_id,
                "file_id": repo_file.id,
                "path": repo_file.path,
                "chunk_index": chunk_index,
            }
            for repo_file, chunk_index, _ in chunk_batch
        ]

        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        return True
    except Exception as exc:
        logger.error("Chunk batch indexing failed: %s", exc)
        return False


def _cleanup_extracted_repository(extract_root):
    try:
        shutil.rmtree(extract_root)
    except OSError as exc:
        logger.warning("Could not remove extracted repository %s: %s", extract_root, exc)


def _build_overview(repository, language_counts):
    languages = ", ".join(sorted(language_counts.keys())) or "No supported languages detected"
    folders = repository.main_folders or "No top-level folders detected"
    return (
        f"{repository.name} contains {repository.total_files} supported code files. "
        f"Languages detected: {languages}. Main folders: {folders}."
    )
