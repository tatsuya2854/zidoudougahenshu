"""SRT 読込 → TranscriptData / API 経由の SRT 登録 + reuse_transcript 解析 / faster-whisper ラッパの検証（stub）。"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.transcription.srt_import import parse_srt
from tests.make_sample import make_sample

SRT_3 = """﻿1
00:00:01,000 --> 00:00:03,500
こんにちは、みなさん

2
00:00:04,000 --> 00:00:06,200
今日は<i>なんぼ</i>で買えたか
話します

3
00:00:07,000 --> 00:00:09,000
結論から言うと
"""


def _wait_job(client: TestClient, job_id: str, timeout: float = 120) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.3)
    raise AssertionError("job timeout")


# ---------- parse_srt ----------


def test_parse_srt_basic() -> None:
    t = parse_srt(SRT_3)
    assert t.provider == "srt_import" and len(t.segments) == 3
    assert [(s.start, s.end) for s in t.segments] == [(1.0, 3.5), (4.0, 6.2), (7.0, 9.0)]
    # 表示タグは剥がす。方言（なんぼ）はそのまま。複数行は連結
    assert t.segments[1].text == "今日はなんぼで買えたか話します"
    assert t.segments[1].words == []
    assert t.duration == 9.0
    # 単語TS が無くても文字を等分配して字幕に出せる
    assert len(t.words_between(1.0, 2.0)) >= 3


def test_parse_srt_reversed_time_rejected() -> None:
    bad = "1\n00:00:05,000 --> 00:00:07,000\nあと\n\n2\n00:00:01,000 --> 00:00:03,000\nさき\n"
    with pytest.raises(ValueError, match="前の cue より前"):
        parse_srt(bad)
    with pytest.raises(ValueError, match="終了が開始以下"):
        parse_srt("1\n00:00:05,000 --> 00:00:05,000\nx\n")
    with pytest.raises(ValueError, match="時刻行を読めません"):
        parse_srt("1\nこれは時刻ではない\nx\n")


def test_parse_srt_overlap_clamped_and_duration() -> None:
    srt = "1\n00:00:01,000 --> 00:00:04,000\nA\n\n2\n00:00:03,000 --> 00:00:06,000\nB\n"
    t = parse_srt(srt)
    assert (t.segments[1].start, t.segments[1].end) == (4.0, 6.0)
    # 動画尺を超える cue は「別の動画の字幕」として拒否
    with pytest.raises(ValueError, match="動画の長さ"):
        parse_srt(srt, duration=1.5)
    # 少しはみ出す程度なら末尾を動画尺に丸める
    t2 = parse_srt(srt, duration=5.0)
    assert t2.segments[-1].end == 5.0 and t2.duration == 5.0


# ---------- API: SRT 登録 → reuse_transcript 解析 ----------


def test_srt_import_and_reuse_transcript(tmp_path: Path) -> None:
    sample = make_sample(tmp_path / "sample.mp4", seconds=90)
    # 90 秒に 4 秒間隔で cue を敷く
    cues = []
    for i in range(20):
        st = 1 + i * 4.4
        cues.append(f"{i + 1}\n{_ts(st)} --> {_ts(st + 3.5)}\nここは{i + 1}番目の話。なんぼでも語れる\n")
    srt = "\n".join(cues)

    with TestClient(app) as client:
        c = client.post("/api/creators", json={"name": "SRT太郎", "dictionary": [{"term": "なんぼ", "category": "dialect"}]}).json()
        with open(sample, "rb") as f:
            v = client.post(f"/api/creators/{c['id']}/videos", files={"file": ("sample.mp4", f, "video/mp4")}).json()
        vid = v["id"]
        assert client.get(f"/api/videos/{vid}/transcript").status_code == 404

        # 壊れた SRT は 400 + 日本語メッセージ
        r = client.post(f"/api/videos/{vid}/transcript/srt", files={"file": ("bad.srt", b"1\nfoo\nbar\n", "text/plain")})
        assert r.status_code == 400 and "SRT" in r.json()["detail"]
        r = client.post(f"/api/videos/{vid}/transcript/srt", files={"file": ("sjis.srt", "1\n00:00:01,000 --> 00:00:02,000\nあ".encode("shift_jis"), "text/plain")})
        assert r.status_code == 400 and "UTF-8" in r.json()["detail"]

        r = client.post(f"/api/videos/{vid}/transcript/srt", files={"file": ("sub.srt", srt.encode("utf-8"), "text/plain")})
        assert r.status_code == 200, r.text
        assert r.json()["segments"] == 20 and r.json()["provider"] == "srt_import"
        tr = client.get(f"/api/videos/{vid}/transcript").json()
        assert tr["provider"] == "srt_import" and len(tr["data"]["segments"]) == 20

        cost0 = client.get(f"/api/videos/{vid}/cost").json()
        assert cost0["entries"] == 1  # アップロード時の storage のみ

        job = client.post(f"/api/videos/{vid}/analyze?reuse_transcript=true").json()
        assert job["payload"]["reuse_transcript"] is True
        j = _wait_job(client, job["id"])
        assert j["status"] == "done", j.get("error")
        cands = client.get(f"/api/videos/{vid}/candidates").json()
        assert len(cands) >= 1
        # 文字起こしは置き換わらず、原価も transcription 分は増えない（llm の 1 件だけ増える）
        tr2 = client.get(f"/api/videos/{vid}/transcript").json()
        assert tr2["provider"] == "srt_import" and tr2["id"] == tr["id"]
        cost1 = client.get(f"/api/videos/{vid}/cost").json()
        assert cost1["entries"] == cost0["entries"] + 1, cost1

        # reuse 無しなら通常どおり文字起こし（mock）し直して transcription の原価が付く
        job = client.post(f"/api/videos/{vid}/analyze").json()
        j = _wait_job(client, job["id"])
        assert j["status"] == "done", j.get("error")
        assert client.get(f"/api/videos/{vid}/transcript").json()["provider"] == "mock"
        cost2 = client.get(f"/api/videos/{vid}/cost").json()
        assert cost2["entries"] == cost1["entries"] + 2, cost2


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


# ---------- faster-whisper ラッパ（モデルは stub。実モデルはネットワーク依存なので CI では読まない） ----------


def test_faster_whisper_wrapper_with_stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fw = pytest.importorskip("faster_whisper")
    from app.providers.transcription.faster_whisper_stt import FasterWhisperTranscription

    created: list[dict] = []

    class StubModel:
        def __init__(self, model_size, device, compute_type):
            created.append({"model": model_size, "device": device, "compute_type": compute_type})

        def transcribe(self, path, **kw):
            assert kw["word_timestamps"] is True and kw["vad_filter"] is True
            assert "なんぼ" in (kw["hotwords"] or "") and "なんぼ" in (kw["initial_prompt"] or "")
            w = lambda t, s, e: SimpleNamespace(word=t, start=s, end=e, probability=0.9)  # noqa: E731
            segs = [
                SimpleNamespace(text=" なんぼやねん", start=0.5, end=1.5, words=[w("なんぼ", 0.5, 1.0), w("やねん", 1.0, 1.5)]),
                SimpleNamespace(text="   ", start=2.0, end=2.5, words=[]),  # 空は落とす
                SimpleNamespace(text="次の話", start=3.0, end=4.0, words=None),
            ]
            return iter(segs), SimpleNamespace(duration=6.0)

    monkeypatch.setattr(fw, "WhisperModel", StubModel)
    FasterWhisperTranscription._cache_key, FasterWhisperTranscription._cache_model = None, None
    try:
        stt = FasterWhisperTranscription(model_size="tiny", device="cpu")
        assert stt.compute_type == "int8"
        msgs: list[str] = []
        res = stt.transcribe(tmp_path / "dummy.wav", vocabulary=["なんぼ"], progress=lambda m, p: msgs.append(m))
        res2 = stt.transcribe(tmp_path / "dummy.wav", vocabulary=["なんぼ"])
        assert len(created) == 1  # 1 プロセス 1 回のロード
        assert any("モデル読込中" in m for m in msgs)
        t = res.transcript
        assert t.provider == "faster_whisper" and t.model == "tiny" and t.duration == 6.0
        assert [s.text for s in t.segments] == ["なんぼやねん", "次の話"]
        assert t.segments[0].words[0].text == "なんぼ" and t.segments[0].words[0].confidence == 0.9
        assert res.usage.quantities == {"minutes": 0.1} and res.usage.meta["device"] == "cpu"
        assert res2.transcript.segments[0].text == "なんぼやねん"
    finally:
        FasterWhisperTranscription._cache_key, FasterWhisperTranscription._cache_model = None, None


def test_faster_whisper_load_failure_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fw = pytest.importorskip("faster_whisper")
    from app.providers.transcription.faster_whisper_stt import FasterWhisperTranscription

    class Broken:
        def __init__(self, *a, **k):
            raise OSError("no network")

    monkeypatch.setattr(fw, "WhisperModel", Broken)
    FasterWhisperTranscription._cache_key, FasterWhisperTranscription._cache_model = None, None
    stt = FasterWhisperTranscription(model_size="nope-model", device="cpu")
    with pytest.raises(RuntimeError, match="モデル読込に失敗.*nope-model.*no network"):
        stt.transcribe(tmp_path / "dummy.wav")
