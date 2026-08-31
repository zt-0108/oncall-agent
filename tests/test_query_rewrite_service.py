from app.services.query_rewrite_service import QueryRewriteService


def test_clear_query_skips_model_call():
    called = False

    def rewrite_model(_query: str) -> str:
        nonlocal called
        called = True
        return "不应调用"

    result = QueryRewriteService(rewrite_model).rewrite("checkoutservice 数据库连接失败")

    assert result.applied is False
    assert result.reason == "clear_query"
    assert result.rewritten_query == result.original_query
    assert called is False


def test_noisy_query_is_rewritten_and_identifiers_are_preserved():
    noisy_query = (
        "2026-08-31 10:20:30 ERROR checkoutservice request_id=abc123 "
        "DBConnectionError 数据库连接被拒绝，请帮我看看这个问题该按哪个 Runbook 排查"
    )
    service = QueryRewriteService(
        lambda _query: "checkoutservice DBConnectionError 数据库连接被拒绝 Runbook 排查"
    )

    result = service.rewrite(noisy_query)

    assert result.attempted is True
    assert result.applied is True
    assert result.reason in {"ambiguous_reference", "log_noise", "long_query"}
    assert "checkoutservice" in result.rewritten_query
    assert "DBConnectionError" in result.rewritten_query


def test_rewrite_falls_back_when_identifier_is_lost():
    query = "刚才 checkoutservice 报 DBConnectionError，这个该怎么排查"
    result = QueryRewriteService(lambda _query: "数据库连接失败排查").rewrite(query)

    assert result.attempted is True
    assert result.applied is False
    assert result.reason == "identifier_loss"
    assert result.rewritten_query == query


def test_rewrite_failure_falls_back_to_original_query():
    query = "刚才 checkoutservice 的那个错误怎么处理"

    def failed_model(_query: str) -> str:
        raise RuntimeError("model unavailable")

    result = QueryRewriteService(failed_model).rewrite(query)

    assert result.attempted is True
    assert result.applied is False
    assert result.reason == "rewrite_error"
    assert result.rewritten_query == query
