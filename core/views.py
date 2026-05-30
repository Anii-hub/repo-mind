import logging

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ChatForm, RegisterForm, RepositoryUploadForm
from .models import ChatMessage, Repository
from .services.groq_service import answer_repository_question
from .services.repository_processor import process_repository


logger = logging.getLogger(__name__)


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
        repository.save()

        try:
            process_repository(repository)
            messages.success(request, "Repository uploaded and processed successfully.")
        except Exception as exc:
            logger.error("Processing failed for repository %s: %s", repository.id, exc)
            repository.status = "failed"
            repository.error_message = str(exc)
            repository.save(update_fields=["status", "error_message", "updated_at"])
            messages.error(request, f"Repository uploaded, but processing failed: {exc}")

        return redirect("repository_detail", repository_id=repository.id)

    return render(request, "repositories/upload.html", {"form": form})


@login_required
def repository_detail_view(request, repository_id):
    repository = get_object_or_404(Repository, id=repository_id, user=request.user)
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
        try:
            process_repository(repository)
            messages.success(request, "Repository reprocessed successfully.")
        except Exception as exc:
            logger.error(
                "Reprocessing failed for repository %s: %s", repository.id, exc
            )
            repository.status = "failed"
            repository.error_message = str(exc)
            repository.save(update_fields=["status", "error_message", "updated_at"])
            messages.error(request, f"Reprocessing failed: {exc}")

    return redirect("repository_detail", repository_id=repository.id)
