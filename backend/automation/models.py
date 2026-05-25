import json
import uuid
import hashlib
import base64
from datetime import datetime
from pathlib import Path
from typing import Optional
from backend.config import get_settings


class ActionTemplate:
    def __init__(self, data: dict):
        self.id = data.get("id", f"at_{uuid.uuid4().hex[:8]}")
        self.name = data.get("name", "")
        self.url = data.get("url", "")
        self.login_url = data.get("login_url", "")
        self.description = data.get("description", "")
        self.steps = data.get("steps", [])
        self.login_steps = data.get("login_steps", [])
        self.variables = data.get("variables", {})
        self.field_mapping = data.get("field_mapping", {})
        self.submit_after_fill = data.get("submit_after_fill", True)
        self.loop_mode = data.get("loop_mode", "single")
        self.created_at = data.get("created_at", datetime.now().isoformat())
        self.updated_at = data.get("updated_at", datetime.now().isoformat())
        self.usage_count = data.get("usage_count", 0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "login_url": self.login_url,
            "description": self.description,
            "steps": self.steps,
            "login_steps": self.login_steps,
            "variables": self.variables,
            "field_mapping": self.field_mapping,
            "submit_after_fill": self.submit_after_fill,
            "loop_mode": self.loop_mode,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "usage_count": self.usage_count,
        }


class CredentialStore:
    def __init__(self):
        settings = get_settings()
        self.data_dir = Path(settings.data_dir) / "automation"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._key = "fc_2026_key"

    def _obfuscate(self, text: str) -> str:
        return base64.b64encode(
            bytes([b ^ 0x5A for b in text.encode("utf-8")])
        ).decode("ascii")

    def _deobfuscate(self, text: str) -> str:
        return bytes([b ^ 0x5A for b in base64.b64decode(text)]).decode("utf-8")

    def save_credentials(self, site_url: str, username: str, password: str):
        creds = self._load_all()
        domain = self._extract_domain(site_url)
        creds[domain] = {
            "username": self._obfuscate(username),
            "password": self._obfuscate(password),
            "updated_at": datetime.now().isoformat(),
        }
        self._save_all(creds)

    def get_credentials(self, site_url: str) -> Optional[dict]:
        creds = self._load_all()
        domain = self._extract_domain(site_url)
        entry = creds.get(domain)
        if entry:
            return {
                "username": self._deobfuscate(entry["username"]),
                "password": self._deobfuscate(entry["password"]),
            }
        return None

    def delete_credentials(self, site_url: str):
        creds = self._load_all()
        domain = self._extract_domain(site_url)
        creds.pop(domain, None)
        self._save_all(creds)

    def _extract_domain(self, url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return url

    def _load_all(self) -> dict:
        path = self.data_dir / "credentials.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_all(self, creds: dict):
        path = self.data_dir / "credentials.json"
        path.write_text(json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8")


class AutomationStore:
    def __init__(self):
        settings = get_settings()
        self.store_dir = Path(settings.data_dir) / "automation"
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def save(self, template: ActionTemplate) -> dict:
        path = self.store_dir / f"{template.id}.json"
        path.write_text(json.dumps(template.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return template.to_dict()

    def load(self, template_id: str) -> Optional[dict]:
        path = self.store_dir / f"{template_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_all(self) -> list[dict]:
        results = []
        for f in self.store_dir.glob("at_*.json"):
            try:
                results.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
        return results

    def find_by_name(self, name: str) -> Optional[dict]:
        for t in self.list_all():
            if name in t.get("name", "") or t.get("name", "") in name:
                return t
        return None

    def delete(self, template_id: str) -> bool:
        path = self.store_dir / f"{template_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def increment_usage(self, template_id: str):
        data = self.load(template_id)
        if data:
            data["usage_count"] = data.get("usage_count", 0) + 1
            data["updated_at"] = datetime.now().isoformat()
            path = self.store_dir / f"{template_id}.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class SystemGuideStore:
    def __init__(self):
        settings = get_settings()
        self.store_dir = Path(settings.data_dir) / "automation" / "guides"
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def save(self, guide_data: dict) -> dict:
        guide_id = guide_data.get("id", f"sg_{uuid.uuid4().hex[:8]}")
        guide_data["id"] = guide_id
        guide_data["updated_at"] = datetime.now().isoformat()
        path = self.store_dir / f"{guide_id}.json"
        path.write_text(json.dumps(guide_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return guide_data

    def load(self, guide_id: str) -> Optional[dict]:
        path = self.store_dir / f"{guide_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_all(self) -> list[dict]:
        results = []
        for f in self.store_dir.glob("sg_*.json"):
            try:
                results.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
        return results

    def find_by_site(self, site_url: str) -> Optional[dict]:
        from urllib.parse import urlparse
        domain = urlparse(site_url).netloc
        for g in self.list_all():
            g_domain = urlparse(g.get("site_url", "")).netloc
            if g_domain == domain:
                return g
        return None

    def delete(self, guide_id: str) -> bool:
        path = self.store_dir / f"{guide_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False
