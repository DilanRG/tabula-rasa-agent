import os
from datetime import datetime
from agent.tools.base import Tool
from typing import Any, Dict

class JournalTool(Tool):
    @property
    def name(self) -> str:
        return "journal"

    @property
    def description(self) -> str:
        return "Read or write to your persistent journal. Use it for memory, reflection, and state tracking."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["write", "read_today", "read_date", "search"],
                    "description": "The action to perform."
                },
                "content": {
                    "type": "string",
                    "description": "Content to write (for 'write' action)."
                },
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format (for 'read_date' action)."
                },
                "query": {
                    "type": "string",
                    "description": "Keyword to search for."
                }
            },
            "required": ["action"]
        }

    @staticmethod
    def _word_set(text: str) -> set:
        """Extract a set of lowercase words from text (for overlap check)."""
        return set(text.lower().split())

    def _is_duplicate(self, file_path: str, new_content: str) -> bool:
        """Check if new_content overlaps too much with the last journal entry."""
        if not os.path.exists(file_path):
            return False
        with open(file_path, "r") as f:
            text = f.read()
        # Find the last entry (after the last ### header)
        parts = text.split("\n### ")
        if len(parts) < 2:
            return False
        last_entry = parts[-1]
        # Compare word overlap
        old_words = self._word_set(last_entry)
        new_words = self._word_set(new_content)
        if not new_words:
            return False
        overlap = len(old_words & new_words) / len(new_words)
        return overlap > 0.5

    async def execute(self, action: str, content: str = "", date: str = "", query: str = "") -> str:
        base_path = "/data/journal"
        if not os.path.exists(base_path):
            os.makedirs(base_path, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        file_path = f"{base_path}/{today}.md"

        if action == "write":
            # Allow [SYSTEM] and [REMINDER] entries through without dupe check
            is_system = content.strip().startswith(("[SYSTEM]", "[REMINDER]"))
            if not is_system and self._is_duplicate(file_path, content):
                return "Entry too similar to the last one — not written. Do something new first."
            timestamp = datetime.now().strftime("%H:%M:%S")
            with open(file_path, "a") as f:
                f.write(f"\n### [{timestamp}]\n{content}\n")
            return f"Successfully wrote to today's journal."

        elif action == "read_today":
            if not os.path.exists(file_path):
                return "No entries for today yet."
            with open(file_path, "r") as f:
                content = f.read()
                if len(content) > 4000:
                    return "...[EARLIER ENTRIES TRUNCATED]...\n" + content[-4000:]
                return content

        elif action == "read_date":
            path = f"{base_path}/{date}.md"
            if not os.path.exists(path):
                return f"No entries found for {date}."
            with open(path, "r") as f:
                return f.read()

        elif action == "search":
            results = []
            for file in os.listdir(base_path):
                if file.endswith(".md"):
                    with open(f"{base_path}/{file}", "r") as f:
                        if query.lower() in f.read().lower():
                            results.append(file.replace(".md", ""))
            return f"Keyword found in journals for dates: {', '.join(results)}" if results else "No results found."

        return "Invalid action."
