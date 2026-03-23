from pathlib import Path

import yaml


class PromptManager:
    def __init__(self, base_dir: Path | None = None):
        if base_dir is None:
            base_dir = Path(__file__).parent
        self._prompts_dir = base_dir / "prompts"
        config_path = base_dir / "prompt_config.yaml"
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        self._active: dict[str, str] = cfg["active"]
        self._cache: dict[str, dict] = {}

    def _load(self, name: str) -> dict:
        if name not in self._cache:
            version = self._active[name]
            path = self._prompts_dir / name / f"{version}.yaml"
            with open(path) as f:
                self._cache[name] = yaml.safe_load(f)
        return self._cache[name]

    def get(self, name: str) -> str:
        """Return prompt content string for the active version."""
        return self._load(name)["content"]

    def meta(self, name: str) -> dict:
        """Return {version, description, created} for the active prompt."""
        data = self._load(name)
        return {k: data[k] for k in ("version", "description", "created")}

    def active_versions(self) -> dict[str, str]:
        """Return {prompt_name: version_string} for all active prompts."""
        return dict(self._active)
