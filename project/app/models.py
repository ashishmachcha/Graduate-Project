from pathlib import Path

from django.conf import settings
from django.db import models


class Workspace(models.Model):
    """
    One workspace per user + slug. The folder on disk is:
    AI_WORKSPACE_DIR / user_id / slug /
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workspaces",
    )
    name = models.CharField(max_length=255, help_text="Display name for the project")
    slug = models.SlugField(
        max_length=100,
        help_text="Used as folder name (project_id). Only letters, numbers, underscore, hyphen.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [["user", "slug"]]

    def __str__(self):
        return f"{self.name} ({self.slug})"

    def get_workspace_path(self) -> Path:
        """Return the workspace directory path on disk."""
        base = getattr(settings, "AI_WORKSPACE_DIR", None) or Path("workspace")
        base = Path(base).resolve()
        return (base / str(self.user_id) / self.slug).resolve()

    def ensure_directory_exists(self) -> Path:
        """Create the workspace directory if it does not exist. Return the path."""
        path = self.get_workspace_path()
        path.mkdir(parents=True, exist_ok=True)
        return path
