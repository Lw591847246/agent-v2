"""API 冒烟测试：只验证路由挂载 + 基本响应，不连真实 DB。"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """构造最小 FastAPI app，不跑 bootstrap 的 mysql/skill 初始化。"""
    from fastapi import FastAPI
    from app.api.router import api_router
    from app.middleware.exception import register_exception_handlers
    from app.middleware.auth import auth_interceptor

    app = FastAPI()
    register_exception_handlers(app)
    # 覆盖 auth，跳过 Redis
    app.dependency_overrides[auth_interceptor] = lambda: {"userId": "1", "username": "test"}
    app.include_router(api_router)
    return TestClient(app)


def test_job_not_found(client):
    with patch("app.api.v1.job_controller.task_repo") as mock_repo:
        mock_repo.get.return_value = None
        r = client.get("/agent/v1/job/xxx")
        assert r.status_code == 404


def test_conversation_new(client):
    with patch("app.api.v1.conversation_controller.conversation_service") as mock_conv:
        mock_conv.new_conversation.return_value = "s-new"
        r = client.post("/agent/v1/conversation/new")
        assert r.status_code == 200
        assert r.json()["session_id"] == "s-new"