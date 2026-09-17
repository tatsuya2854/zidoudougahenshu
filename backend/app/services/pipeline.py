"""解析パイプライン（analyze）と書き出しパイプライン（export）。"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import select

from ..config import get_settings
from ..db import session_scope
from ..models import Candidate, Creator, Export, HumanEdit, Transcript, Video
from ..providers.registry import get_llm_provider, get_transcription_provider, get_video_provider
from ..schemas.transcript import TranscriptData
from . import cost
from .candidates import select_candidates
from .jobs import JobContext
from .render import render_candidate

log = logging.getLogger(__name__)


def _creator_vocab(creator: Creator) -> list[str]:
    terms = [d.get("term", "") for d in (creator.dictionary or [])]
    dna = creator.creator_dna or {}
    terms += [d.get("term", "") for d in dna.get("dictionary", [])]
    seen, out = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def run_analyze(job_id: str, video_id: str, n_candidates: int = 10) -> Any:
    def _fn(ctx: JobContext) -> dict[str, Any]:
        s_cfg = get_settings()
        vp = get_video_provider()
        with session_scope() as s:
            video = s.get(Video, video_id)
            if not video:
                raise RuntimeError("video not found")
            creator = s.get(Creator, video.creator_id)
            assert creator
            video.status = "analyzing"
            s.add(video)
            s.commit()
            vpath = Path(video.path)
            vtitle = video.title
            cid = creator.id
            vocab = _creator_vocab(creator)

        # 1) 音声抽出
        ctx.update("音声を抽出", 0.02, "16kHz mono に変換中")
        work = s_cfg.work_dir / video_id
        work.mkdir(parents=True, exist_ok=True)
        audio = vp.extract_audio(vpath, work / "audio.mp3")

        # 2) 文字起こし
        stt = get_transcription_provider()
        ctx.update("文字起こし", 0.1, f"provider={stt.name}")
        tr = stt.transcribe(audio, language="ja", vocabulary=vocab,
                            progress=lambda m, p: ctx.update("文字起こし", 0.1 + 0.45 * p, m))
        with session_scope() as s:
            for old in s.exec(select(Transcript).where(Transcript.video_id == video_id)).all():
                s.delete(old)
            s.add(Transcript(video_id=video_id, creator_id=cid, provider=stt.name, model=getattr(stt, "model", ""),
                             language=tr.transcript.language, data=tr.transcript.model_dump()))
            s.commit()
            cost.record_usage(s, category="transcription", usage=tr.usage, creator_id=cid, video_id=video_id, job_id=job_id)

        if ctx.cancelled():
            raise RuntimeError("cancelled")

        # 3) 候補選定
        llm = get_llm_provider()
        ctx.update("Shorts候補を選定", 0.6, f"provider={llm.name} model={getattr(llm, 'model', '')}")
        with session_scope() as s:
            creator = s.get(Creator, cid)
            assert creator
            res = select_candidates(llm, creator, tr.transcript, vtitle, n=n_candidates)
            cost.record_usage(s, category="llm", usage=res.usage, creator_id=cid, video_id=video_id, job_id=job_id)
            for old in s.exec(select(Candidate).where(Candidate.video_id == video_id)).all():
                s.delete(old)
            s.commit()
            cl = res.parsed
            rows = []
            for i, c in enumerate(cl.candidates):  # type: ignore[attr-defined]
                rows.append(Candidate(video_id=video_id, creator_id=cid, rank=i + 1, start_sec=c.start_sec, end_sec=c.end_sec,
                                      score=c.score, title=c.title, data=c.model_dump()))
            s.add_all(rows)
            s.commit()
            cand_ids = [(r.id, r.start_sec) for r in rows]
            notes = getattr(cl, "overall_notes", "")

        # 4) サムネイル
        ctx.update("サムネイル生成", 0.85)
        thumbs = work / "thumbs"
        thumbs.mkdir(exist_ok=True)
        for i, (cid_, st) in enumerate(cand_ids):
            try:
                vp.extract_frame(vpath, st + 0.5, thumbs / f"{cid_}.jpg", width=480)
            except Exception as e:  # noqa: BLE001
                log.warning("thumb failed: %s", e)
            ctx.update("サムネイル生成", 0.85 + 0.13 * (i + 1) / max(len(cand_ids), 1))

        with session_scope() as s:
            video = s.get(Video, video_id)
            if video:
                video.status = "analyzed"
                s.add(video)
                s.commit()
        ctx.update("完了", 1.0, "解析完了")
        return {"candidates": len(cand_ids), "notes": notes}

    return _fn


def run_export(job_id: str, export_ids: list[str], options: dict[str, Any]) -> Any:
    def _fn(ctx: JobContext) -> dict[str, Any]:
        s_cfg = get_settings()
        done: list[str] = []
        for k, export_id in enumerate(export_ids):
            if ctx.cancelled():
                raise RuntimeError("cancelled")
            base = k / len(export_ids)
            span = 1.0 / len(export_ids)
            with session_scope() as s:
                exp = s.get(Export, export_id)
                if not exp:
                    continue
                cand = s.get(Candidate, exp.candidate_id)
                video = s.get(Video, exp.video_id)
                creator = s.get(Creator, exp.creator_id)
                trow = s.exec(select(Transcript).where(Transcript.video_id == exp.video_id)).first()
                if not (cand and video and creator and trow):
                    exp.status = "failed"
                    exp.error = "candidate/video/transcript が見つからない"
                    s.add(exp)
                    s.commit()
                    continue
                exp.status = "rendering"
                s.add(exp)
                s.commit()
                transcript = TranscriptData(**trow.data)
                out_dir = s_cfg.exports_dir / creator.id / video.id
                out_dir.mkdir(parents=True, exist_ok=True)
                safe_title = "".join(ch for ch in cand.title if ch.isalnum() or ch in "ー_-　 ")[:24].strip() or "short"
                out_path = out_dir / f"{cand.rank:02d}_{safe_title}_{cand.id}.mp4"
                # ORM オブジェクトをセッション外で使うため必要値を取り出す
                v_copy, c_copy, cand_copy = Video(**video.model_dump()), Creator(**creator.model_dump()), Candidate(**cand.model_dump())
            try:
                plan, usages = render_candidate(
                    v_copy, c_copy, cand_copy, transcript, out_path,
                    reframe_style=options.get("reframe_style"), captions=options.get("captions", True),
                    progress=lambda stage, p: ctx.update(f"[{k + 1}/{len(export_ids)}] {stage}", base + span * p, cand_copy.title),
                )
                with session_scope() as s:
                    exp = s.get(Export, export_id)
                    assert exp
                    exp.status = "done"
                    exp.path = str(out_path)
                    exp.edit_plan = plan
                    exp.finished_at = datetime.utcnow()
                    s.add(exp)
                    s.commit()
                    for cat, u in usages:
                        cost.record_usage(s, category=cat, usage=u, creator_id=exp.creator_id, video_id=exp.video_id, job_id=job_id)
                    if out_path.exists():
                        cost.record_storage(s, size_bytes=out_path.stat().st_size, creator_id=exp.creator_id, video_id=exp.video_id)
                done.append(export_id)
            except Exception as e:  # noqa: BLE001
                log.exception("export failed")
                with session_scope() as s:
                    exp = s.get(Export, export_id)
                    if exp:
                        exp.status = "failed"
                        exp.error = str(e)[-1000:]
                        exp.finished_at = datetime.utcnow()
                        s.add(exp)
                        s.commit()
        ctx.update("完了", 1.0, f"{len(done)}/{len(export_ids)} 本書き出し")
        return {"done": done}

    return _fn


def record_decision(candidate_id: str, decision: str) -> None:
    with session_scope() as s:
        cand = s.get(Candidate, candidate_id)
        if not cand:
            raise KeyError(candidate_id)
        before = cand.decision
        cand.decision = decision
        cand.decided_at = datetime.utcnow()
        s.add(cand)
        s.add(HumanEdit(creator_id=cand.creator_id, video_id=cand.video_id, candidate_id=cand.id,
                        action=f"candidate_{decision}", before={"decision": before, "score": cand.score, "rank": cand.rank},
                        after={"decision": decision}))
        s.commit()
