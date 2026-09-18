"""キー無し（mock provider）で アップロード→解析→候補10本→書き出し が通ることを確認。"""
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from tests.make_sample import make_sample


def _wait_job(client: TestClient, job_id: str, timeout: float = 300) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.5)
    raise AssertionError("job timeout")


def test_end_to_end(tmp_path: Path) -> None:
    sample = make_sample(tmp_path / "sample.mp4", seconds=150)
    with TestClient(app) as client:
        assert client.get("/api/status").json()["providers"]["llm"]["effective"] == "mock"
        c = client.post("/api/creators", json={"name": "テスト太郎", "dictionary": [{"term": "なんぼ", "category": "dialect"}]}).json()
        with open(sample, "rb") as f:
            v = client.post(f"/api/creators/{c['id']}/videos", files={"file": ("sample.mp4", f, "video/mp4")}).json()
        assert v["duration_sec"] > 140 and v["width"] == 1280

        job = client.post(f"/api/videos/{v['id']}/analyze").json()
        j = _wait_job(client, job["id"])
        assert j["status"] == "done", j.get("error")
        cands = client.get(f"/api/videos/{v['id']}/candidates").json()
        assert len(cands) >= 10, len(cands)
        assert client.get(f"/api/videos/{v['id']}/transcript").status_code == 200
        assert client.get(f"/api/candidates/{cands[0]['id']}/thumb").status_code == 200

        client.post(f"/api/candidates/{cands[0]['id']}/decision", json={"decision": "accepted"})
        client.post(f"/api/candidates/{cands[1]['id']}/decision", json={"decision": "rejected"})

        r = client.post(f"/api/videos/{v['id']}/exports", json={"candidate_ids": [cands[0]["id"], cands[2]["id"]], "options": {}}).json()
        j = _wait_job(client, r["job"]["id"])
        assert j["status"] == "done", j.get("error")
        exps = client.get(f"/api/videos/{v['id']}/exports").json()
        # 書き出し = 採用。HumanEdit に記録される（Phase5 の学習材料）
        cands2 = {c["id"]: c for c in client.get(f"/api/videos/{v['id']}/candidates").json()}
        assert cands2[cands[2]["id"]]["decision"] == "accepted"
        done = [e for e in exps if e["status"] == "done"]
        assert len(done) == 2, exps
        out = Path(done[0]["path"])
        assert out.exists() and out.stat().st_size > 10_000
        assert out.with_suffix(".ass").exists()
        # 縦になっているか
        from app.providers.registry import get_video_provider

        info = get_video_provider().probe(out)
        assert (info.width, info.height) == (1080, 1920)

        cost = client.get(f"/api/videos/{v['id']}/cost").json()
        assert cost["entries"] >= 4 and "transcription" in cost["by_category"]

        # blur_fit スタイルも通す
        r = client.post(f"/api/videos/{v['id']}/exports", json={"candidate_ids": [cands[3]["id"]], "options": {"reframe_style": "blur_fit"}}).json()
        j = _wait_job(client, r["job"]["id"])
        assert j["status"] == "done", j.get("error")
