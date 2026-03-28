import os
from typing import Any, Dict, List, Optional
from openai import AsyncOpenAI
import yaml

class LLMManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.client = AsyncOpenAI(
            base_url=config['lm_studio']['host'] + "/v1",
            api_key=config['lm_studio']['api_key']
        )
    
    async def chat(self, messages: List[Dict[str, str]], model_type: str = "small", stream: bool = False, tools: Optional[List[Dict[str, Any]]] = None):
        model_cfg = self.config['models'][model_type]
        model_name = model_cfg['name']
        
        # In a real scenario, we might call LM Studio's /v1/models/load if it's not loaded
        # But for now we assume it's managed via config or already loaded.
        
        params = {
            "model": model_name,
            "messages": messages,
            "temperature": self.config['sampling']['temperature'],
            "top_p": self.config['sampling']['top_p'],
            "max_tokens": self.config['sampling']['max_tokens'],
            "stream": stream,
            "extra_body": {
                "presence_penalty": self.config['sampling']['presence_penalty'],
                "frequency_penalty": 0.0,
            }
        }
        if tools:
            params["tools"] = tools

        response = await self.client.chat.completions.create(**params)
        return response

def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    with open(path, 'r') as f:
        return yaml.safe_load(f)
