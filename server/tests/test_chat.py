"""Scoped Q&A chat: endpoints, history persistence, and context builders.

Ollama is stubbed — these tests are about what the chat is TOLD (the context
must carry the operator's numbers) and what it stores, not about the model.
"""

import json
from pathlib import Path

import pytest

from app.vlm.client import OllamaVlmClient

PDFS_DIR = Path(__file__).parent.parent.parent / "pdfs"
TABLES_DIR = Path(__file__).parent.parent.parent / "tables"


@pytest.fixture
def mock_chat(monkeypatch):
    """Record what the model is asked; answer with two deltas."""
    calls: list[dict] = []

    def fake_stream(self, messages, model=None, num_ctx=8192, timeout_s=None):
        calls.append({"messages": messages, "model": model})
        yield "stubbed "
        yield "answer"

    monkeypatch.setattr(OllamaVlmClient, "text_available", lambda self, m=None: True)
    monkeypatch.setattr(OllamaVlmClient, "chat_stream", fake_stream)
    return calls


def _upload_doc(client, project_id=None):
    pdf = PDFS_DIR / "117-626-141_4_Rev.3_BLANK.pdf"
    url = (
        f"/api/projects/{project_id}/documents"
        if project_id
        else "/api/documents"
    )
    with open(pdf, "rb") as f:
        r = client.post(url, files={"file": (pdf.name, f, "application/pdf")})
    if r.status_code == 409:  # module-scoped client: same sha across tests
        docs = client.get("/api/documents").json()
        return next(d for d in docs if d["filename"] == pdf.name)
    assert r.status_code == 201, r.text
    return r.json()


def test_document_chat_answers_from_document_context(client, mock_chat):
    doc = _upload_doc(client)
    client.delete(f"/api/chat/document/{doc['id']}/messages")
    r = client.post(
        f"/api/chat/document/{doc['id']}/messages",
        json={"content": "how many holes?"},
    )
    assert r.status_code == 200
    assert r.text == "stubbed answer"

    # the system prompt carries THIS document's data and nothing else
    system = mock_chat[0]["messages"][0]
    assert system["role"] == "system"
    assert doc["filename"] in system["content"]
    assert "one engineering drawing document" in system["content"]

    # both turns persisted, in order
    msgs = client.get(f"/api/chat/document/{doc['id']}/messages").json()
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "how many holes?"),
        ("assistant", "stubbed answer"),
    ]


def test_follow_up_replays_history(client, mock_chat):
    doc = _upload_doc(client)
    client.delete(f"/api/chat/document/{doc['id']}/messages")
    client.post(f"/api/chat/document/{doc['id']}/messages", json={"content": "q1"})
    client.post(f"/api/chat/document/{doc['id']}/messages", json={"content": "q2"})

    roles = [m["role"] for m in mock_chat[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert mock_chat[1]["messages"][1]["content"] == "q1"
    assert mock_chat[1]["messages"][2]["content"] == "stubbed answer"


def test_clear_chat(client, mock_chat):
    doc = _upload_doc(client)
    client.post(f"/api/chat/document/{doc['id']}/messages", json={"content": "q"})
    r = client.delete(f"/api/chat/document/{doc['id']}/messages")
    assert r.status_code == 204
    assert client.get(f"/api/chat/document/{doc['id']}/messages").json() == []


def test_scope_validation(client, mock_chat):
    assert client.get("/api/chat/document/99999/messages").status_code == 404
    assert client.get("/api/chat/basement/1/messages").status_code == 404
    # the summary scope has exactly one conversation
    assert client.get("/api/chat/summary/7/messages").status_code == 404
    assert client.get("/api/chat/summary/0/messages").status_code == 200


def test_chat_unavailable_is_503_not_a_hang(client, monkeypatch):
    monkeypatch.setattr(OllamaVlmClient, "text_available", lambda self, m=None: False)
    r = client.post("/api/chat/summary/0/messages", json={"content": "hi"})
    assert r.status_code == 503
    assert "Ollama" in r.json()["detail"]


def test_project_chat_context_carries_summary_and_prices(client, mock_chat, wait_job):
    project_id = client.post("/api/projects", json={"name": "chat proj"}).json()["id"]
    pdf = TABLES_DIR / "NCD5168[_EN](5).pdf"
    with open(pdf, "rb") as f:
        doc = client.post(
            f"/api/projects/{project_id}/documents",
            files={"file": (pdf.name, f, "application/pdf")},
        ).json()
    job = client.post(f"/api/documents/{doc['id']}/table-jobs", json={"vlm": False})
    assert wait_job(client, job.json()["id"])["status"] == "done"

    # approve the BOM table (clearing any flagged rows first) so its rows count,
    # and price one material
    tables = client.get(f"/api/documents/{doc['id']}/tables").json()
    bom = max(tables, key=lambda t: t["row_count"])
    for row in client.get(f"/api/tables/{bom['id']}").json()["rows"]:
        if row["status"] == "needs_review":
            client.patch(f"/api/material-rows/{row['id']}", json={"action": "approve"})
    r = client.patch(f"/api/tables/{bom['id']}", json={"action": "approve"})
    assert r.status_code == 200, r.text
    client.put(
        f"/api/projects/{project_id}/prices",
        json={
            "entries": [
                {"material_key": "L160X160X15", "price": 4.2, "pricing_unit": "per_kg"}
            ]
        },
    )

    client.post(
        f"/api/chat/project/{project_id}/messages",
        json={"content": "what do the legs cost?"},
    )
    system = mock_chat[0]["messages"][0]["content"]
    data = json.loads(system.split("DATA:\n", 1)[1])
    row = next(
        r
        for r in data["materials_with_pricing"]["rows"]
        if r["material_key"] == "L160X160X15"
    )
    assert row["qty"] == 4  # two legs x qty 2, straight from the approved table
    assert row["price"] == 4.2
    assert data["project"]["name"] == "chat proj"


def test_summary_chat_context_carries_order_plans(client, mock_chat, wait_job):
    project_id = client.post("/api/projects", json={"name": "sum proj"}).json()["id"]
    r = client.post(
        f"/api/projects/{project_id}/order-plans",
        json={
            "stock": [{"length_mm": 12000, "price": 100.0}],
            "kerf_mm": 5,
            "pieces": [{"length_mm": 9000, "qty": 2}],
        },
    )
    assert r.status_code == 201, r.text

    client.post("/api/chat/summary/0/messages", json={"content": "waste?"})
    system = mock_chat[0]["messages"][0]["content"]
    data = json.loads(system.split("DATA:\n", 1)[1])
    proj = next(p for p in data["projects"] if p["name"] == "sum proj")
    assert len(proj["order_plans"]) == 1
    assert proj["order_plans"][0]["waste_pct"] == pytest.approx(25.0, abs=0.5)
    assert data["scope"] == "all projects combined"
