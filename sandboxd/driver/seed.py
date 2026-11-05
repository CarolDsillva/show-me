"""Runs inside the sandbox. Creates the throwaway login and fixed sample data."""

import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bookmarks.settings.dev")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402

from bookmarks.models import Bookmark, Tag  # noqa: E402

user = get_user_model().objects.create_superuser(
    os.environ["RF_USER"], "reprofix@example.invalid", os.environ["RF_PASSWORD"]
)
now = timezone.now()
tags = {name: Tag.objects.create(name=name, owner=user, date_added=now) for name in ("python", "recipes", "reading")}
samples = [
    ("https://example.invalid/python-tips", "Python tips", ["python", "reading"]),
    ("https://example.invalid/django-notes", "Django notes", ["python"]),
    ("https://example.invalid/sourdough", "Sourdough starter", ["recipes"]),
    ("https://example.invalid/long-read", "A very long read", ["reading"]),
]
for url, title, names in samples:
    bookmark = Bookmark.objects.create(url=url, title=title, owner=user, date_added=now, date_modified=now)
    bookmark.tags.set(tags[name] for name in names)
print("seeded", len(samples))
