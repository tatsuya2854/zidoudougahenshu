"""候補の手直し（overrides）: PUT → HumanEdit 記録 → 書き出しに反映 → reset。mock provider で完走する。"""
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

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


def _human_edits(candidate_id: str) -> list:
    from app.db import session_scope
    from app.models import HumanEdit

    with session_scope() as s:
        rows = s.exec(select(HumanEdit).where(HumanEdit.candidate_id == candidate_id).order_by(HumanEdit.created_at)).all()
        return [(r.action, r.before, r.after) for r in rows]


def test_candidate_overrides_roundtrip(tmp_path: Path) -> None:
    sample = make_sample(tmp_path / "sample.mp4", seconds=150)
    with TestClient(app) as client:
        c = client.post("/api/creators", json={"name": "手直し花子"}).json()
        with open(sample, "rb") as f:
            v = client.post(f"/api/creators/{c['id']}/videos", files={"file": ("sample.mp4", f, "video/mp4")}).json()
        j = _wait_job(client, client.post(f"/api/videos/{v['id']}/analyze").json()["id"])
        assert j["status"] == "done", j.get("error")
        cands = client.get(f"/api/videos/{v['id']}/candidates").json()
        cand = cands[0]
        assert cand["overrides"] == {}  # 新規は空
        cid = cand["id"]

        # 字幕の初期値（自動分割・絶対秒）
        cap = client.get(f"/api/candidates/{cid}/captions").json()
        assert cap["source"] == "auto" and len(cap["cues"]) >= 1
        assert all(cand["start_sec"] <= q["start"] < cand["end_sec"] + 0.01 for q in cap["cues"])

        # ── 検証エラー ──
        assert client.put(f"/api/candidates/{cid}/overrides", json={"end_sec": cand["start_sec"] + 2}).status_code == 400  # 短すぎ
        assert client.put(f"/api/candidates/{cid}/overrides", json={"start_sec": cand["end_sec"] + 1}).status_code == 400  # 逆転
        assert client.put(f"/api/candidates/{cid}/overrides", json={"end_sec": 10_000}).status_code == 400  # 動画外
        assert client.put(f"/api/candidates/{cid}/overrides", json={"crop_x": 1.5}).status_code == 400
        assert client.put(f"/api/candidates/{cid}/overrides", json={"crop_mode": "diagonal"}).status_code == 400
        assert client.put(f"/api/candidates/{cid}/overrides", json={"unknown_key": 1}).status_code == 400
        assert _human_edits(cid) == []  # 失敗は記録しない

        # ── 手直し: 尺 12 秒・タイトル・字幕本文（方言はそのまま）・手動構図・字幕サイズ/位置 ──
        new_start = round(cand["start_sec"] + 1.0, 3)
        new_end = round(new_start + 12.0, 3)
        edited_cues = [{"start": new_start + 0.2, "end": new_start + 3.0, "text": "なんぼやねん\nこれ"},
                       {"start": new_start + 3.5, "end": new_start + 6.0, "text": "ほんまに？"},
                       {"start": new_start + 6.5, "end": new_start + 8.0, "text": ""}]  # 空 = 出さない
        r = client.put(f"/api/candidates/{cid}/overrides", json={
            "start_sec": new_start, "end_sec": new_end, "title": " 手直しタイトル ", "captions": edited_cues,
            "crop_mode": "manual", "crop_x": 0.2, "font_size_ratio": 0.05, "caption_position": "top",
        })
        assert r.status_code == 200, r.text
        ov = r.json()["overrides"]
        assert ov["start_sec"] == new_start and ov["end_sec"] == new_end and ov["title"] == "手直しタイトル"
        assert ov["crop_mode"] == "manual" and ov["crop_x"] == 0.2 and ov["caption_position"] == "top"
        assert len(ov["captions"]) == 3
        # 元の候補値は残る（before/after の基準）
        c2 = next(x for x in client.get(f"/api/videos/{v['id']}/candidates").json() if x["id"] == cid)
        assert c2["start_sec"] == cand["start_sec"] and c2["title"] == cand["title"]

        # 部分更新: null でキー削除、他は保持
        r = client.put(f"/api/candidates/{cid}/overrides", json={"caption_position": None})
        assert r.status_code == 200 and "caption_position" not in r.json()["overrides"] and r.json()["overrides"]["crop_mode"] == "manual"

        # captions は override 済みなのでそれが返る
        cap = client.get(f"/api/candidates/{cid}/captions").json()
        assert cap["source"] == "override" and cap["cues"][0]["text"] == "なんぼやねん\nこれ" and cap["start_sec"] == new_start

        # HumanEdit: 2 回の PUT が before/after で残る
        edits = _human_edits(cid)
        assert [a for a, _, _ in edits] == ["candidate_edited", "candidate_edited"]
        assert edits[0][1] == {"overrides": {}, "start_sec": cand["start_sec"], "end_sec": cand["end_sec"], "title": cand["title"]}
        assert edits[0][2]["overrides"]["title"] == "手直しタイトル"
        assert edits[1][1]["overrides"]["caption_position"] == "top" and "caption_position" not in edits[1][2]["overrides"]

        # ── 書き出し: overrides の尺・字幕・構図が反映される ──
        r = client.post(f"/api/videos/{v['id']}/exports", json={"candidate_ids": [cid], "options": {}}).json()
        j = _wait_job(client, r["job"]["id"])
        assert j["status"] == "done", j.get("error")
        exp = next(e for e in client.get(f"/api/videos/{v['id']}/exports").json() if e["candidate_id"] == cid and e["status"] == "done")
        plan = exp["edit_plan"]
        assert plan["overrides"]["title"] == "手直しタイトル" and plan["title"] == "手直しタイトル"
        assert plan["keep_ranges"] == [[new_start, new_end]]
        assert plan["reframe_style"] == "manual" and len(plan["crop"]["keyframes"]) == 1
        assert plan["decisions"][0]["human_status"] == "modified"
        assert plan["decisions"][0]["original"]["time_start"] == cand["start_sec"]
        assert [q["text"] for q in plan["caption_cues"]] == ["なんぼやねん\nこれ", "ほんまに？"]  # 空文は落ちる、相対秒
        assert abs(plan["caption_cues"][0]["start"] - 0.2) < 0.01
        # crop_x=0.2 → 左寄り（中央より左）
        from app.services.reframe import compute_crop_size

        cw, _ = compute_crop_size(v["width"], v["height"], 1080, 1920)
        assert plan["crop"]["keyframes"][0]["x"] == int((v["width"] - cw) * 0.2) // 2 * 2
        assert plan["caption_style"]["font_size_ratio"] == 0.05 and plan["caption_style"]["position"] == "bottom"  # position は null で戻した
        out = Path(exp["path"])
        assert out.exists()
        from app.providers.registry import get_video_provider

        info = get_video_provider().probe(out)
        assert (info.width, info.height) == (1080, 1920)
        assert abs(info.duration - 12.0) < 0.6, info.duration  # overrides の尺
        ass = out.with_suffix(".ass").read_text(encoding="utf-8")
        assert r"なんぼやねん\Nこれ" in ass and "ほんまに？" in ass
        assert ass.count("Dialogue:") == 2
        assert ",96," in ass  # font_size_ratio 0.05 * 1920

        # ── reset: 空に戻り HumanEdit に残る ──
        r = client.post(f"/api/candidates/{cid}/reset")
        assert r.status_code == 200 and r.json()["overrides"] == {}
        edits = _human_edits(cid)
        assert edits[-1][0] == "candidate_reset" and edits[-1][1]["overrides"]["title"] == "手直しタイトル" and edits[-1][2] == {"overrides": {}}
        assert client.get(f"/api/candidates/{cid}/captions").json()["source"] == "auto"

        # face_track / center / blur_fit も overrides 経由で通る（center は顔検出なし）
        r = client.put(f"/api/candidates/{cands[1]['id']}/overrides", json={"crop_mode": "center"})
        assert r.status_code == 200
        r = client.post(f"/api/videos/{v['id']}/exports", json={"candidate_ids": [cands[1]["id"]], "options": {"reframe_style": "face_track"}}).json()
        j = _wait_job(client, r["job"]["id"])
        assert j["status"] == "done", j.get("error")
        exp = next(e for e in client.get(f"/api/videos/{v['id']}/exports").json() if e["candidate_id"] == cands[1]["id"])
        assert exp["edit_plan"]["reframe_style"] == "center" and exp["edit_plan"]["overrides"] == {"crop_mode": "center"}
        assert exp["edit_plan"]["decisions"][0]["human_status"] == "modified"

        assert client.put("/api/candidates/nope/overrides", json={}).status_code == 404
        assert client.post("/api/candidates/nope/reset").status_code == 404


