"""
API 接口层单元测试（不启动 lifespan，因此不依赖大模型与真实数据库）

覆盖本轮新增/加固的接口行为：
1. GET /api/threads：返回结构、参数透传、异常降级为 500
2. GET /api/threads/{id}：会话不存在返回 404、存在返回 turns
3. POST /api/upload：thread_id 白名单校验（防路径穿越）
4. GET /api/files：越权访问 output 之外返回 403、目录不存在返回 404

说明：TestClient 只有在 `with` 语句中才会执行 FastAPI 的 lifespan，
这里刻意不使用 with，避免 init_main_agent() 去连大模型与 SQLite。
"""

import io

import pytest
from fastapi.testclient import TestClient

from app.api import server


@pytest.fixture()
def client() -> TestClient:
    return TestClient(server.app)


# ============================================================
# GET /api/threads
# ============================================================
class TestThreadsEndpoint:
    def test_returns_expected_shape(self, client, monkeypatch):
        captured = {}

        def fake_list_threads(db_path, limit, include_test):
            captured["db_path"] = db_path
            captured["limit"] = limit
            captured["include_test"] = include_test
            return {
                "threads": [
                    {
                        "thread_id": "t1",
                        "title": "第一轮问题",
                        "preview": "第一轮问题",
                        "has_title": True,
                        "steps": 12,
                        "session_path": "",
                        "updated_at": "2026-09-20T16:10:47+08:00",
                    }
                ],
                "total": 1,
                "returned": 1,
            }

        # server 里是 from ... import list_threads，需 patch server 模块内的引用
        monkeypatch.setattr(server, "list_threads", fake_list_threads)

        response = client.get("/api/threads?limit=7&include_test=true")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["threads"][0]["title"] == "第一轮问题"
        # 查询参数正确透传给聚合函数
        assert captured["limit"] == 7
        assert captured["include_test"] is True
        assert captured["db_path"] is None      # 用默认库路径

    def test_default_limit_when_omitted(self, client, monkeypatch):
        captured = {}

        def fake_list_threads(db_path, limit, include_test):
            captured["limit"] = limit
            captured["include_test"] = include_test
            return {"threads": [], "total": 0, "returned": 0}

        monkeypatch.setattr(server, "list_threads", fake_list_threads)
        response = client.get("/api/threads")

        assert response.status_code == 200
        assert captured["limit"] == 30
        assert captured["include_test"] is False

    def test_failure_returns_500_not_crash(self, client, monkeypatch):
        def boom(db_path, limit, include_test):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(server, "list_threads", boom)
        response = client.get("/api/threads")

        assert response.status_code == 500
        assert "database is locked" in response.json()["detail"]


# ============================================================
# GET /api/threads/{thread_id}
# ============================================================
class TestThreadDetailEndpoint:
    def test_unknown_thread_returns_404(self, client, monkeypatch):
        monkeypatch.setattr(
            server,
            "get_thread_detail",
            lambda thread_id, db_path: {
                "thread_id": thread_id, "found": False, "turns": [], "turn_count": 0
            },
        )

        response = client.get("/api/threads/does-not-exist")

        assert response.status_code == 404
        assert "会话不存在" in response.json()["detail"]

    def test_known_thread_returns_turns(self, client, monkeypatch):
        monkeypatch.setattr(
            server,
            "get_thread_detail",
            lambda thread_id, db_path: {
                "thread_id": thread_id,
                "found": True,
                "turns": [
                    {"query": "第一问", "answer": "第一答"},
                    {"query": "第二问", "answer": ""},
                ],
                "turn_count": 2,
            },
        )

        response = client.get("/api/threads/abc-123")

        assert response.status_code == 200
        body = response.json()
        assert body["thread_id"] == "abc-123"
        assert body["turn_count"] == 2
        # 第二轮答案为空（任务被取消），接口应原样返回而不是报错
        assert body["turns"][1]["answer"] == ""

    def test_detail_failure_returns_500(self, client, monkeypatch):
        def boom(thread_id, db_path):
            raise RuntimeError("corrupt db")

        monkeypatch.setattr(server, "get_thread_detail", boom)
        response = client.get("/api/threads/abc")

        assert response.status_code == 500
        assert "corrupt db" in response.json()["detail"]


