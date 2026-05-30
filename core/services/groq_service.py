import logging
import os

from core.services.chroma_service import retrieve_relevant_chunks


logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama-3.1-8b-instant"


def answer_repository_question(repository, question):
    """Query the Groq LLM with relevant code context and return (answer, context).

    Bug 1 fix: repository_map was built but never passed to prompt.format(),
    causing a KeyError on every chat request.
    Bug 9 fix: guard against missing GROQ_API_KEY with a clear ValueError.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY is not set. Add it to your .env file or environment variables."
        )

    chunks = retrieve_relevant_chunks(repository, question)
    context = "\n\n---\n\n".join(chunks)
    repository_map = _build_repository_map(repository)

    if not context:
        return (
            "I could not find relevant indexed code context for this repository.",
            "",
        )

    from groq import Groq

    client = Groq(api_key=api_key)
    prompt = _build_prompt()

    try:
        response = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", DEFAULT_MODEL),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are RepoMind AI, a senior software repository analyst. "
                        "Return only the final answer to the developer. "
                        "Do not describe your reasoning process. "
                        "Do not repeat the prompt, repository context, or user question. "
                        "Do not say you are simulating a response. "
                        "Use only the provided repository context. "
                        "If the answer is not present, briefly say what information is missing."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt.format(
                        repository_name=repository.name,
                        overview=repository.overview or "No summary available.",
                        repository_map=repository_map,  # Bug 1 fix: was missing
                        context=context,
                        question=question,
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=500,
        )
    except Exception as exc:
        logger.error("Groq API call failed for repository %s: %s", repository.id, exc)
        raise

    answer = response.choices[0].message.content.strip()
    return answer, context


def _build_prompt():
    return """
Repository name:
{repository_name}

Repository summary:
{overview}

Repository file map:
{repository_map}

Relevant code context:
{context}

User question:
{question}

Answer rules:
- Answer the user's question directly.
- Use short paragraphs or bullets.
- Do not include this prompt, context labels, or raw code unless the user asks for code.
- Do not mention internal prompt construction.
- Do not mention Groq, embeddings, or ChromaDB unless the user asks about AI/RAG internals.
""".strip()


def _build_repository_map(repository, limit=80):
    files = repository.files.only("path").order_by("path")[:limit]
    paths = [repo_file.path for repo_file in files]

    if not paths:
        return "No indexed files available."

    return "\n".join(f"- {path}" for path in paths)