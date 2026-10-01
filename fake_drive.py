"""Drive falso con árbol de carpetas, para pruebas. Soporta lo que usa google_services."""

import re
from unittest.mock import MagicMock

_PARENT = re.compile(r"^'((?:[^'\\]|\\.)*)' in parents")


class FakeDriveTree:
    def __init__(self, tree=None, root_name="2026", root_id="parent"):
        """`tree` = {"Cliente": {"Subcarpeta": {}}} bajo la carpeta padre `root_name`."""
        self.folders = {}
        self.created = []
        self._next = 1
        self.root_id = self._add(root_name, None, root_id)
        self._load(self.root_id, tree or {})

    def _add(self, name, parent, folder_id=None):
        folder_id = folder_id or f"f{self._next}"
        self._next += 1
        self.folders[folder_id] = {"id": folder_id, "name": name, "parent": parent}
        return folder_id

    def _load(self, parent, tree):
        for name, children in tree.items():
            self._load(self._add(name, parent), children)

    def children(self, parent_id):
        return [f for f in self.folders.values() if f["parent"] == parent_id]

    def path(self, folder_id):
        names = []
        while folder_id:
            names.append(self.folders[folder_id]["name"])
            folder_id = self.folders[folder_id]["parent"]
        return "/".join(reversed(names))

    # --- API de googleapiclient (solo lo que se usa) ---
    def files(self):
        return self

    def list(self, q, fields=None, pageSize=None, pageToken=None):
        match = _PARENT.match(q)
        parent = match.group(1).replace("\\'", "'").replace("\\\\", "\\") if match else None
        files = [{"id": f["id"], "name": f["name"]} for f in self.children(parent)]
        return MagicMock(execute=lambda: {"files": files})

    def get(self, fileId, fields=None):
        folder = self.folders.get(fileId)
        if folder is None:
            raise KeyError(fileId)
        return MagicMock(execute=lambda: {"id": folder["id"], "name": folder["name"]})

    def create(self, body, fields=None):
        parent = body["parents"][0]
        folder_id = self._add(body["name"], parent)
        self.created.append(self.path(folder_id))
        folder = self.folders[folder_id]
        return MagicMock(execute=lambda: {"id": folder["id"], "name": folder["name"]})
