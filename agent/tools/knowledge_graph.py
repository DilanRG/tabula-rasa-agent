import os
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict
from agent.tools.base import Tool

DB_PATH = "/data/knowledge_graph.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db():
    conn = _get_conn()
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
    conn.commit()
    conn.close()


class KnowledgeGraphTool(Tool):
    @property
    def name(self) -> str:
        return "knowledge_graph"

    @property
    def description(self) -> str:
        return (
            "Persistent memory as a knowledge graph. Store and query entities, "
            "relationships, and observations. Survives restarts. "
            "Actions: add_entity, add_relation, add_observation, query, search, "
            "neighbors, delete_entity, delete_relation, stats, dump."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "add_entity", "add_relation", "add_observation",
                        "query", "search", "neighbors",
                        "delete_entity", "delete_relation",
                        "stats", "dump",
                    ],
                    "description": "The operation to perform."
                },
                "name": {
                    "type": "string",
                    "description": "Entity name (for add_entity, query, neighbors, delete_entity, add_observation)."
                },
                "type": {
                    "type": "string",
                    "description": "Entity type (for add_entity). E.g. person, concept, tool, place, event."
                },
                "description": {
                    "type": "string",
                    "description": "Entity description (for add_entity)."
                },
                "source": {
                    "type": "string",
                    "description": "Source entity name (for add_relation, delete_relation)."
                },
                "relation": {
                    "type": "string",
                    "description": "Relationship type in active voice (for add_relation, delete_relation). E.g. 'runs_on', 'created_by', 'knows'."
                },
                "target": {
                    "type": "string",
                    "description": "Target entity name (for add_relation, delete_relation)."
                },
                "content": {
                    "type": "string",
                    "description": "Observation text (for add_observation)."
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for search action). Searches entity names, descriptions, and observations."
                },
                "depth": {
                    "type": "integer",
                    "description": "Traversal depth for neighbors (default 1, max 3)."
                },
            },
            "required": ["action"]
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        _init_db()
        try:
            if action == "add_entity":
                return self._add_entity(kwargs.get("name", ""), kwargs.get("type", "concept"), kwargs.get("description", ""))
            elif action == "add_relation":
                return self._add_relation(kwargs.get("source", ""), kwargs.get("relation", ""), kwargs.get("target", ""), kwargs.get("properties", "{}"))
            elif action == "add_observation":
                return self._add_observation(kwargs.get("name", ""), kwargs.get("content", ""), kwargs.get("source", ""))
            elif action == "query":
                return self._query_entity(kwargs.get("name", ""))
            elif action == "search":
                return self._search(kwargs.get("query", ""))
            elif action == "neighbors":
                return self._neighbors(kwargs.get("name", ""), kwargs.get("depth", 1))
            elif action == "delete_entity":
                return self._delete_entity(kwargs.get("name", ""))
            elif action == "delete_relation":
                return self._delete_relation(kwargs.get("source", ""), kwargs.get("relation", ""), kwargs.get("target", ""))
            elif action == "stats":
                return self._stats()
            elif action == "dump":
                return self._dump()
            else:
                return f"Unknown action: {action}"
        except Exception as e:
            return f"Knowledge graph error: {e}"

    def _add_entity(self, name: str, etype: str, description: str) -> str:
        if not name:
            return "Error: name is required."
        now = datetime.now().isoformat()
        conn = _get_conn()
        existing = conn.execute("SELECT id FROM entities WHERE name = ?", (name,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE entities SET type = ?, description = ?, updated_at = ? WHERE name = ?",
                (etype, description, now, name)
            )
            conn.commit()
            conn.close()
            return f"Updated entity '{name}' (type={etype})."
        else:
            conn.execute(
                "INSERT INTO entities (name, type, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (name, etype, description, now, now)
            )
            conn.commit()
            conn.close()
            return f"Created entity '{name}' (type={etype})."

    def _add_relation(self, source: str, relation: str, target: str, properties: str = "{}") -> str:
        if not all([source, relation, target]):
            return "Error: source, relation, and target are required."
        now = datetime.now().isoformat()
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO relations (source, relation, target, properties, created_at) VALUES (?, ?, ?, ?, ?)",
                (source, relation, target, properties, now)
            )
            conn.commit()
            return f"Relation: {source} --[{relation}]--> {target}"
        finally:
            conn.close()

    def _add_observation(self, entity_name: str, content: str, source: str = "") -> str:
        if not entity_name or not content:
            return "Error: name and content are required."
        now = datetime.now().isoformat()
        conn = _get_conn()
        conn.execute(
            "INSERT INTO observations (entity_name, content, source, created_at) VALUES (?, ?, ?, ?)",
            (entity_name, content, source, now)
        )
        conn.commit()
        conn.close()
        return f"Observation added to '{entity_name}'."

    def _query_entity(self, name: str) -> str:
        if not name:
            return "Error: name is required."
        conn = _get_conn()
        entity = conn.execute("SELECT * FROM entities WHERE name = ?", (name,)).fetchone()
        if not entity:
            conn.close()
            return f"Entity '{name}' not found."

        outgoing = conn.execute(
            "SELECT relation, target, properties FROM relations WHERE source = ?", (name,)
        ).fetchall()
        incoming = conn.execute(
            "SELECT source, relation, properties FROM relations WHERE target = ?", (name,)
        ).fetchall()
        observations = conn.execute(
            "SELECT content, source, created_at FROM observations WHERE entity_name = ? ORDER BY created_at DESC LIMIT 20",
            (name,)
        ).fetchall()
        conn.close()

        result = {
            "entity": {
                "name": entity["name"],
                "type": entity["type"],
                "description": entity["description"],
                "created": entity["created_at"],
                "updated": entity["updated_at"],
            },
            "outgoing_relations": [
                {"relation": r["relation"], "target": r["target"]} for r in outgoing
            ],
            "incoming_relations": [
                {"source": r["source"], "relation": r["relation"]} for r in incoming
            ],
            "observations": [
                {"content": o["content"], "source": o["source"], "at": o["created_at"]} for o in observations
            ],
        }
        return json.dumps(result, indent=2)

    def _search(self, query: str) -> str:
        if not query:
            return "Error: query is required."
        conn = _get_conn()
        pattern = f"%{query}%"
        entities = conn.execute(
            "SELECT name, type, description FROM entities WHERE name LIKE ? OR description LIKE ? LIMIT 20",
            (pattern, pattern)
        ).fetchall()
        observations = conn.execute(
            "SELECT entity_name, content FROM observations WHERE content LIKE ? LIMIT 20",
            (pattern,)
        ).fetchall()
        relations = conn.execute(
            "SELECT source, relation, target FROM relations WHERE source LIKE ? OR target LIKE ? OR relation LIKE ? LIMIT 20",
            (pattern, pattern, pattern)
        ).fetchall()
        conn.close()

        result = {
            "entities": [{"name": e["name"], "type": e["type"], "desc": e["description"][:100]} for e in entities],
            "observations": [{"entity": o["entity_name"], "content": o["content"][:200]} for o in observations],
            "relations": [{"source": r["source"], "rel": r["relation"], "target": r["target"]} for r in relations],
        }
        return json.dumps(result, indent=2)

    def _neighbors(self, name: str, depth: int = 1) -> str:
        if not name:
            return "Error: name is required."
        depth = min(max(depth, 1), 3)
        conn = _get_conn()
        visited = set()
        frontier = {name}
        graph = {"nodes": [], "edges": []}

        for d in range(depth):
            next_frontier = set()
            for node in frontier:
                if node in visited:
                    continue
                visited.add(node)
                entity = conn.execute("SELECT name, type FROM entities WHERE name = ?", (node,)).fetchone()
                if entity:
                    graph["nodes"].append({"name": entity["name"], "type": entity["type"], "depth": d})

                outgoing = conn.execute(
                    "SELECT relation, target FROM relations WHERE source = ?", (node,)
                ).fetchall()
                for r in outgoing:
                    graph["edges"].append({"source": node, "relation": r["relation"], "target": r["target"]})
                    next_frontier.add(r["target"])

                incoming = conn.execute(
                    "SELECT source, relation FROM relations WHERE target = ?", (node,)
                ).fetchall()
                for r in incoming:
                    graph["edges"].append({"source": r["source"], "relation": r["relation"], "target": node})
                    next_frontier.add(r["source"])

            frontier = next_frontier - visited

        conn.close()
        return json.dumps(graph, indent=2)

    def _delete_entity(self, name: str) -> str:
        if not name:
            return "Error: name is required."
        conn = _get_conn()
        conn.execute("DELETE FROM entities WHERE name = ?", (name,))
        conn.execute("DELETE FROM relations WHERE source = ? OR target = ?", (name, name))
        conn.execute("DELETE FROM observations WHERE entity_name = ?", (name,))
        conn.commit()
        conn.close()
        return f"Deleted entity '{name}' and all its relations/observations."

    def _delete_relation(self, source: str, relation: str, target: str) -> str:
        if not all([source, relation, target]):
            return "Error: source, relation, and target are required."
        conn = _get_conn()
        conn.execute(
            "DELETE FROM relations WHERE source = ? AND relation = ? AND target = ?",
            (source, relation, target)
        )
        conn.commit()
        conn.close()
        return f"Deleted relation: {source} --[{relation}]--> {target}"

    def _stats(self) -> str:
        conn = _get_conn()
        entities = conn.execute("SELECT COUNT(*) as c FROM entities").fetchone()["c"]
        relations = conn.execute("SELECT COUNT(*) as c FROM relations").fetchone()["c"]
        observations = conn.execute("SELECT COUNT(*) as c FROM observations").fetchone()["c"]
        types = conn.execute("SELECT type, COUNT(*) as c FROM entities GROUP BY type ORDER BY c DESC").fetchall()
        conn.close()
        return json.dumps({
            "entities": entities,
            "relations": relations,
            "observations": observations,
            "entity_types": {t["type"]: t["c"] for t in types},
        }, indent=2)

    def _dump(self) -> str:
        """Return the full graph as a compact summary."""
        conn = _get_conn()
        entities = conn.execute("SELECT name, type, description FROM entities ORDER BY type, name").fetchall()
        relations = conn.execute("SELECT source, relation, target FROM relations ORDER BY source").fetchall()
        conn.close()

        lines = ["=== ENTITIES ==="]
        for e in entities:
            desc = f" — {e['description']}" if e["description"] else ""
            lines.append(f"  [{e['type']}] {e['name']}{desc}")
        lines.append(f"\n=== RELATIONS ({len(relations)}) ===")
        for r in relations:
            lines.append(f"  {r['source']} --[{r['relation']}]--> {r['target']}")

        output = "\n".join(lines)
        return output[:6000]  # Cap output size
