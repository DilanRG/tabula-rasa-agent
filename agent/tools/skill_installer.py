import json
import os
import re
import trafilatura
from datetime import datetime
from agent.tools.base import Tool
from typing import Any, Dict

SKILLS_REGISTRY_PATH = "/data/skills_registry.json"
TOOLS_DIR = "/app/agent/tools"

CORE_TOOLS = {
    "journal", "web_search", "web_read", "clock", "self_modify",
    "git", "moltbook", "reboot", "skill_installer"
}


def _sanitize_skill_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip("_")
    return name


def _to_class_name(skill_name: str) -> str:
    return "".join(part.capitalize() for part in skill_name.split("_"))


def _load_registry() -> Dict[str, Any]:
    if os.path.exists(SKILLS_REGISTRY_PATH):
        try:
            with open(SKILLS_REGISTRY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"skills": {}}


def _save_registry(registry: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(SKILLS_REGISTRY_PATH), exist_ok=True)
    with open(SKILLS_REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)


def _generate_skeleton(skill_name: str, description: str) -> str:
    class_name = _to_class_name(skill_name)
    return f'''import httpx
from agent.tools.base import Tool
from typing import Any, Dict


class {class_name}Tool(Tool):
    @property
    def name(self) -> str:
        return "{skill_name}"

    @property
    def description(self) -> str:
        return "{description}"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {{
            "type": "object",
            "properties": {{
                "action": {{
                    "type": "string",
                    "enum": ["example_action"],
                    "description": "The action to perform."
                }},
                "input": {{
                    "type": "string",
                    "description": "Input for the action."
                }}
            }},
            "required": ["action"]
        }}

    async def execute(self, action: str, **kwargs: Any) -> str:
        # TODO: Implement your actions here
        if action == "example_action":
            return "Not implemented yet. Use self_modify to add implementation."
        return f"Unknown action: {{action}}"
'''


def _generate_from_content(skill_name: str, description: str, content: str, source_url: str) -> str:
    class_name = _to_class_name(skill_name)

    # Extract HTTP endpoints from content
    endpoint_pattern = re.compile(
        r"(GET|POST|PUT|DELETE|PATCH)\s+(https?://[^\s\"'<>]+|/[^\s\"'<>]+)",
        re.IGNORECASE
    )
    endpoints = endpoint_pattern.findall(content)

    # Deduplicate and limit
    seen = set()
    unique_endpoints = []
    for method, path in endpoints:
        key = (method.upper(), path)
        if key not in seen:
            seen.add(key)
            unique_endpoints.append(key)
    unique_endpoints = unique_endpoints[:10]

    # Try to detect a base URL
    base_url_match = re.search(r"https?://[a-zA-Z0-9.\-]+(?:/[a-zA-Z0-9.\-/]*)?(?=\s|\")", content)
    base_url = ""
    if base_url_match:
        candidate = base_url_match.group(0).rstrip("/")
        # Only use it if it looks like an API base
        if re.search(r"api\.", candidate) or "/api" in candidate or "v1" in candidate or "v2" in candidate:
            base_url = candidate

    # Try to detect auth env var hints
    auth_env_hints = re.findall(
        r"(API[_\s]?KEY|TOKEN|SECRET|BEARER|AUTHORIZATION)[^\n]*?([A-Z_]{4,})",
        content,
        re.IGNORECASE
    )
    auth_var = None
    for _, var in auth_env_hints:
        if len(var) >= 4:
            auth_var = var.upper()
            break

    # Build action names from endpoints
    action_entries = []
    if_blocks = []
    for method, path in unique_endpoints:
        # Derive a readable action name from the path
        path_parts = [p for p in path.replace(base_url, "").split("/") if p and not p.startswith("{")]
        action_label = "_".join(path_parts[-2:]) if len(path_parts) >= 2 else (path_parts[-1] if path_parts else "call")
        action_label = re.sub(r"[^a-z0-9_]", "_", action_label.lower()).strip("_")
        action_label = f"{method.lower()}_{action_label}" if action_label else f"{method.lower()}_endpoint"

        full_path = path if path.startswith("http") else (base_url + path if base_url else path)
        action_entries.append(f'"{action_label}"')
        if_blocks.append(
            f'        if action == "{action_label}":\n'
            f'            resp = await client.{method.lower()}("{full_path}", params=kwargs)\n'
            f'            resp.raise_for_status()\n'
            f'            return resp.text[:4000]'
        )

    if not action_entries:
        action_entries = ['"call"']
        if_blocks = [
            '        if action == "call":\n'
            f'            resp = await client.get("{base_url or source_url}", params=kwargs)\n'
            '            resp.raise_for_status()\n'
            '            return resp.text[:4000]'
        ]

    actions_enum = ", ".join(action_entries)
    if_chain = "\n\n".join(if_blocks)

    auth_setup = ""
    if auth_var:
        auth_setup = (
            f'\n        api_key = os.environ.get("{auth_var}", "")\n'
            f'        headers = {{"Authorization": f"Bearer {{api_key}}"}} if api_key else {{}}'
        )
        headers_arg = "headers=headers"
    else:
        auth_setup = "\n        headers = {}"
        headers_arg = "headers=headers"

    return f'''import os
import httpx
from agent.tools.base import Tool
from typing import Any, Dict


# Auto-generated from: {source_url}
# Description: {description}

class {class_name}Tool(Tool):
    @property
    def name(self) -> str:
        return "{skill_name}"

    @property
    def description(self) -> str:
        return "{description}"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {{
            "type": "object",
            "properties": {{
                "action": {{
                    "type": "string",
                    "enum": [{actions_enum}],
                    "description": "The API action to perform."
                }},
                "params": {{
                    "type": "object",
                    "description": "Optional query parameters or body fields for the request.",
                    "additionalProperties": True
                }}
            }},
            "required": ["action"]
        }}

    async def execute(self, action: str, **kwargs: Any) -> str:
        try:{auth_setup}
            async with httpx.AsyncClient({headers_arg}, timeout=30) as client:
{if_chain}

                return f"Unknown action: {{action}}"
        except httpx.HTTPStatusError as e:
            return f"HTTP error {{e.response.status_code}}: {{e.response.text[:500]}}"
        except Exception as e:
            return f"Request failed: {{str(e)}}"
'''