def test_old_schema_db_gets_overrides_column(tmp_path: Path) -> None:
    """overrides 列の無い旧 DB でも、接続時のマイグレーションで SELECT / INSERT が通る。"""
    from app.models import Candidate, ensure_candidate_overrides_column

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE candidate (
        id VARCHAR PRIMARY KEY, video_id VARCHAR NOT NULL, creator_id VARCHAR NOT NULL, rank INTEGER NOT NULL,
        start_sec FLOAT NOT NULL, end_sec FLOAT NOT NULL, score FLOAT NOT NULL, title VARCHAR NOT NULL,
        data JSON, decision VARCHAR NOT NULL, decided_at DATETIME, created_at DATETIME NOT NULL)""")
    con.execute("INSERT INTO candidate VALUES ('old1','v','c',1,0,10,50,'旧','{}','pending',NULL,'2024-01-01 00:00:00')")
    con.commit()
    con.close()

    engine = create_engine(f"sqlite:///{db}")
    # 接続フック（models._migrate_on_connect）が列を足す → 明示呼び出しは何もしない（冪等）
    with Session(engine) as s:
        row = s.get(Candidate, "old1")
        assert row is not None and row.overrides == {}  # DEFAULT '{}' が効く
        row.overrides = {"title": "直した"}
        s.add(row)
        s.commit()
    assert ensure_candidate_overrides_column(engine) == []
    with Session(engine) as s:
        assert s.get(Candidate, "old1").overrides == {"title": "直した"}

    # フックを介さない生の旧 DB に対する明示マイグレーションも動く
    db2 = tmp_path / "old2.db"
    con = sqlite3.connect(db2)
    con.execute("CREATE TABLE candidate (id VARCHAR PRIMARY KEY, title VARCHAR)")
    con.commit()
    con.close()
    from app.models import _migrate_dbapi

    con = sqlite3.connect(db2)
    assert _migrate_dbapi(con) == ["candidate.overrides"]
    assert _migrate_dbapi(con) == []
    assert "overrides" in [r[1] for r in con.execute("PRAGMA table_info(candidate)")]
    con.close()
