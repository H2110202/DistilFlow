"""
工具注册系统 — 借鉴 Hermes 的 ToolRegistry 模式。
支持装饰器注册、函数对象注册、JSON Schema 注册三种方式。
"""
from typing import Callable, Optional, Any
import inspect
import json


class ToolEntry:
    def __init__(
        self,
        name: str,
        fn: Callable,
        description: str = "",
        category: str = "general",
    ):
        self.name = name
        self.fn = fn
        self.description = description or (fn.__doc__ or "").strip()
        self.category = category
        self._schema = None

    @property
    def schema(self) -> dict:
        if self._schema is None:
            self._schema = self._build_schema()
        return self._schema

    def _build_schema(self) -> dict:
        sig = inspect.signature(self.fn)
        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            param_type = "string"
            if param.annotation != inspect.Parameter.empty:
                anno = param.annotation
                if anno == int:
                    param_type = "integer"
                elif anno == float:
                    param_type = "number"
                elif anno == bool:
                    param_type = "boolean"

            properties[param_name] = {"type": param_type, "description": param_name}
            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        desc = self.description.split("\n")[0][:200]

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def execute(self, **kwargs) -> str:
        try:
            result = self.fn(**kwargs)
            if isinstance(result, str):
                return result
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}

    def register(
        self,
        fn: Optional[Callable] = None,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        category: str = "general",
    ):
        """装饰器注册或函数注册"""
        if fn is None:
            return lambda f: self.register(f, name=name, description=description, category=category)

        tool_name = name or fn.__name__
        entry = ToolEntry(name=tool_name, fn=fn, description=description or "", category=category)
        self._tools[tool_name] = entry
        return fn

    def register_schema(self, name: str, schema: dict, fn: Callable, category: str = "general"):
        """通过 JSON Schema 注册工具"""
        entry = ToolEntry(name=name, fn=fn, category=category)
        entry._schema = schema
        self._tools[name] = entry

    def get_schemas(self, categories: Optional[list[str]] = None) -> list[dict]:
        """获取所有工具的 OpenAI function-calling schema"""
        schemas = []
        for entry in self._tools.values():
            if categories is None or entry.category in categories:
                schemas.append(entry.schema)
        return schemas

    def execute(self, name: str, arguments: dict) -> str:
        """按名称执行工具"""
        entry = self._tools.get(name)
        if entry is None:
            return json.dumps({"error": f"未知工具: {name}"})
        return entry.execute(**arguments)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def get_by_category(self, category: str) -> list[ToolEntry]:
        return [e for e in self._tools.values() if e.category == category]


registry = ToolRegistry()