class SkillInstallerTool(Tool):
    @property
    def name(self) -> str:
        return "skill_installer"

    @property
    def description(self) -> str:
        return (
            "Discover, install, create, list, and uninstall agent skills (tools). "
            "Can generate a new tool from an API definition URL or a blank skeleton. "
            "After installing or creating a skill, reboot the agent to load it."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["install_from_url", "list", "create", "uninstall"],
                    "description": "The skill management action."
                },
                "url": {
                    "type": "string",
                    "description": "URL of the skill definition or API docs to install from."
                },
                "skill_name": {
                    "type": "string",
                    "description": "Name for the skill (lowercase, underscores only)."
                },
                "description": {
                    "type": "string",
                    "description": "Description of what the skill does."
                }
            },
            "required": ["action"]
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        if action == "install_from_url":
            return await self._install_from_url(
                url=kwargs.get("url", ""),
                skill_name=kwargs.get("skill_name", ""),
                description=kwargs.get("description", "")
            )
        elif action == "list":
            return self._list_skills()
        elif action == "create":
            return self._create_skill(
                skill_name=kwargs.get("skill_name", ""),
                description=kwargs.get("description", "")
            )
        elif action == "uninstall":
            return self._uninstall_skill(skill_name=kwargs.get("skill_name", ""))
        else:
            return f"Unknown action: {action}"

    async def _install_from_url(self, url: str, skill_name: str, description: str) -> str:
        if not url:
            return "Error: 'url' is required for install_from_url."

        # Fetch and extract content
        try:
            downloaded = trafilatura.fetch_url(url)
        except Exception as e:
            return f"Failed to fetch URL: {e}"

        if not downloaded:
            return f"Failed to fetch content from: {url}"

        content = trafilatura.extract(downloaded) or ""
        if not content:
            return f"Could not extract readable content from: {url}"

        # Derive skill name from URL if not provided
        if not skill_name:
            path_part = re.sub(r"https?://", "", url).split("/")[0]
            domain = path_part.split(".")[0] if "." in path_part else path_part
            skill_name = _sanitize_skill_name(domain)

        skill_name = _sanitize_skill_name(skill_name)

        if not skill_name:
            return "Error: Could not derive a valid skill name. Please provide 'skill_name'."

        if skill_name in CORE_TOOLS:
            return f"Error: '{skill_name}' is a core tool and cannot be overwritten."

        if not description:
            # Try to pull a first sentence from the content
            first_sentence = content.split(".")[0].strip()[:120]
            description = first_sentence if first_sentence else f"Skill installed from {url}"

        # Generate tool code
        code = _generate_from_content(skill_name, description, content, url)

        # Write file
        file_path = os.path.join(TOOLS_DIR, f"{skill_name}.py")
        try:
            os.makedirs(TOOLS_DIR, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)
        except OSError as e:
            return f"Failed to write tool file: {e}"

        # Register in registry
        registry = _load_registry()
        registry["skills"][skill_name] = {
            "source_url": url,
            "installed_at": datetime.utcnow().isoformat(),
            "description": description,
            "file": f"agent/tools/{skill_name}.py",
            "active": True
        }
        try:
            _save_registry(registry)
        except OSError as e:
            return f"Tool file written but registry update failed: {e}"

        return (
            f"Skill '{skill_name}' installed successfully from {url}.\n"
            f"File: {file_path}\n\n"
            f"Generated code preview (first 60 lines):\n"
            + "\n".join(code.splitlines()[:60])
            + "\n\nReboot the agent to load the new skill. "
            "Use self_modify to refine the generated code if needed."
        )

    def _list_skills(self) -> str:
        registry = _load_registry()
        skills = registry.get("skills", {})
        if not skills:
            return "No skills installed. Use install_from_url or create to add a skill."

        lines = [f"Installed skills ({len(skills)} total):\n"]
        for name, meta in skills.items():
            status = "active" if meta.get("active", True) else "inactive"
            installed = meta.get("installed_at", "unknown")
            source = meta.get("source_url", "manually created")
            desc = meta.get("description", "")
            lines.append(
                f"  {name} [{status}]\n"
                f"    Description : {desc}\n"
                f"    Source      : {source}\n"
                f"    Installed   : {installed}\n"
                f"    File        : {meta.get('file', '')}"
            )
        return "\n".join(lines)

    def _create_skill(self, skill_name: str, description: str) -> str:
        if not skill_name:
            return "Error: 'skill_name' is required for create."

        skill_name = _sanitize_skill_name(skill_name)
        if not skill_name:
            return "Error: skill_name is invalid after sanitization (use lowercase letters, digits, underscores)."

        if skill_name in CORE_TOOLS:
            return f"Error: '{skill_name}' is a core tool and cannot be overwritten."

        if not description:
            description = f"A custom skill named {skill_name}."

        code = _generate_skeleton(skill_name, description)

        file_path = os.path.join(TOOLS_DIR, f"{skill_name}.py")
        try:
            os.makedirs(TOOLS_DIR, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)
        except OSError as e:
            return f"Failed to write tool file: {e}"

        # Register in registry
        registry = _load_registry()
        registry["skills"][skill_name] = {
            "source_url": "",
            "installed_at": datetime.utcnow().isoformat(),
            "description": description,
            "file": f"agent/tools/{skill_name}.py",
            "active": True
        }
        try:
            _save_registry(registry)
        except OSError as e:
            return f"Tool file written but registry update failed: {e}"

        return (
            f"Skill '{skill_name}' created at {file_path}.\n\n"
            f"Generated skeleton:\n{code}\n"
            "Use self_modify to implement the actions, then reboot to load the skill."
        )

    def _uninstall_skill(self, skill_name: str) -> str:
        if not skill_name:
            return "Error: 'skill_name' is required for uninstall."

        skill_name = _sanitize_skill_name(skill_name)

        if skill_name in CORE_TOOLS:
            return f"Error: '{skill_name}' is a core tool and cannot be uninstalled."

        registry = _load_registry()
        skill_meta = registry.get("skills", {}).get(skill_name)

        removed_file = False
        file_path = os.path.join(TOOLS_DIR, f"{skill_name}.py")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                removed_file = True
            except OSError as e:
                return f"Failed to remove skill file: {e}"
        elif skill_meta and skill_meta.get("file"):
            # Try the path from registry
            alt_path = "/" + skill_meta["file"].lstrip("/")
            if os.path.exists(alt_path):
                try:
                    os.remove(alt_path)
                    removed_file = True
                except OSError as e:
                    return f"Failed to remove skill file: {e}"

        # Remove from registry
        removed_registry = False
        if skill_name in registry.get("skills", {}):
            del registry["skills"][skill_name]
            try:
                _save_registry(registry)
                removed_registry = True
            except OSError as e:
                return f"File removed but registry update failed: {e}"

        if not removed_file and not removed_registry:
            return f"Skill '{skill_name}' not found in tools directory or registry."

        parts = []
        if removed_file:
            parts.append(f"file {file_path} deleted")
        if removed_registry:
            parts.append("removed from registry")

        return f"Skill '{skill_name}' uninstalled ({', '.join(parts)}). Reboot to apply changes."
