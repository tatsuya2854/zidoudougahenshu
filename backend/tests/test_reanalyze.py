"""書き出し済みの動画を再解析してもジョブが落ちず、旧候補は superseded として残る（qa 指摘の回帰）。"""
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from tests.make_sample import make_sample


def _wait(client, job_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.5)
    raise AssertionError("job timeout")


def test_reanalyze_after_export(tmp_path: Path) -> None:
    sample = make_sample(tmp_path / "s.mp4", seconds=60)
    with TestClient(app) as client:
        c = client.post("/api/creators", json={"name": "再解析"}).json()
        with open(sample, "rb") as f:
            v = client.post(f"/api/creators/{c['id']}/videos", files={"file": ("s.mp4", f, "video/mp4")}).json()
        assert _wait(client, client.post(f"/api/videos/{v['id']}/analyze").json()["id"])["status"] == "done"
        cands = client.get(f"/api/videos/{v['id']}/candidates").json()
        exported_id = cands[0]["id"]
        assert _wait(client, client.post(f"/api/videos/{v['id']}/exports", json={"candidate_ids": [exported_id]}).json()["job"]["id"])["status"] == "done"

        j = _wait(client, client.post(f"/api/videos/{v['id']}/analyze?reuse_transcript=true").json()["id"])
        assert j["status"] == "done", j.get("error")
        assert client.get(f"/api/videos/{v['id']}").json()["status"] == "analyzed"
        new = client.get(f"/api/videos/{v['id']}/candidates").json()
        assert new and all(c["id"] != exported_id for c in new)  # 旧候補は一覧に出ない
        exps = client.get(f"/api/videos/{v['id']}/exports").json()
        assert exps[0]["candidate_id"] == exported_id and exps[0]["status"] == "done"  # 完成品は残る
        assert client.get(f"/api/videos/{v['id']}/bundle.zip").status_code == 200
