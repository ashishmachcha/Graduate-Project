from django.contrib import admin
from .models import Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "user", "created_at")
    list_filter = ("user",)
    search_fields = ("name", "slug")
    raw_id_fields = ("user",)
