from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.index),
    path("home/", views.home_view, name="home"),
    path("signup/", views.signup_view, name="signup"),
    path("api/chat/", views.chat_api),
    path("api/workspaces/", views.workspace_list_api),
    path("api/workspaces/create/", views.workspace_create_api),
    path("api/workspaces/<slug:slug>/files/", views.workspace_files_api),
    path("api/workspaces/<slug:slug>/create-file/", views.workspace_create_file_api),
    path("api/workspaces/<slug:slug>/upload/", views.workspace_upload_file_api),
    path("api/workspaces/<slug:slug>/content/", views.workspace_file_content_api),
    path("api/workspaces/<slug:slug>/unlock/", views.workspace_unlock_api),
    path("api/workspaces/<slug:slug>/run/", views.workspace_run_api),
    path("login/", views.login_view, name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("ide/", views.ide_page, name="ide"),
]
