"""On/off switches and model choices, stored in team_config.json so you can flip them any time."""
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "team_config.json"

DEFAULT = {
    "agents": {
        "team_lead":  {"enabled": True, "model": "claude-sonnet-5-5"},
        "assistant":  {"enabled": True, "model": "claude-haiku-5-5"},
        "travel":     {"enabled": True, "model": "claude-haiku-5-5"},
        "senior_dev": {"enabled": True, "model": "claude-sonnet-5-5"},
        "junior_dev": {"enabled": True, "model": "claude-haiku-5-5"},
    }
}


def load() -> dict:
    if not CONFIG_PATH.exists():
        save(DEFAULT)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def is_enabled(key: str) -> bool:
    return load()["agents"].get(key, {}).get("enabled", False)


def set_enabled(key: str, on: bool) -> str:
    cfg = load()
    if key == "all":
        for a in cfg["agents"].values():
            a["enabled"] = on
    elif key in cfg["agents"]:
        cfg["agents"][key]["enabled"] = on
    else:
        return f"Unknown agent '{key}'. Choose: {', '.join(cfg['agents'])}, all"
    save(cfg)
    return f"{key} is now {'ON' if on else 'OFF'}."


def model_for(key: str) -> str:
    return load()["agents"][key]["model"]
