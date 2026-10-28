"""Drive de memoria con bytes, claves, paginación y fallos para validar expedientes completos."""

import re
from types import SimpleNamespace

from anka.infra.google_services import FOLDER_MIME


class MemoryDrive:
    def __init__(self):
        self.items = {"reception": {"id": "reception", "name": "Recepcion", "mimeType": FOLDER_MIME}}
        self.contents = {}
        self.fail_name = ""
        self.writes = 0
        self.page_size = 2

    def files(self):
        return self

    def list(self, q, **kwargs):
        parent = re.match(r"'([^']+)'", q).group(1)
        key = re.search(r"value='([^']+)'", q).group(1)
        found = [v.copy() for v in self.items.values()
                 if parent in v.get("parents", []) and v.get("appProperties", {}).get("anka_work_key") == key]
        start = int(kwargs.get("pageToken") or 0)
        page = {"files": found[start:start + self.page_size]}
        if start + self.page_size < len(found):
            page["nextPageToken"] = str(start + self.page_size)
        return SimpleNamespace(execute=lambda: page)

    def get(self, fileId, **kwargs):
        return SimpleNamespace(execute=lambda: self.items[fileId].copy())

    def get_media(self, fileId):
        return SimpleNamespace(execute=lambda: self.contents[fileId])

    def create(self, body, media_body=None, **kwargs):
        def execute():
            if self.fail_name and self.fail_name in body["name"]:
                raise ConnectionError("subida interrumpida")
            file_id = "f" + str(len(self.items))
            self.items[file_id] = {**body, "id": file_id}
            if media_body:
                self.contents[file_id] = media_body.getbytes(0, media_body.size())
            self.writes += 1
            return self.items[file_id].copy()
        return SimpleNamespace(execute=execute)

    def update(self, fileId, media_body, **kwargs):
        def execute():
            if self.fail_name and self.fail_name in self.items[fileId]["name"]:
                raise ConnectionError("actualización interrumpida")
            self.contents[fileId] = media_body.getbytes(0, media_body.size())
            self.writes += 1
            return self.items[fileId].copy()
        return SimpleNamespace(execute=execute)
