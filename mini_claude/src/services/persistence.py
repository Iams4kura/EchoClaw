"""Session persistence - save and load conversations.

Reference: config/sessions/ directory
"""

import json
import os
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from ..models.state import AppState


class SessionPersistence:
    """Save and load conversation sessions to disk."""

    def __init__(self, sessions_dir: str = "config/sessions"):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def save(self, state: AppState) -> str:
        """Save session state to file. Returns the file path."""
        filename = f"{state.session_id}.json"
        filepath = self.sessions_dir / filename

        data = state.to_dict()
        data["saved_at"] = datetime.now().isoformat()

        filepath.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )

        return str(filepath)

    def load(self, session_id: str) -> Optional[AppState]:
        """Load session state from file."""
        filepath = self.sessions_dir / f"{session_id}.json"

        if not filepath.exists():
            # Try partial match
            for f in self.sessions_dir.glob(f"*{session_id}*.json"):
                filepath = f
                break
            else:
                return None

        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            return AppState.from_dict(data)
        except Exception:
            return None

    def list_sessions(self, limit: int = 20) -> List[dict]:
        """List recent sessions."""
        sessions = []
        for filepath in sorted(
            self.sessions_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]:
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                sessions.append({
                    "session_id": data.get("session_id", filepath.stem),
                    "created_at": data.get("created_at", ""),
                    "saved_at": data.get("saved_at", ""),
                    "messages": len(data.get("messages", [])),
                    "file": str(filepath),
                })
            except Exception:
                continue

        return sessions

    def delete(self, session_id: str) -> bool:
        """Delete a saved session."""
        filepath = self.sessions_dir / f"{session_id}.json"
        if filepath.exists():
            filepath.unlink()
            return True
        return False

    def cleanup(self, max_sessions: int = 50) -> int:
        """Remove oldest sessions if over limit. Returns count removed."""
        files = sorted(
            self.sessions_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
        )

        removed = 0
        while len(files) > max_sessions:
            files[0].unlink()
            files.pop(0)
            removed += 1

        return removed
