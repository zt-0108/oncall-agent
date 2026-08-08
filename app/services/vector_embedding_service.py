"""DashScope 向量嵌入服务。"""

from langchain_core.embeddings import Embeddings
from loguru import logger
from openai import OpenAI

from app.config import config


class DashScopeEmbeddings(Embeddings):
    """兼容 LangChain Embeddings 接口的延迟初始化客户端。"""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        if self._client is not None:
            return self._client
        if not self.api_key or self.api_key == "your-api-key-here":
            raise RuntimeError("未配置 DASHSCOPE_API_KEY")

        self._client = OpenAI(
            api_key=self.api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        logger.info(
            "DashScope Embeddings 初始化完成: model={}, dimensions={}",
            self.model,
            self.dimensions,
        )
        return self._client

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            response = self._get_client().embeddings.create(
                model=self.model,
                input=texts,
                dimensions=self.dimensions,
                encoding_format="float",
            )
            embeddings = [item.embedding for item in response.data]
            if len(embeddings) != len(texts):
                raise RuntimeError(
                    f"嵌入数量不匹配: requested={len(texts)}, returned={len(embeddings)}"
                )
            return embeddings
        except Exception as exc:
            logger.exception("批量嵌入失败")
            raise RuntimeError("批量嵌入失败") from exc

    def embed_query(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("查询文本不能为空")

        try:
            response = self._get_client().embeddings.create(
                model=self.model,
                input=text,
                dimensions=self.dimensions,
                encoding_format="float",
            )
            if not response.data:
                raise RuntimeError("嵌入服务返回空结果")
            return response.data[0].embedding
        except Exception as exc:
            logger.exception("查询嵌入失败")
            raise RuntimeError("查询嵌入失败") from exc


vector_embedding_service = DashScopeEmbeddings(
    api_key=config.dashscope_api_key,
    model=config.dashscope_embedding_model,
    dimensions=1024,
)
