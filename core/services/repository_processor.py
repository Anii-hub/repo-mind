import logging
import os
import shutil
import zipfile
from collections import Counter
from pathlib import Path

from django.conf import settings

from core.models import RepositoryFile
from core.services.chroma_service import delete_repository_chunks, index_repository_file


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

# Bug 12: cap per-file content reads to avoid loading huge files into memory
MAX_FILE_BYTES = 500 * 1024  # 500 KB


def process_repository(repository):
    extract_root = _extract_repository(repository)
    repository.extracted_path = str(extract_root)

    # Bug 14: remove stale ChromaDB vectors from any previous run before re-indexing
    delete_repository_chunks(repository.id)
    RepositoryFile.objects.filter(repository=repository).delete()

    saved_files = []
    language_counts = Counter()
    main_folders = set()
    index_errors = []  # Bug 13: collect per-file indexing errors instead of aborting

    for file_path in _iter_supported_files(extract_root):
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

        # Bug 13: catch per-file indexing errors so one bad file doesn't abort the whole run
        try:
            index_repository_file(repo_file)
        except Exception as exc:
            logger.warning("Could not index %s: %s", relative_path, exc)
            index_errors.append(relative_path)

    repository.total_files = len(saved_files)
    repository.languages = ", ".join(sorted(language_counts.keys()))
    repository.main_folders = ", ".join(sorted(main_folders))
    repository.overview = _build_overview(repository, language_counts)
    repository.status = "processed"

    if index_errors:
        sample = ", ".join(index_errors[:5])
        suffix = f" (and {len(index_errors) - 5} more)" if len(index_errors) > 5 else ""
        repository.error_message = (
            f"Warning: {len(index_errors)} file(s) could not be indexed: {sample}{suffix}"
        )
    else:
        repository.error_message = ""

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
