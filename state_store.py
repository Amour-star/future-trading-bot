import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

STATE_PATH = Path("data/state.json")
_PASSWORD = "1.618"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def default_state() -> Dict[str, Any]:
    return {
        "bot_status": "stopped",
        "last_action": None,
        "last_retrain": None,
        "indicators": {
            "min_price_model_proba": 0.55,
            "min_news_sentiment": 0.1,
            "max_news_sentiment_for_short": -0.1,
        },
        "daily_pnl": [],
        "budget_pnl": [],
        "news_health": {
            "last_fetch": None,
            "last_error": None,
            "source": None,
            "headlines": 0,
        },
        "commands": [],
    }


class StateStore:
    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(default_state())

    def _read(self) -> Dict[str, Any]:
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: Dict[str, Any]) -> None:
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return self._read()

    def update_state(self, **kwargs: Any) -> Dict[str, Any]:
        with self._lock:
            data = self._read()
            data.update(kwargs)
            self._write(data)
            return data

    def record_command(self, action: str, meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
        entry = {
            "action": action,
            "meta": meta or {},
            "ts": _now_iso(),
        }
        with self._lock:
            data = self._read()
            data.setdefault("commands", []).insert(0, entry)
            data["commands"] = data["commands"][:50]
            self._write(data)
            return data

    def record_news_health(self, source: str, headlines: int, error: str | None = None) -> Dict[str, Any]:
        with self._lock:
            data = self._read()
            data["news_health"] = {
                "last_fetch": _now_iso(),
                "last_error": error,
                "source": source,
                "headlines": headlines,
            }
            self._write(data)
            return data

    def update_indicators(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            data = self._read()
            ind = data.get("indicators", {})
            ind.update(updates)
            data["indicators"] = ind
            self._write(data)
            return data

    def update_pnl(self, daily: list[Dict[str, Any]] | None = None, budget: list[Dict[str, Any]] | None = None) -> Dict[str, Any]:
        with self._lock:
            data = self._read()
            if daily is not None:
                data["daily_pnl"] = daily
            if budget is not None:
                data["budget_pnl"] = budget
            self._write(data)
            return data


def check_password(value: str) -> bool:
    return value == _PASSWORD


store = StateStore()