# ============================================================
# POST /api/upload：thread_id 校验（防路径穿越）
# ============================================================
class TestUploadValidation:
    def test_rejects_path_traversal_thread_id(self, client):
        response = client.post(
            "/api/upload",
            files={"files": ("a.csv", io.BytesIO(b"x,y\n1,2\n"), "text/csv")},
            data={"thread_id": "../../etc"},
        )

        assert response.status_code == 400
        assert "thread_id" in response.json()["detail"]

    def test_rejects_empty_thread_id(self, client):
        response = client.post(
            "/api/upload",
            files={"files": ("a.csv", io.BytesIO(b"x"), "text/csv")},
            data={"thread_id": ""},
        )

        # 空 thread_id 一定被拒绝，但拦截层取决于请求编码方式：
        # - Starlette TestClient 把空表单字段解析为 None，由 Form(...) 必填校验拦下 -> 422
        # - 真实浏览器 FormData 传空字符串时，由本接口的白名单校验拦下 -> 400
        # 两者都表示请求被拒绝、不会创建任何目录，故都视为通过
        assert response.status_code in (400, 422)

    def test_rejects_oversized_thread_id(self, client):
        response = client.post(
            "/api/upload",
            files={"files": ("a.csv", io.BytesIO(b"x"), "text/csv")},
            data={"thread_id": "a" * 65},
        )

        assert response.status_code == 400

    def test_accepts_uuid_thread_id_and_strips_unsafe_filename(self, client, monkeypatch, tmp_path):
        # 把上传根目录指向临时目录，避免污染真实的 app/updated
        monkeypatch.setattr(server, "updated_dir", tmp_path)

        tid = "9d6a2c2c-1475-4e9d-9171-7ef9dbb6de5a"
        response = client.post(
            "/api/upload",
            # 客户端传入带目录穿越的文件名，服务端应只保留文件名本身
            files={"files": ("../../../evil.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
            data={"thread_id": tid},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "uploaded"
        assert body["files"] == ["evil.csv"]

        # 文件必须落在会话目录内，不能跑到上层
        saved = tmp_path / f"session_{tid}" / "evil.csv"
        assert saved.exists()
        assert saved.read_bytes() == b"a,b\n1,2\n"
        assert not (tmp_path.parent / "evil.csv").exists()


# ============================================================
# GET /api/files：安全边界
# ============================================================
class TestFilesEndpointGuards:
    def test_rejects_path_outside_output_dir(self, client, tmp_path):
        outside = tmp_path / "secret.txt"
        outside.write_text("top secret", encoding="utf-8")

        response = client.get("/api/files", params={"path": str(tmp_path)})

        assert response.status_code == 403
        assert "只能访问输出目录" in response.json()["detail"]

    def test_missing_dir_returns_404(self, client):
        missing = server.output_dir / "session_definitely_not_exists"
        response = client.get("/api/files", params={"path": str(missing)})

        assert response.status_code == 404

    def test_lists_files_inside_output_dir(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "output_dir", tmp_path)
        session = tmp_path / "session_x"
        session.mkdir()
        (session / "report.md").write_text("# 报告", encoding="utf-8")
        (session / "chart.png").write_bytes(b"\x89PNG")

        response = client.get("/api/files", params={"path": str(session)})

        assert response.status_code == 200
        names = {f["name"] for f in response.json()["files"]}
        assert names == {"report.md", "chart.png"}
        # 每个条目都应带下载所需的绝对路径与大小
        for item in response.json()["files"]:
            assert item["path"]
            assert item["size"] > 0
            assert item["mtime"] > 0


# ============================================================
# GET /api/download：安全边界
# ============================================================
class TestDownloadGuards:
    def test_rejects_path_outside_output_dir(self, client, tmp_path):
        outside = tmp_path / "secret.txt"
        outside.write_text("x", encoding="utf-8")

        response = client.get("/api/download", params={"path": str(outside)})

        assert response.status_code == 403

    def test_missing_file_returns_404(self, client):
        missing = server.output_dir / "session_none" / "nope.pdf"
        response = client.get("/api/download", params={"path": str(missing)})

        assert response.status_code in (403, 404)
