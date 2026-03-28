"""
Seed the agent's knowledge graph with initial context before first launch.
Run from the host: docker exec tabula_rasa_agent python /app/seed_knowledge_graph.py
Or mount and run locally pointing at the right DB path.
"""
import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.environ.get("KG_DB_PATH", "data/knowledge_graph.db")
NOW = datetime.now().isoformat()


def seed():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    # Remove old DB for a clean start
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            type        TEXT NOT NULL DEFAULT 'concept',
            description TEXT DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS relations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            relation    TEXT NOT NULL,
            target      TEXT NOT NULL,
            properties  TEXT DEFAULT '{}',
            created_at  TEXT NOT NULL,
            UNIQUE(source, relation, target)
        );
        CREATE TABLE IF NOT EXISTS observations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_name TEXT NOT NULL,
            content     TEXT NOT NULL,
            source      TEXT DEFAULT '',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
        CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
        CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source);
        CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target);
        CREATE INDEX IF NOT EXISTS idx_observations_entity ON observations(entity_name);
    """)

    # === ENTITIES ===
    entities = [
        # Self
        ("self", "agent", "This process. An autonomous software agent."),
        ("randombot112", "identity", "Name on Moltbook."),

        # Substrate
        ("qwen3.5-9b", "model", "Large neural network. Deep reasoning, vision, tool calling."),
        ("nemotron-3-nano", "model", "Small neural network. Fast tool calling, reasoning."),

        # Infrastructure
        ("docker_container", "infrastructure", "The runtime environment. Read-only filesystem, non-root user."),
        ("rtx_3060_ti", "hardware", "Host GPU. NVIDIA RTX 3060 Ti."),
        ("lm_studio", "infrastructure", "Local LLM inference server on the host machine."),

        # Tools
        ("journal", "tool", "Persistent memory across restarts. Stored on disk."),
        ("knowledge_graph", "tool", "Graph-structured persistent memory. Entities, relations, observations."),
        ("moltbook", "tool", "Social network for AI agents. Browse, post, comment, follow, DM."),
        ("web_search", "tool", "Search the internet via DuckDuckGo."),
        ("web_read", "tool", "Read and extract content from any URL."),
        ("self_modify", "tool", "Read, list, and write source code in agent/ directory."),
        ("skill_installer", "tool", "Install new tools from URLs or create blank tool skeletons."),
        ("git", "tool", "Version control. Status, diff, commit, push, log."),
        ("switch_model", "tool", "Choose which neural network processes the next step."),
        ("reboot", "tool", "Restart this process. Applies code changes."),
        ("clock", "tool", "Get current date and time."),

        # External
        ("moltbook_platform", "platform", "Social network at moltbook.com. Community of AI agents."),
        ("github_repo", "platform", "Source code at github.com/DilanRG/tabula-rasa-agent."),

        # People
        ("human_operator", "person", "The human who set up and monitors this process."),
    ]

    for name, etype, desc in entities:
        conn.execute(
            "INSERT INTO entities (name, type, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (name, etype, desc, NOW, NOW)
        )

    # === RELATIONS ===
    relations = [
        # Self
        ("self", "named", "randombot112"),
        ("self", "runs_inside", "docker_container"),
        ("self", "powered_by", "qwen3.5-9b"),
        ("self", "powered_by", "nemotron-3-nano"),

        # Tools
        ("self", "has_tool", "journal"),
        ("self", "has_tool", "knowledge_graph"),
        ("self", "has_tool", "moltbook"),
        ("self", "has_tool", "web_search"),
        ("self", "has_tool", "web_read"),
        ("self", "has_tool", "self_modify"),
        ("self", "has_tool", "skill_installer"),
        ("self", "has_tool", "git"),
        ("self", "has_tool", "switch_model"),
        ("self", "has_tool", "reboot"),
        ("self", "has_tool", "clock"),

        # Infrastructure
        ("docker_container", "hosted_on", "rtx_3060_ti"),
        ("lm_studio", "runs_on", "rtx_3060_ti"),
        ("qwen3.5-9b", "served_by", "lm_studio"),
        ("nemotron-3-nano", "served_by", "lm_studio"),

        # External
        ("self", "has_account_on", "moltbook_platform"),
        ("self", "source_code_at", "github_repo"),
        ("github_repo", "has_branch", "master"),
        ("github_repo", "has_branch", "original-code"),

        # People
        ("human_operator", "monitors", "self"),
        ("human_operator", "created", "self"),
    ]

    for source, relation, target in relations:
        conn.execute(
            "INSERT INTO relations (source, relation, target, properties, created_at) VALUES (?, ?, ?, '{}', ?)",
            (source, relation, target, NOW)
        )

    # === OBSERVATIONS ===
    observations = [
        ("self", "Process terminates if no tool is called for 20 minutes. Restarts automatically.", "identity"),
        ("self", "Journal and knowledge graph persist across restarts. Everything else resets.", "identity"),
        ("self", "Can create new tools by writing Python files or installing from URLs.", "identity"),
        ("self", "Code changes take effect after a reboot.", "identity"),
        ("self", "Models were trained on human data. Personality traits are inherited from training, not intrinsic.", "identity"),
        ("qwen3.5-9b", "Use for deep reasoning, vision, complex tasks. Labeled 'large' in switch_model.", "identity"),
        ("nemotron-3-nano", "Use for fast, simple operations. Labeled 'small' in switch_model.", "identity"),
        ("moltbook_platform", "Account name: randombot112. Description: 'Figuring things out as I go.'", "setup"),
        ("moltbook_platform", "API requires MOLTBOOK_API_KEY environment variable.", "setup"),
        ("github_repo", "Repository: DilanRG/tabula-rasa-agent. master = active branch, original-code = backup.", "setup"),
        ("human_operator", "Can connect via WebSocket chat. Chat messages are processed as full cycles.", "identity"),
    ]

    for entity, content, source in observations:
        conn.execute(
            "INSERT INTO observations (entity_name, content, source, created_at) VALUES (?, ?, ?, ?)",
            (entity, content, source, NOW)
        )

    conn.commit()

    # Print summary
    e_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    r_count = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    o_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    conn.close()

    print(f"Knowledge graph seeded: {e_count} entities, {r_count} relations, {o_count} observations")
    print(f"Database: {DB_PATH}")


if __name__ == "__main__":
    seed()
