"""SRT 書き出しと ZIP バンドル（MP4＋SRT＋編集計画 JSON）が mock provider で通ることを確認。"""
import io
import json
import re
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.providers.caption.srt_export import build_srt, cues_from_overrides, cues_to_srt
from app.schemas.editing_dna import CaptionStyle
from app.schemas.transcript import Word
from tests.make_sample import make_sample

SRT_HEAD = re.compile(r"^1\n(\d\d:\d\d:\d\d,\d{3}) --> \d\d:\d\d:\d\d,\d{3}\n")


def _wait_job(client: TestClient, job_id: str, timeout: float = 300) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.5)
    raise AssertionError("job timeout")


def test_build_srt_format() -> None:
    """ffmpeg 不要の単体: 形式・クリップ相対時刻・実改行（\\N ではない）。"""
    words = [Word(text="こんにちは、", start=10.0, end=10.6), Word(text="今日は", start=10.6, end=11.0),
             Word(text="なんぼでも", start=11.0, end=11.5), Word(text="喋るで。", start=11.5, end=12.2),
             Word(text="次の話。", start=13.5, end=14.0)]
    srt = build_srt(words, clip_start=10.0, clip_end=20.0, style=CaptionStyle())
    blocks = srt.strip().split("\n\n")
    # 「。」で切れて 18 字 → 14 字で折る（ASS と同じ分割）。折り返しは \N ではなく実改行。方言はそのまま
    assert blocks[0] == "1\n00:00:00,000 --> 00:00:02,200\nこんにちは、今日はなんぼでも\n喋るで。", blocks[0]
    # 1.3 秒の間で区切られ、クリップ相対 3.5 秒から
    assert blocks[1] == "2\n00:00:03,500 --> 00:00:04,000\n次の話。", blocks[1]
    assert "\\N" not in srt
    # overrides.captions 契約（render と同じ）: 元動画の絶対秒 {start,end,text}。区間外は切り落とし、空文は落とす
    over = cues_to_srt(cues_from_overrides(
        [{"start": 11.0, "end": 12.5, "text": "直した字幕"}, {"start": 10.0, "end": 10.5, "text": ""}, {"start": 5.0, "end": 9.0, "text": "区間外"}],
        clip_start=10.0, clip_end=20.0, style=CaptionStyle()))
    assert over == "1\n00:00:01,000 --> 00:00:02,500\n直した字幕\n"


def test_srt_and_bundle(tmp_path: Path) -> None:
    sample = make_sample(tmp_path / "sample.mp4", seconds=60)
    with TestClient(app) as client:
        c = client.post("/api/creators", json={"name": "バンドル太郎"}).json()
        with open(sample, "rb") as f:
            v = client.post(f"/api/creators/{c['id']}/videos", files={"file": ("sample.mp4", f, "video/mp4")}).json()
        with open(sample, "rb") as f:
            v_empty = client.post(f"/api/creators/{c['id']}/videos", files={"file": ("empty.mp4", f, "video/mp4")}).json()

        # 完成品 0 件 → 404 と日本語メッセージ
        r = client.get(f"/api/videos/{v_empty['id']}/bundle.zip")
        assert r.status_code == 404 and "Shorts" in r.json()["detail"]

        j = _wait_job(client, client.post(f"/api/videos/{v['id']}/analyze").json()["id"])
        assert j["status"] == "done", j.get("error")
        cands = client.get(f"/api/videos/{v['id']}/candidates").json()
        assert len(cands) >= 2
        r = client.post(f"/api/videos/{v['id']}/exports", json={"candidate_ids": [cands[0]["id"], cands[1]["id"]], "options": {}}).json()
        j = _wait_job(client, r["job"]["id"])
        assert j["status"] == "done", j.get("error")
        done = [e for e in client.get(f"/api/videos/{v['id']}/exports").json() if e["status"] == "done"]
        assert len(done) == 2

        # SRT（クリップ相対: 最初の cue は 0 秒付近から始まる）
        r = client.get(f"/api/exports/{done[0]['id']}/srt")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-subrip")
        m = SRT_HEAD.match(r.text)
        assert m and m.group(1).startswith("00:00:00,"), r.text[:60]
        assert "\\N" not in r.text
        r = client.get(f"/api/exports/{done[0]['id']}/srt?download=true")
        assert ".srt" in r.headers["content-disposition"]

        # 動画まるごと ZIP
        r = client.get(f"/api/videos/{v['id']}/bundle.zip")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        assert "shorts.zip" in r.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = z.namelist()
            assert "README.txt" in names and "costs.json" in names
            mp4s = sorted(n for n in names if n.endswith(".mp4"))
            assert len(mp4s) == 2 and mp4s[0].startswith("01_") and "/" in mp4s[0]
            for m in mp4s:
                stem = m[:-4]
                assert f"{stem}.srt" in names and f"{stem}.edit_plan.json" in names
                assert z.getinfo(m).file_size == Path(next(e["path"] for e in done if Path(e["path"]).stem == Path(m).stem)).stat().st_size
                plan = json.loads(z.read(f"{stem}.edit_plan.json"))
                assert plan["keep_ranges"] and plan["caption_style"]
                assert SRT_HEAD.match(z.read(f"{stem}.srt").decode("utf-8"))
            readme = z.read("README.txt").decode("utf-8")
            assert "バンドル太郎" in readme and "推定原価合計" in readme
            costs = json.loads(z.read("costs.json"))
            assert costs["entries"] >= 4 and "total_usd" in costs

        # 1 本分 ZIP
        r = client.get(f"/api/exports/{done[1]['id']}/bundle.zip")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            assert len([n for n in z.namelist() if n.endswith(".mp4")]) == 1

        # 存在しない export
        assert client.get("/api/exports/nope/bundle.zip").status_code == 404
        assert client.get("/api/exports/nope/srt").status_code == 404
