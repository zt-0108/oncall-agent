"""LLM 工厂。"""

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.config import config


class LLMFactory:
    DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @staticmethod
    def create_chat_model(
        model: str | None = None,
        temperature: float = 0.7,
        streaming: bool = True,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> ChatOpenAI:
        api_secret = SecretStr(api_key) if api_key else config.dashscope_api_secret
        return ChatOpenAI(
            model=model or config.dashscope_model,
            temperature=temperature,
            streaming=streaming,
            base_url=base_url or LLMFactory.DASHSCOPE_BASE_URL,
            api_key=api_secret,
            extra_body={"stream": streaming},
        )


llm_factory = LLMFactory()
