import json
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_api_base: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    llm_provider: str = "openrouter"
    default_model: str = "anthropic/claude-sonnet-4-20250514"
    max_tool_call_rounds: int = 10
    tool_call_timeout: int = 30

    company_name: str = ""
    department: str = ""

    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_corp_id: str = ""

    data_dir: str = "./data"

    provider_keys: dict = {}
    provider_base_urls: dict = {}

    @property
    def memories_dir(self) -> Path:
        p = Path(self.data_dir) / "memories"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def skills_dir(self) -> Path:
        p = Path(self.data_dir) / "skills"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sessions_dir(self) -> Path:
        p = Path(self.data_dir) / "sessions"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def memory_file(self) -> Path:
        return self.memories_dir / "MEMORY.md"

    @property
    def user_file(self) -> Path:
        return self.memories_dir / "USER.md"

    @property
    def summary_file(self) -> Path:
        return self.memories_dir / "SUMMARY.md"

    def get_provider_key(self, provider: str) -> str:
        if provider in self.provider_keys:
            return self.provider_keys[provider]
        if provider == "openrouter" and self.openrouter_api_key:
            return self.openrouter_api_key
        if provider == "openai" and self.openai_api_key:
            return self.openai_api_key
        return ""

    def get_provider_base_url(self, provider: str) -> str:
        if provider in self.provider_base_urls:
            return self.provider_base_urls[provider]
        defaults = {
            "openrouter": "https://openrouter.ai/api/v1",
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
        }
        return defaults.get(provider, "")

    def get_llm_config(self) -> dict:
        provider = self.llm_provider
        key = self.get_provider_key(provider)
        base_url = self.get_provider_base_url(provider)
        litellm_provider_map = {
            "openrouter": "openrouter",
            "openai": "openai",
            "anthropic": "anthropic",
            "deepseek": "openai",
            "qwen": "openai",
            "gemini": "openai",
        }
        litellm_provider = litellm_provider_map.get(provider, "openai")
        if key:
            config = {
                "api_key": key,
                "custom_llm_provider": litellm_provider,
            }
            if base_url and litellm_provider == "openai":
                config["api_base"] = base_url
            return config
        if self.openrouter_api_key:
            return {
                "api_key": self.openrouter_api_key,
                "api_base": "https://openrouter.ai/api/v1",
                "custom_llm_provider": "openrouter",
            }
        return {
            "api_key": self.openai_api_key,
            "api_base": self.openai_api_base,
        }


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.data_dir = str(PROJECT_ROOT / "data")
        config_path = PROJECT_ROOT / "data" / "providers.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                if data.get("provider_keys"):
                    _settings.provider_keys = data["provider_keys"]
                if data.get("provider_base_urls"):
                    _settings.provider_base_urls = data["provider_base_urls"]
                if data.get("default_model"):
                    _settings.default_model = data["default_model"]
                if data.get("llm_provider"):
                    _settings.llm_provider = data["llm_provider"]
                if data.get("company_name"):
                    _settings.company_name = data["company_name"]
                if data.get("dingtalk_app_key"):
                    _settings.dingtalk_app_key = data["dingtalk_app_key"]
                if data.get("dingtalk_app_secret"):
                    _settings.dingtalk_app_secret = data["dingtalk_app_secret"]
                if data.get("dingtalk_corp_id"):
                    _settings.dingtalk_corp_id = data["dingtalk_corp_id"]
            except:
                pass
    return _settings


def reset_settings():
    global _settings
    _settings = None
