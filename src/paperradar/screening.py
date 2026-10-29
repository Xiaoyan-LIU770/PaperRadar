"""透明的主题初筛规则；Agent 逐条复核可通过 review.json 覆盖。"""
import json
import re
from pathlib import Path
from .core import ROOT, connect_db, dump, export_library, now, resolve_paper_id

PATTERNS = {
    "perovskite": r"perovskit",
    "blue": r"\bblue\b|sky[ -]blue|deep[ -]blue|pure[ -]blue",
    "device": r"light[ -]emitting diode|\bpeleds?\b|\bleds?\b|electroluminescen",
    "operational": r"operational|operating|half[ -]life|\bt50\b|\bt₅₀\b|\bt90\b|lifetime|device stability|ion migration|halide segregation|spectral stability",
    "stability": r"stabil|degrad|lifetime|passivat|defect|segregat|ion migration",
    "review": r"\breview\b|roadmap|perspective|progress|advances|challenges",
}


def has(kind, text):
    return bool(re.search(PATTERNS[kind], text, re.I))


def evidence(record):
    result = [{"field": "title", "source": record["sources"][0], "quote": record["title"]}]
    abstract = record.get("abstract") or ""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", abstract)
    selected = [s for s in sentences if has("operational", s) or has("stability", s)]
    if not selected and sentences and sentences[0]: selected = sentences[:1]
    for sentence in selected[:2]:
        result.append({"field": "abstract", "source": record.get("abstract_source"), "quote": sentence})
    return result


def classify(record):
    title = record["title"]; abstract = record.get("abstract") or ""; text = title + " " + abstract
    if has("perovskite", text) and has("blue", text) and has("device", text):
        if abstract and has("blue", title) and has("operational", abstract):
            level = "高"; reason = "标题聚焦蓝光器件，摘要明确涉及工作稳定性、寿命或电致发光失稳机制，直接对应研究问题。"
        else:
            level = "中"; reason = "涉及蓝光钙钛矿或发光器件，可提供材料/效率/背景线索；现有标题或摘要未充分证明蓝光器件工作寿命提升。"
    elif has("perovskite", text) and has("blue", title):
        level = "中"; reason = "聚焦蓝光钙钛矿发光材料，但现有证据未确认电致发光器件的工作寿命，作为辅助材料研究保留。"
    else:
        level = "低"; reason = "现有标题或摘要的重点不在蓝光钙钛矿电致发光器件及其工作寿命，暂不纳入核心阅读。"
    if not abstract: reason += " 本次缺少摘要，只能依据标题暂判，需补充摘要或全文。"
    retracted = any("撤稿" in w for w in record.get("warnings", []))
    return {"level": level, "reason": reason, "evidence": evidence(record), "recommend_close_reading": level == "高" and bool(abstract) and not retracted, "method": "可复现主题规则初筛；不使用引用次数", "status": "待Codex逐条复核", "screened_at": now()}


def screen(run_id=None):
    if run_id is None: run_id = json.loads((ROOT / "logs/latest_run.json").read_text())["run_id"]
    run_dir = ROOT / "results" / run_id
    manifest = json.loads((run_dir / "manifest.json").read_text())
    review_path = ROOT / "config/screening_review.json"
    review = json.loads(review_path.read_text()) if review_path.exists() else {}
    conn = connect_db(ROOT / "database/papers.sqlite")
    papers = []
    with conn:
        for paper_id in manifest["paper_ids"]:
            paper_id = resolve_paper_id(conn, paper_id)
            p = json.loads(conn.execute("SELECT payload FROM papers WHERE paper_id=?", (paper_id,)).fetchone()[0])
            decision = classify(p)
            if paper_id in review:
                decision.update(review[paper_id]); decision["status"] = "Codex已逐条复核"
                decision["method"] = "Codex阅读保存的标题/摘要后逐条复核；不调用第三方模型API，不使用引用次数"
            for e in decision["evidence"]:
                assert e["quote"] and e["quote"] in (p.get(e["field"]) or ""), (paper_id, e)
            p["relevance"] = decision
            conn.execute("UPDATE papers SET payload=? WHERE paper_id=?", (json.dumps(p, ensure_ascii=False), paper_id))
            papers.append(p)
        counts = {level: sum(p["relevance"]["level"] == level for p in papers) for level in ["高", "中", "低"]}
        manifest["screening_counts"] = counts
        manifest["screening_reviewed_count"] = sum(p["relevance"]["status"] == "Codex已逐条复核" for p in papers)
        conn.execute("UPDATE runs SET payload=? WHERE run_id=?", (json.dumps(manifest, ensure_ascii=False), run_id))
    conn.close()
    dump(run_dir / "papers.json", papers); dump(run_dir / "manifest.json", manifest)
    dump(run_dir / "screening.json", [{"paper_id": p["paper_id"], "title": p["title"], **p["relevance"]} for p in papers])
    export_library(ROOT / "database/papers.sqlite", ROOT / "database")
    print(json.dumps({"run_id": run_id, "counts": counts, "reviewed": manifest["screening_reviewed_count"]}, ensure_ascii=False))

