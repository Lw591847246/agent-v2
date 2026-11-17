import requests
import json
from app.config.loader import config
from app.core.logger import logger


class OllamaClient:
    def _get_url(self, service: str):
        host = config.get(f"ollama.hosts.{service}", "http://localhost:11434")
        return f"{host}/api/chat"

    def _build_payload(self, model_key: str, messages: list, stream: bool, keep_alive: str, format: str,
                       temperature: float):
        model = config.get(f"ollama.models.{model_key}")
        if not model:
            raise ValueError(f"未配置模型: ollama.models.{model_key}")
        # ⭐ 防呆处理：如果是 "-1"，自动补全为 "-1m"，防止 Ollama 报错
        if str(keep_alive) == "-1":
            keep_alive = "-1m"

        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "keep_alive": keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": 16384,  # ⭐ 新增：强制开启 16K 上下文窗口
                "num_predict": 4096  # ⭐ 核心：将最大生成 Token 数提升到 4096
            }
        }

        # ⭐ 只有明确要求 json 时才传 format 字段
        if format == "json":
            payload["format"] = "json"

        return payload

    def chat(self, service: str, model_key: str, messages: list, keep_alive: str = "-1", format: str = "",
             temperature: float = 0.7) -> str:
        url = self._get_url(service)
        payload = self._build_payload(model_key, messages, False, keep_alive, format, temperature)

        from app.core.llm_tracker import track_llm

        with track_llm(service=service, model_key=model_key) as t:
            try:
                res = requests.post(url, json=payload, timeout=300)
                if res.status_code != 200:
                    logger.error(f"【OllamaClient】HTTP {res.status_code}: {res.text[:500]}")
                res.raise_for_status()

                data = res.json()
                # ✅ 关键：从原始 dict 里取 token，再返回 content
                t.from_ollama_response(data)

                return data.get("message", {}).get("content", "")
            except Exception as e:
                logger.error(f"【OllamaClient】请求失败: {e}")
                raise e

    def stream(self, service: str, model_key: str, messages: list, keep_alive: str = "-1", temperature: float = 0.6):
        url = self._get_url(service)
        payload = self._build_payload(model_key, messages, True, keep_alive, "", temperature)

        from app.core.llm_tracker import track_llm

        with track_llm(service=service, model_key=model_key) as t:
            try:
                with requests.post(url, json=payload, stream=True, timeout=300) as r:
                    if r.status_code != 200:
                        logger.error(f"【OllamaClient】流式 HTTP {r.status_code}: {r.text[:500]}")
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if line:
                            data = json.loads(line)
                            t.from_ollama_chunk(data)  # ← 每行都喂，done=true 时记录 token
                            chunk = data.get("message", {}).get("content", "")
                            if chunk:
                                yield chunk
                            if data.get("done"):
                                break
            except Exception as e:
                logger.error(f"【OllamaClient】流式请求失败: {e}")
                raise e


ollama_client = OllamaClient()