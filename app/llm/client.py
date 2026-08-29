"""DeepSeek LLM client (chat completions via requests, JSON output)."""
import requests

class LLMClient:
    def __init__(self, api_key, base_url="https://api.deepseek.com", model="deepseek-v4-flash",
                 temperature=0.7, max_tokens=32768, timeout=120):
        self.api_key=api_key; self.base_url=base_url.rstrip("/"); self.model=model
        self.temperature=temperature; self.max_tokens=max_tokens; self.timeout=timeout
    def chat(self, messages, json_mode=True):
        body={"model":self.model,"messages":messages,"temperature":self.temperature,"max_tokens":self.max_tokens}
        if json_mode: body["response_format"]={"type":"json_object"}
        r=requests.post(self.base_url+"/chat/completions",
                        headers={"Authorization":"Bearer "+self.api_key}, json=body, timeout=self.timeout)
        r.raise_for_status(); d=r.json()
        return d["choices"][0]["message"]["content"], d.get("usage", {})