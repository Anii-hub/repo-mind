import logging
from pathlib import Path

from django.conf import settings


logger = logging.getLogger(__name__)

# Chunk settings — these directly control how many embeddings are generated.
# Larger CHUNK_SIZE = fewer chunks = fewer neural network passes = faster indexing.
# CHUNK_OVERLAP keeps context across chunk boundaries; 50 chars is enough for code.
# Step between chunks = CHUNK_SIZE - CHUNK_OVERLAP = 1950 chars (was 1000).
# Effect: ~2x fewer chunks for the same codebase → ~2x faster embedding.
CHUNK_SIZE    = 2000   # was 1200
CHUNK_OVERLAP = 50     # was 200
COLLECTION_NAME = "repository_chunks"

_embedding_model = None
_chroma_client = None


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedding_model


def get_collection():
    global _chroma_client
    if _chroma_client is None:
        import chromadb

        chroma_path = Path(settings.BASE_DIR) / "chroma_db"
        _chroma_client = chromadb.PersistentClient(path=str(chroma_path))
    return _chroma_client.get_or_create_collection(name=COLLECTION_NAME)


def index_repository_file(repository_file):
    chunks = chunk_text(repository_file.content)
    if not chunks:
        return

    model = get_embedding_model()
    embeddings = model.encode(chunks).tolist()
    collection = get_collection()

    ids = [
        f"repo-{repository_file.repository_id}-file-{repository_file.id}-chunk-{index}"
        for index in range(len(chunks))
    ]
    metadatas = [
        {
            "repository_id": repository_file.repository_id,
            "file_id": repository_file.id,
            "path": repository_file.path,
            "chunk_index": index,
        }
        for index in range(len(chunks))
    ]

    collection.upsert(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadatas,
    )


def retrieve_relevant_chunks(repository, query, limit=5):
    """Return the most relevant code chunks for a query.

    Bug 3 fix: ChromaDB raises InvalidArgumentError when n_results exceeds the
    number of documents in the collection. We count available docs first and
    clamp the limit before querying.
    """
    model = get_embedding_model()
    query_embedding = model.encode([query]).tolist()[0]
    collection = get_collection()

    try:
        # Count how many chunks exist for this repository.
        # include=[] means "return IDs only" — no documents, embeddings, or metadata
        # are fetched, making this O(count) in ID space rather than O(N*doc_size).
        existing = collection.get(where={"repository_id": repository.id}, include=[])
        available = len(existing.get("ids", []))
        if available == 0:
            logger.debug("No indexed chunks found for repository %s.", repository.id)
            return []

        n_results = min(limit, available)

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where={"repository_id": repository.id},
        )
    except Exception as exc:
        logger.warning("ChromaDB query failed for repository %s: %s", repository.id, exc)
        return []

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    chunks = []
    for document, metadata in zip(documents, metadatas):
        path = metadata.get("path", "unknown")
        chunks.append(f"File: {path}\n{document}")

    return chunks


# Bug 14 fix: delete stale vectors before re-indexing a repository
def delete_repository_chunks(repository_id):
    """Remove all ChromaDB vectors associated with a repository."""
    try:
        collection = get_collection()
        # include=[] fetches IDs only — no documents or embeddings are loaded into RAM
        existing = collection.get(where={"repository_id": repository_id}, include=[])
        ids_to_delete = existing.get("ids", [])
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
            logger.info(
                "Deleted %d stale chunks for repository %s.",
                len(ids_to_delete),
                repository_id,
            )
    except Exception as exc:
        logger.warning(
            "Failed to delete ChromaDB chunks for repository %s: %s",
            repository_id,
            exc,
        )


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split text into overlapping chunks.

    Bug 2 fix: if overlap >= chunk_size, start never advances and the loop
    runs forever. We clamp effective_overlap to chunk_size - 1 so start
    always moves forward by at least 1 character.
    """
    clean_text = text.strip()
    if not clean_text:
        return []

    # Clamp overlap so every iteration is guaranteed to advance
    effective_overlap = min(overlap, chunk_size - 1)

    chunks = []
    start = 0

    while start < len(clean_text):
        end = min(start + chunk_size, len(clean_text))
        chunks.append(clean_text[start:end])
        next_start = end - effective_overlap
        # Safety guard: ensure forward progress even after clamping
        if next_start <= start:
            next_start = start + 1
        start = next_start

    return chunks
