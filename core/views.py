import logging
import threading
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import connection as db_connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import ChatForm, RegisterForm, RepositoryUploadForm
from .models import ChatMessage, Repository
from .services.groq_service import answer_repository_question
from .services.repository_processor import process_repository


logger = logging.getLogger(__name__)


def _run_processing_thread(repository_id):
    """
    Run process_repository in a background daemon thread.

    Each thread gets its own Django DB connection automatically.
    We close it explicitly when done so it is returned to the pool.
    """
    try:
        # Fetch fresh — this thread owns its own DB connection state
        repository = Repository.objects.get(pk=repository_id)
        process_repository(repository)
    except Repository.DoesNotExist:
        logger.error("Background thread: repository %s not found.", repository_id)
    except Exception as exc:
        logger.error(
            "Background processing failed for repository %s: %s", repository_id, exc
        )
        try:
            repo = Repository.objects.get(pk=repository_id)
            repo.status = "failed"
            repo.error_message = str(exc)
            repo.save(update_fields=["status", "error_message", "updated_at"])
        except Exception:
            pass
    finally:
        # Return the thread-local DB connection to the pool
        db_connection.close()


def register_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Account created successfully.")
        return redirect("dashboard")

    return render(request, "auth/register.html", {"form": form})


@login_required
def dashboard_view(request):
    repositories = Repository.objects.filter(user=request.user)
    return render(request, "repositories/dashboard.html", {"repositories": repositories})


@login_required
def repository_upload_view(request):
    form = RepositoryUploadForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        repository = form.save(commit=False)
        repository.user = request.user
        repository.status = "processing"
        repository.save()

        # Fire processing in a background thread so the HTTP response
        # returns immediately. The detail page auto-refreshes every 5 s
        # while status == "processing".
        thread = threading.Thread(
            target=_run_processing_thread,
            args=(repository.id,),
            daemon=True,
        )
        thread.start()

        messages.info(
            request,
            "Repository uploaded! Processing is running in the background \u2014 "
            "this page will refresh automatically.",
        )
        return redirect("repository_detail", repository_id=repository.id)

    return render(request, "repositories/upload.html", {"form": form})


@login_required
def repository_detail_view(request, repository_id):
    repository = get_object_or_404(Repository, id=repository_id, user=request.user)

    # Stuck-processing guard: if the thread was killed (e.g. Render dyno sleep
    # wiped the process mid-run), the status stays "processing" forever.
    # Detect this by checking if updated_at is older than 30 minutes.
    PROCESSING_TIMEOUT = timedelta(minutes=30)
    if repository.status == "processing" and repository.updated_at:
        if timezone.now() - repository.updated_at > PROCESSING_TIMEOUT:
            repository.status = "failed"
            repository.error_message = (
                "Processing timed out after 30 minutes. The server may have restarted "
                "mid-run. Please use the Retry button to reprocess."
            )
            repository.save(update_fields=["status", "error_message", "updated_at"])

    files = repository.files.all()[:100]
    return render(
        request,
        "repositories/detail.html",
        {
            "repository": repository,
            "files": files,
        },
    )


@login_required
def repository_status_api(request, repository_id):
    """Lightweight JSON endpoint polled by the detail page while processing.

    Returns only the fields the JavaScript poller needs — no HTML rendering.
    Also applies the same stuck-processing guard as the detail view.
    """
    repository = get_object_or_404(Repository, id=repository_id, user=request.user)

    PROCESSING_TIMEOUT = timedelta(minutes=30)
    if repository.status == "processing" and repository.updated_at:
        if timezone.now() - repository.updated_at > PROCESSING_TIMEOUT:
            repository.status = "failed"
            repository.error_message = (
                "Processing timed out. Please retry."
            )
            repository.save(update_fields=["status", "error_message", "updated_at"])

    return JsonResponse({
        "status": repository.status,
        "status_display": repository.get_status_display(),
        "total_files": repository.total_files,
        "languages": repository.languages,
        "error_message": repository.error_message,
    })


@login_required
def repository_chat_view(request, repository_id):
    repository = get_object_or_404(Repository, id=repository_id, user=request.user)

    # Bug 8: block chat until the repository is fully processed
    if repository.status != "processed":
        messages.warning(
            request,
            "This repository must finish processing before you can chat with it.",
        )
        return redirect("repository_detail", repository_id=repository.id)

    form = ChatForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        question = form.cleaned_data["question"]
        try:
            answer, context = answer_repository_question(repository, question)
        except Exception as exc:
            logger.error(
                "Chat answer failed for repository %s: %s", repository.id, exc
            )
            messages.error(request, f"Could not get an answer: {exc}")
            return redirect("repository_chat", repository_id=repository.id)

        ChatMessage.objects.create(
            repository=repository,
            user=request.user,
            question=question,
            answer=answer,
            context=context,
        )
        return redirect("repository_chat", repository_id=repository.id)

    messages_qs = repository.chat_messages.select_related("user")
    return render(
        request,
        "chat/chat.html",
        {
            "repository": repository,
            "form": form,
            "chat_messages": messages_qs,
        },
    )


# Bug 6: add delete view (was entirely missing)
@login_required
def repository_delete_view(request, repository_id):
    repository = get_object_or_404(Repository, id=repository_id, user=request.user)

    if request.method == "POST":
        from .services.chroma_service import delete_repository_chunks

        name = repository.name
        delete_repository_chunks(repository.id)
        repository.delete()
        messages.success(request, f'Repository "{name}" has been deleted.')
        return redirect("dashboard")

    return render(
        request,
        "repositories/delete_confirm.html",
        {"repository": repository},
    )


# Bug 7: add reprocess view so failed repos can be retried without re-uploading
@login_required
def repository_reprocess_view(request, repository_id):
    repository = get_object_or_404(Repository, id=repository_id, user=request.user)

    if request.method == "POST":
        # Mark as processing right away so the UI shows a spinner
        repository.status = "processing"
        repository.error_message = ""
        repository.save(update_fields=["status", "error_message", "updated_at"])

        thread = threading.Thread(
            target=_run_processing_thread,
            args=(repository.id,),
            daemon=True,
        )
        thread.start()

        messages.info(
            request,
            "Reprocessing started in the background — "
            "this page will refresh automatically.",
        )

    return redirect("repository_detail", repository_id=repository.id)
