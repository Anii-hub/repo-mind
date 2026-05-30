from django.conf import settings
from django.db import models


class Repository(models.Model):
    STATUS_CHOICES = (
        ("uploaded", "Uploaded"),
        ("processed", "Processed"),
        ("failed", "Failed"),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    zip_file = models.FileField(upload_to="repositories/zips/")
    extracted_path = models.CharField(max_length=500, blank=True)
    overview = models.TextField(blank=True)
    languages = models.CharField(max_length=500, blank=True)
    total_files = models.PositiveIntegerField(default=0)
    main_folders = models.CharField(max_length=1000, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="uploaded")
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class RepositoryFile(models.Model):
    repository = models.ForeignKey(
        Repository,
        related_name="files",
        on_delete=models.CASCADE,
    )
    path = models.CharField(max_length=1000)
    extension = models.CharField(max_length=20)
    content = models.TextField(blank=True)
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["path"]
        unique_together = ("repository", "path")

    def __str__(self):
        return self.path


class ChatMessage(models.Model):
    repository = models.ForeignKey(
        Repository,
        related_name="chat_messages",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    question = models.TextField()
    answer = models.TextField()
    context = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.repository.name}: {self.question[:50]}"
