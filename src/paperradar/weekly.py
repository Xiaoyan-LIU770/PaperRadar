"""仅使用数据库证据生成简报；无摘要或无分析卡时明确保留未知。"""
import json
from collections import Counter
from pathlib import Path

from .core import ROOT, dump, now
from .tracking import open_db, papers


def paper_link(p):
    if p["identifiers"].get("doi"): return "https://doi.org/" + p["identifiers"]["doi"][0]
    if p["identifiers"].get("arxiv"): return "https://arxiv.org/abs/" + p["identifiers"]["arxiv"][0]
    return next(iter(p.get("links", [])), "")


def analysis_cards(extra_path=None):
    cards = {}
    paths=[ROOT/'reports/analysis_cards.json',ROOT/'reports/weekly_analysis.json']
    if extra_path and Path(extra_path) not in paths:paths.append(Path(extra_path))
    for path in paths:
        if path.exists():
            for card in json.loads(path.read_text()): cards[card["paper_id"]] = card
    return cards


def generate_weekly(db_path, run_id, output_path):
    conn = open_db(db_path)
    try:
        row = conn.execute("SELECT payload FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row: raise ValueError("运行不存在")
        m = json.loads(row[0]); lib = papers(conn)
        if "new_paper_ids" not in m: raise ValueError("第一阶段运行请阅读原报告；周报使用第二阶段运行")
        rel = {pid: json.loads(r) for pid,r in conn.execute("SELECT paper_id,relevance FROM topic_papers WHERE topic_id=?", (m["topic_id"],))}
        levels = Counter(rel[pid].get("level", "待筛选") for pid in m["new_paper_ids"])
        reviewed = sum(rel[pid].get("status") == "Codex已逐条复核" for pid in m["new_paper_ids"])
        title_only = sum("仅标题" in rel[pid].get("review_basis", "") for pid in m["new_paper_ids"])
        changes = [{"paper_id":r[0],"source_key":r[1],"fields":json.loads(r[2])} for r in conn.execute("SELECT paper_id,source_key,fields FROM metadata_changes WHERE run_id=?", (run_id,))]
        cited = set()
        def cite(pid):
            p = lib[pid]; cited.add(pid)
            return f'[{p["title"]}]({paper_link(p)})（`{pid}`）'
        lines = ["# Weekly Research Update｜每周研究进展", "", f'主题：{m["topic"]["topic_zh"]}。追踪时间：{m["created_at"]}；报告生成：{now()}。', f'运行：`{run_id}`；模式：`{m["mode"]}`；范围：{m["topic"]["date_from"]}—{m["topic"]["date_to"]}。', ""]
        if m.get("test_data") and m.get('mode')=='scheduled-test':
            lines += ['**隔离定时验收：本次检索调用真实接口，写入专用测试库；新增相对测试基线计算，不代表正式库本期新增。**','']
        elif m.get("test_data"):
            lines += ["**测试回放：使用明确标注的测试快照，在隔离库中模拟先后发现；不代表这些论文本周新发表，也不改变正式文献库。**", ""]
        if m["status"] != "success":
            lines += ["**本报告不完整：存在数据来源失败。失败不等于没有新论文。**", ""]
        if m["status"] == "failed":
            lines += ["本次全部来源检索失败，新增总数无法判断；没有推进成功检索时间锚点。请查看下列错误并在恢复后重试。", ""]
        else:
            qualifier = "成功请求中已确认" if m["status"] != "success" else "本次返回记录中已确认"
            label = "首次基线收录" if m["mode"] == "baseline" else "相对该主题历史库新增"
            lines += [f'{qualifier}，{label}{m["new_papers"]}条；其中首次进入整个文献库{m["new_library_records"]}条、命中该主题已有{m["existing_papers_seen"]}条。', f'新增相关性：高{levels["高"]}、中{levels["中"]}、低{levels["低"]}、待筛选{levels["待筛选"]}。等级来自当前已保存判断，规则初筛仍须Codex复核。', ""]
            lines += [f'Codex已复核{reviewed}条；其中{title_only}条仅依据标题复核，未进行摘要级精读。', ""]
            if not m["new_paper_ids"]:
                lines += ["## 本期无新增", "", "本次成功返回的记录未产生确定新增。此结论只适用于以下检索范围和已返回页面，不能推断整个领域没有新论文。", ""]
        lines += ["## 来源、检索表达式与覆盖限制", ""]
        for s in m["sources"]:
            lines += [f'- {s["source"]}：{s["status"]}；返回数：{s.get("raw_count") if s.get("raw_count") is not None else "未知（请求失败）"}；日期合格数：{s.get("eligible_date_count", "未知")}；缺摘要：{s.get("missing_abstract_count", "未单独统计")}。']
            if s.get("parameters"): lines += ["  参数：`" + json.dumps(s["parameters"], ensure_ascii=False) + "`。"]
            if s.get("error"): lines += ["  错误：" + s["error"]]
            if s.get('coverage_gaps'):lines += ['  **部分窗口未完整覆盖：**'+json.dumps(s['coverage_gaps'],ensure_ascii=False)]
            elif s.get("stopped_at_limit"): lines += ["  **达到单来源50条上限，尚有未取回记录，覆盖不完整。**"]
        policy = '采用标题与摘要的多组检索，在日期窗口内按相关性排序，每请求最多50条并限制总请求次数；组间记录可能重复，入库统一去重。' if m.get('search_strategy')=='blue-peled-v2' else '采用本次保存的排序，每来源最多50条。'
        lines += ["", "arXiv采用首次提交日期，OpenAlex采用其出版日期；新增指第一次被本主题库发现，不等于刚发表。常规更新从最近双来源成功截止日回查35天。"+policy+"延迟超过回查期的索引和旧论文仅修改元数据可能漏检。", "", f'元数据变化：{m["metadata_changed_papers"]}条；身份冲突暂存待复核：{m["unresolved_count"]}条。变化保存在数据库差异表；旧摘要及人工筛选保留。', ""]
        for change in changes:
            lines += [f'- {cite(change["paper_id"])}：来源{change["source_key"]}，变化字段：{", ".join(change["fields"])}。旧判断可能需要复核，不能直接视为科研突破。']
        cards = analysis_cards(Path(output_path).parent.parent/'weekly_analysis.json')
        ranked = sorted(m["new_paper_ids"], key=lambda pid: ({"高":0,"中":1,"低":2}.get(rel[pid].get("level"),3), pid))
        selected = [pid for pid in ranked if rel[pid].get("level") in ("高", "中")][:10]
        if m["new_paper_ids"]:
            lines += ["", "## 值得关注的新增记录", "", "以下最多10条优先阅读高、中相关记录；只取得标题/摘要，不开展全文质量评价。", ""]
            for pid in selected:
                p, r = lib[pid], rel[pid]; card = cards.get(pid)
                lines += [f'### {cite(pid)}', "", f'相关性：{r.get("level")}；审核：{r.get("status", "待复核")}。{r.get("reason", "")}', ""]
                work_types=sorted({sr.get('work_type') for sr in p.get('source_records',[]) if sr.get('work_type')})
                if work_types:lines += ['来源记录类型：'+', '.join(work_types)+'。','']
                if any('figshare' in d.lower() for d in p['identifiers'].get('doi',[])) or any(t in ('dataset','supplementary-material') for t in work_types):
                    lines += ['**资料类型复核：此记录可能是数据集、仓储合集或补充文件，需核对主体论文及版本关系，不能直接计作一项独立研究。**','']
                if card and card.get("evidence_quote") and card["evidence_quote"] in (p.get(card.get("evidence_field", "abstract")) or ""):
                    lines += ["**基于摘要分析；文献直接报告的内容（摘要转述）**", "", "- 研究问题：" + card["question"], "- 方法：" + card["method"], "- 主要发现：" + card["finding"], "- 作者提出的技术特点：" + card["author_proposed_feature"], "- 摘要核对短语：`" + card["evidence_quote"] + "`。"]
                    for old in card.get("comparison_ids", []):
                        if old not in m["previous_topic_ids"]: raise ValueError("比较对象不是该次运行的历史文献")
                        lines += ["- 历史比较对象：" + cite(old)]
                    if card.get("comparison"):
                        lines += ["- **综合推断（不是单篇论文直接结论）**：" + card["comparison"]]
                else:
                    lines += ["**尚无法判断**：未有经核对的分析卡，不能由题目推断研究方法或实验发现。" + ("当前仅有摘要，需Codex阅读后补充。" if p.get("abstract") else "本次缺少摘要。")]
                lines += ["- 推荐精读：" + ("是，" + r.get("reason", "请核实全文证据") if r.get("recommend_close_reading") else "暂不推荐；先补充证据或完成相关性复核。"), "- 证据限制：未核实全文、实验对照、关键实验条件和重复性；不能据此跨论文比较性能。", ""]
            if not selected: lines += ["新增记录暂无已确认高、中相关论文；需完成主题筛选后再判断研究价值。", ""]
            lines += ["## 与历史研究的联系、新方向和未解问题", "", "**综合推断的范围**：仅对上文有明确历史比较对象和证据的分析卡进行联系与区别讨论。没有比较证据时，尚无法判断是否出现新方法、新材料或新方向；不把新入库等同于研究突破。", "", "**尚未解决**：需取得全文与补充信息，核对研究设计、测试条件和版本关系；摘要未披露的信息保留未知。", ""]
        lines += ["## 可追溯文件", "", f'正式运行请求与原始响应：`results/{run_id}/`；SQLite运行记录：`runs`；元数据差异：`metadata_changes`；版本关系：`review_pairs`/`unresolved_observations`。', "", "## 引用核对", "", "以下标识均从本次使用的数据库读取；已入库不等于已经核实全文结论。", ""]
        for pid in sorted(cited):
            p=lib[pid]; lines += [f'- {cite(pid)}；DOI：{p.get("identifiers",{}).get("doi",[])}；arXiv：{p.get("identifiers",{}).get("arxiv",[])}。']
        output_path = Path(output_path); output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proof = {"run_id": run_id, "status": m["status"], "test_data": m.get("test_data",False), "new_papers":m["new_papers"], "new_relevance_counts":dict(levels), "references": [{"paper_id":pid,"identifiers":lib[pid]["identifiers"],"url":paper_link(lib[pid])} for pid in sorted(cited)], "all_reference_ids_in_database": all(pid in lib for pid in cited)}
        dump(output_path.with_suffix(".references.json"), proof)
        return proof
    finally: conn.close()
