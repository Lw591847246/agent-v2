import requests
import json
from app.config.loader import config
from app.core.logger import logger
from app.config.loader import config


class LLMTimeoutError(Exception):
    """Ollama 调用超时（连接超时 / 读取超时）。
    由全局异常处理器接住，返回明确错误码（如 LLM_TIMEOUT），前端据此提示"稍后重试"。"""
    pass


class OllamaClient:
    # 内置兜底：配置完全缺失时仍能跑通（本机单机场景）
    DEFAULT_HOST = "http://localhost:11434"

    def _resolve_host(self, service: str = None) -> str:
        """统一 Ollama 源解析，三级回落：
            ollama.hosts.{service}  ->  ollama.base_url  ->  DEFAULT_HOST
        - 单机：只填 ollama.base_url，所有服务共用一个源。
        - 多机分片：在 ollama.hosts 下按 service 覆盖，未覆盖的自动回落 base_url。
        去掉尾部斜杠，避免拼出 //api/chat。
        """
        if service:
            host = config.get(f"ollama.hosts.{service}")
            if host:
                return host.rstrip("/")
        base = config.get("ollama.base_url")
        if base:
            return base.rstrip("/")
        return self.DEFAULT_HOST

    def get_unload_hosts(self):
        """返回所有需要清理显存的 Ollama 源集合（去重）。
        分片部署时模型可能分布在不同 host，这里把 hosts.* 与 base_url 全部收集，
        对每个源广播 keep_alive=0；打到没有该模型的源是无害 no-op，无需精确 model->host 映射。
        """
        hosts = set()
        hosts_map = config.get("ollama.hosts", {}) or {}
        for h in hosts_map.values():
            if h:
                hosts.add(h.rstrip("/"))
        base = config.get("ollama.base_url")
        if base:
            hosts.add(base.rstrip("/"))
        if not hosts:
            hosts.add(self.DEFAULT_HOST)
        return hosts

    def _get_url(self, service: str):
        return f"{self._resolve_host(service)}/api/chat"

    def _timeout(self, stream: bool):
        """分层超时：connect 短(秒级失败)，read 长(给足生成时间)。
        元组 (connect, read) 让 Ollama 进程没起 / 端口不通时立刻失败，而不是空等 read 全时长。
        """
        connect = config.get("ollama.timeout.connect", 8)
        if stream:
            read = config.get("ollama.timeout.read_stream", 300)
        else:
            read = config.get("ollama.timeout.read_chat", 300)
        return (connect, read)

    def _build_payload(self, model_key: str, messages: list, stream: bool, keep_alive: str, format: str,
                       temperature: float):
        model = config.get(f"ollama.models.{model_key}")
        if not model:
            raise ValueError(f"未配置模型: ollama.models.{model_key}")
        if str(keep_alive) == "-1":
            keep_alive = "-1m"

        num_predict = int(
            config.get(f"ollama.num_predict.{model_key}",
                       config.get("ollama.num_predict.default", 4096))
        )

        # num_ctx 四级回落：num_ctx.{model_key} -> num_ctx.default -> default_num_ctx -> 32768
        num_ctx = int(
            config.get(f"ollama.num_ctx.{model_key}",
                       config.get("ollama.num_ctx.default",
                                  config.get("ollama.default_num_ctx", 32768)))
        )

        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "keep_alive": keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        }
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
                res = requests.post(url, json=payload, timeout=self._timeout(False))
            except requests.exceptions.Timeout as e:
                logger.error(f"【OllamaClient】请求超时 service={service} model={model_key} url={url}: {e}")
                raise LLMTimeoutError(f"Ollama 调用超时(service={service})") from e
            try:
                if res.status_code != 200:
                    logger.error(f"【OllamaClient】HTTP {res.status_code}: {res.text[:500]}")
                res.raise_for_status()

                data = res.json()
                t.from_ollama_response(data)
                msg = data.get("message", {}) or {}
                thinking = (msg.get("thinking") or "").strip()
                content = (msg.get("content") or "").strip()
                if thinking:
                    return f"<think>\n{thinking}\n</think>\n\n{content}"
                return content
            except requests.exceptions.RequestException as e:
                logger.error(f"【OllamaClient】请求失败: {e}")
                raise
            except Exception as e:
                logger.error(f"【OllamaClient】响应解析失败: {e}")
                raise

    def stream(self, service: str, model_key: str, messages: list, keep_alive: str = "-1", temperature: float = 0.6):
        url = self._get_url(service)
        payload = self._build_payload(model_key, messages, True, keep_alive, "", temperature)

        from app.core.llm_tracker import track_llm

        with track_llm(service=service, model_key=model_key) as t:
            try:
                # stream=True 下 read 超时 = 相邻两块的间隔，同时也是"首 token 超时"
                with requests.post(url, json=payload, stream=True, timeout=self._timeout(True)) as r:
                    if r.status_code != 200:
                        logger.error(f"【OllamaClient】流式 HTTP {r.status_code}: {r.text[:500]}")
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if line:
                            data = json.loads(line)
                            t.from_ollama_chunk(data)
                            msg = data.get("message", {}) or {}
                            thinking_chunk = msg.get("thinking")
                            content_chunk = msg.get("content")

                            if thinking_chunk:
                                # 流式时把思考内容直接输出
                                yield thinking_chunk
                            if content_chunk:
                                # 如果前面没吐过开标签，这里可以做个标记，简单起见直接吐正文
                                yield content_chunk

                            if data.get("done"):
                                break
            except requests.exceptions.Timeout as e:
                logger.error(f"【OllamaClient】流式超时 service={service} model={model_key} url={url}: {e}")
                raise LLMTimeoutError(f"Ollama 流式调用超时(service={service})") from e
            except requests.exceptions.RequestException as e:
                logger.error(f"【OllamaClient】流式请求失败: {e}")
                raise


ollama_client = OllamaClient()
