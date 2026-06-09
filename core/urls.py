from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("register/", views.register_view, name="register"),
    path("repositories/upload/", views.repository_upload_view, name="repository_upload"),
    path(
        "repositories/<int:repository_id>/",
        views.repository_detail_view,
        name="repository_detail",
    ),
    path(
        "repositories/<int:repository_id>/chat/",
        views.repository_chat_view,
        name="repository_chat",
    ),
    # Bug 6: delete route (was missing)
    path(
        "repositories/<int:repository_id>/delete/",
        views.repository_delete_view,
        name="repository_delete",
    ),
    # Bug 7: reprocess route (was missing)
    path(
        "repositories/<int:repository_id>/reprocess/",
        views.repository_reprocess_view,
        name="repository_reprocess",
    ),
    # Lightweight JSON endpoint polled by the detail page AJAX poller
    path(
        "repositories/<int:repository_id>/status.json",
        views.repository_status_api,
        name="repository_status_api",
    ),
]
