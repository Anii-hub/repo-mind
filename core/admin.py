from django.contrib import admin

from .models import ChatMessage, Repository, RepositoryFile


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "status", "total_files", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "user__username")


@admin.register(RepositoryFile)
class RepositoryFileAdmin(admin.ModelAdmin):
    list_display = ("path", "repository", "extension", "size")
    list_filter = ("extension",)
    search_fields = ("path", "repository__name")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("repository", "user", "created_at")
    search_fields = ("question", "answer", "repository__name", "user__username")
