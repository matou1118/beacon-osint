"""灯塔采集工具 lighthouse —— 一条命令跑完。

    python lighthouse/run.py --all --limit 20
    python lighthouse/run.py --source mof_gov --dry-run
    python lighthouse/run.py --all --full        # 采集完再跑主项目的库级流水线

与主项目的关系
--------------
* 读 ``config/sources.yaml``（145 个源，已登记的官方公开栏目）
* 输出对齐 ``data/markdown/<source_id>/`` 现有约定，由 ``output/markdown.py`` 渲染
  ——主项目已经会消费这个目录，所以新工具的产出**不需要任何集成改动**
* 抢主项目同一把采集锁 ``data/runtime/collect.lock``，绝不和通宵采集并行写库

它比现有采集链多做的三件事
--------------------------
1. **详情页走 5 级降级阶梯**，每级出的正文都要过质量门禁（``routes.Ladder``）
2. **门禁不过就丢弃 + 记原因**，不拿垃圾充数（原来脏正文直接入库）
3. **摘要是字段拼装不是字符截断**，100% 落在句号上（见 ``quality.build_summary``）
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml                                                    # noqa: E402

from collectors.base import BaseCollector                      # noqa: E402
from collectors.lock import CollectLockBusy, acquire, release  # noqa: E402
from lighthouse.routes import Ladder, build_collector         # noqa: E402
from lighthouse.winflags import install as _install_no_window  # noqa: E402
_install_no_window()  # 本进程 spawn 的任何子进程都不带控制台窗口

from lighthouse.quality import build_summary, extract_fields, judge, load_config  # noqa: E402

SOURCES_YAML = _ROOT / "config" / "sources.yaml"
LOCK_DIR = _ROOT / "data" / "runtime" / "collect.lock"


def log(message: str) -> None:
    print(message, flush=True)


class Progress:
    """全局进度显示。

    为什么必须要有：采集是分钟级到小时级的 IO 等待，不显示进度就分不清
    「正在慢慢跑」和「卡死了」。之前一次全量 109 源跑满 50 分钟被 kill，
    全程只有每源完成后的一行，最后 5 个源是哪个完全看不见。

    两层：
      * 进度行（\\r 原地刷新）：完成数 / 总数、采到、丢弃、用时、ETA、正在跑哪几个源
      * 心跳：默认 20 秒一行，哪怕一个源都没完成也要打，否则安静期看起来像死机
    """

    def __init__(self, total: int, interval: float = 20.0,
                 deadline: float | None = None) -> None:
        self.total = max(1, total)
        self.interval = interval
        self.deadline = deadline
        self.done = 0
        self.records = 0
        self.dropped = 0
        self.inflight: dict[str, float] = {}
        self.started = time.time()
        self.tty = sys.stdout.isatty()
        self._last_beat = self.started

    def start_source(self, source_id: str) -> None:
        self.inflight[source_id] = time.time()

    def finish_source(self, source_id: str, records: int, dropped: int) -> None:
        self.inflight.pop(source_id, None)
        self.done += 1
        self.records += records
        self.dropped += dropped

    def _render(self) -> str:
        elapsed = time.time() - self.started
        rate = self.done / elapsed if elapsed > 0 and self.done else 0.0
        eta = (self.total - self.done) / rate if rate > 0 else 0.0
        running = ",".join(sorted(self.inflight)[:4]) or "-"
        if self.deadline is not None:
            left = max(0.0, self.deadline - time.time())
            # 到点之后 ETA 没意义了，明确说「到点停」，别让人以为还在推进
            tail = f"剩 {left/60:>4.1f}m 后停 | 在跑: {running}" if left > 0 \
                else f"已到时限 | 在跑: {running}"
        else:
            tail = f"ETA {eta/60:>5.1f}m | 在跑: {running}"
        return (f"[{self.done:>3}/{self.total}] 采到 {self.records:>4} 丢弃 {self.dropped:>3} "
                f"| 用时 {elapsed/60:>5.1f}m {tail}")

    def tick(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_beat < self.interval:
            return
        self._last_beat = now
        line = self._render()
        if self.tty:
            print("\r" + line.ljust(120), end="", flush=True)
        else:
            # 重定向到文件时没有 \r 覆盖，每行都要能看全，否则日志等于空白
            print(line, flush=True)

    def newline(self) -> None:
        if self.tty:
            print(flush=True)


def monitor(progress: Progress, stop: "threading.Event") -> None:
    """心跳线程。as_completed 会阻塞在等下一个源上，主线程没法自己打心跳。"""
    while not stop.wait(progress.interval):
        progress.tick()


# ------------------------------------------------------------------ 配置

ALL_STATUSES = ("enabled", "blocked", "pending_reprobe")


def load_sources(statuses: tuple[str, ...] = ("enabled",)) -> list[dict[str, Any]]:
    """按 status 筛源。

    注意：``enabled: true`` 只在筛 enabled 时才作为附加条件。
    blocked / pending_reprobe 的源在 sources.yaml 里 ``enabled`` 本来就是 false，
    如果无条件要求 enabled 为真，--all-statuses 会永远返回空列表——
    这个 bug 让「复探被封的源」这件事从加这个参数起就没生效过。
    """
    with open(SOURCES_YAML, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    sources = data.get("sources") or []
    if not statuses:
        return list(sources)
    picked: list[dict[str, Any]] = []
    for source in sources:
        status = source.get("status")
        if status not in statuses:
            continue
        # enabled 标志只约束 status=enabled 的那一批。加在集合上会把
        # blocked/pending_reprobe（它们的 enabled 本来就是 false）全部误杀，
        # 于是 --all-statuses 只剩 101 个 enabled，复探形同虚设。
        if status == "enabled" and not source.get("enabled"):
            continue
        picked.append(source)
    return picked

def _project_root(cfg: Mapping[str, Any]) -> Path:
    """解析 project_root。相对路径按「lighthouse/ 的上一级」解析，也就是本项目根。

    这个函数存在是因为踩过一次坑：配置里写 project_root: ".."，用
    ``_ROOT / ".."`` 解析，结果影子报告和 markdown 全写到 BeaconOSINT 的
    上一层去了 —— 静默写到项目外面，没有任何报错。
    """
    raw = str((cfg.get("general") or {}).get("project_root", ".")).strip() or "."
    candidate = Path(raw)
    root = candidate if candidate.is_absolute() else (_ROOT / candidate)
    return root.resolve()


def markdown_dir(cfg: Mapping[str, Any]) -> Path:
    return _project_root(cfg) / (cfg.get("general") or {}).get("markdown_dir", "data/markdown")


def report_dir(cfg: Mapping[str, Any]) -> Path:
    path = _project_root(cfg) / (cfg.get("general") or {}).get("report_dir", "data/runtime/lighthouse")
    path.mkdir(parents=True, exist_ok=True)
    return path


# ------------------------------------------------------------------ 单源

def with_defaults(source: Mapping[str, Any], cfg: Mapping[str, Any],
                   archive_pages: int = 0) -> dict[str, Any]:
    """给没写 archive_pages / max_items 的源补上默认值，并把过低的 max_items 抬起来。

    为什么要在这里补而不是改 sources.yaml：
    * 57 个 enabled 源没写 archive_pages，collectors/html_list.py:467 的
      默认值是 1，也就是只看得到首页，109 个源的数据天花板被压在 4260 条。
    * sources.yaml 每条都带人工填的 auth_scope / terms_checked_at / notes。
      scripts/fix_sources.py:57 用 yaml.safe_dump 批量覆写把这些全丢了。
      改默认值只动内存里的副本，磁盘上的登记信息一个字节不变。

    raise_max_items_below：实测 61 个源被 max_items 截断（57 个卡在 20，
    52 个已经是 60）。不抬的话每轮都只是把同一批已知 URL 重新早停一遍，
    增量产出迅速衰减到 0——第 1 轮 +639、第 2 轮 +149、第 3 轮 +9 就是这么来的。
    """
    defaults = cfg.get("collect_defaults") or {}
    merged = dict(source)
    for key in ("archive_pages", "max_items"):
        value = defaults.get(key)
        if value and not merged.get(key):
            merged[key] = int(value)
    # 只填空不覆盖是不够的：52 个源在 sources.yaml 里已经写了 archive_pages: 6，
    # 它们拿不到「翻页多翻 3 倍未采条目」这个实测收益。所以两个字段都按下限抬。
    for key, knob in (("archive_pages", "raise_archive_pages_below"),
                      ("max_items", "raise_max_items_below")):
        floor = defaults.get(knob)
        if floor and int(merged.get(key) or 0) < int(floor):
            merged[key] = int(floor)
    # 通宵调度在产出衰减时会显式加深，用命令行覆盖优先级最高
    if archive_pages and int(merged.get("archive_pages") or 0) < int(archive_pages):
        merged["archive_pages"] = int(archive_pages)
    return merged


def collect_source(source: Mapping[str, Any], cfg: Mapping[str, Any], limit: int,
                   known: set[str], dry_run: bool,
                   deadline: float | None = None,
                   max_stale: int = 0,
                   archive_pages: int = 0) -> dict[str, Any]:
    """采一个源，返回 {records, dropped, notes}。不直接写盘，由上层统一写。

    ``deadline`` 是**全局**轮次时限（epoch 秒），不是单源时限。
    两个时限都要有：
      * 单源 ``rate_limit.source_budget_seconds`` —— 单个源别拖死整轮
      * 全局 ``--max-minutes``          —— 源数一多，单源时限乘起来照样能到
        90 分钟（109 源 / 6 并发 / 单源 300 秒），一次运行必须有硬边界
    """
    source_id = str(source.get("id") or "?")
    notes: list[str] = []
    records: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    started = time.time()

    if deadline is not None and time.time() > deadline:
        return {"source_id": source_id, "records": [], "dropped": [],
                "notes": ["skipped: run time budget exhausted"], "seconds": 0.0,
                "skipped": True}

    # Ladder 要在 collector 建好之后才有 levels 可用，但 levels 来自 cfg 而不是
    # collector，所以顺序是：先建 Ladder 拿 levels → 建 collector → 把 collector
    # 挂回去。之前写成 build_collector(..., ladder.levels) 时 ladder 还没定义。
    ladder = Ladder(cfg, None)
    collector = build_collector(with_defaults(source, cfg, archive_pages), ladder.levels)
    ladder.collector = collector
    try:
        links = list(collector.fetch_list() or [])
    except Exception as exc:                                   # noqa: BLE001
        notes.append(f"fetch_list failed: {type(exc).__name__}: {exc}"[:200])
        notes.extend(_collector_notes(collector))
        return {"source_id": source_id, "records": [], "dropped": [],
                "notes": notes, "seconds": round(time.time() - started, 1)}

    if not links:
        # 列表页也走 5 级阶梯。老链路是 static→render→crawl4ai 一把梭，
        # 失败就 0 条；实测 cd_jinjiang_gov 是 HTTP 412（WAF）、
        # sc_js_gov/js_fgw 是选择器在静态 HTML 里不存在——前者和后者正是
        # 渲染级该接手的场景，但老链路把失败吞成了空列表。
        list_url = str(source.get("url") or getattr(collector, "list_url", "") or "")
        if list_url:
            links, trace = ladder.list_links(list_url)
            if links:
                notes.append(f"list via ladder: {trace}")
            else:
                notes.append(f"list ladder found nothing: {trace}")
        notes.extend(_collector_notes(collector))

    if not links:
        notes.append("no links found on list page")
        return {"source_id": source_id, "records": [], "dropped": [],
                "notes": notes, "seconds": round(time.time() - started, 1)}

    # 增量：已入库的 URL 直接跳过；连续命中到阈值就早停，不空转
    budget = int((cfg.get("routes") or {}).get("max_items", 60)) or 60
    cap = min(limit, budget)
    stale_limit = int(max_stale or (cfg.get("routes") or {}).get("max_stale_pages", 2))
    # 单源时间预算。config 里早就声明了 rate_limit.source_budget_seconds，
    # 但代码一直没读它——后果是「每源上限」只管条数不管时间：一条静态失败
    # 逐级升级的 URL 要花 30s(httpx)+60s(render)+60s(markdown)，10 条就是
    # 25 分钟，109 个源的一轮永远跑不完（实测跑满 50 分钟被 kill）。
    # 预算是按 URL 之间的检查点生效的，单条 URL 最坏仍可能超一次三级超时。
    time_budget = float((cfg.get("rate_limit") or {}).get("source_budget_seconds", 300))
    stale = 0
    taken = 0

    for url in links:
        if taken >= cap:
            notes.append(f"hit per-source cap {cap}")
            break
        if time.time() - started > time_budget:
            notes.append(f"source time budget {int(time_budget)}s exhausted "
                         f"after {taken} records")
            break
        if deadline is not None and time.time() > deadline:
            notes.append(f"run time budget hit after {taken} records")
            break
        if url in known:
            stale += 1
            if stale >= stale_limit:
                notes.append(f"early stop after {stale} known urls")
                break
            continue
        stale = 0
        taken += 1
        detail = ladder.fetch(url)
        if not detail.ok:
            dropped.append({
                "url": url, "reason": detail.dropped_reason,
                "attempts": detail.attempts,
            })
            continue
        fields = extract_fields(detail.content, cfg)
        # 发布机构以 sources.yaml 的人工登记值为准，正则抽的只作兜底。
        # 正则会把「准确获取四川省商务厅」这种动词+状语当成机构名。
        if source.get("name"):
            fields["issuing_body"] = str(source["name"])
        summary, reason = build_summary(detail.title, detail.content, cfg, fields)
        content_hash = BaseCollector.compute_content_hash({
            "title": detail.title, "publish_date": detail.publish_date,
            "url": url, "content": detail.content,
        })
        records.append({
            "source_id": source_id,
            "source_url": url,
            "url": url,
            "title": detail.title,
            "publish_date": detail.publish_date or fields.get("publish_date") or "",
            "content": detail.content,
            "summary": summary,
            # output/markdown.py:18 的 build_rule_summary 只认 key_points，
            # 不看 record["summary"]。不塞进去的话新摘要永远到不了 markdown 里
            # ——之前实测就是这样：写出来的文件里「规则摘要」只剩金额和截止时间。
            "key_points": [summary] if summary else [],
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "content_hash": content_hash,
            "issuing_body": fields.get("issuing_body") or source.get("name"),
            "gov_level": source.get("gov_level"),
            "level": source.get("level"),
            "amount": fields.get("amount"),
            "deadline": fields.get("deadline"),
            "route_used": detail.route_used,
            "attachment_links": detail.attachments,
            "summary_status": "ok" if summary else f"skipped:{reason}",
        })
        if summary and summary[-1] not in "。！？；":
            notes.append("summary not terminated (unexpected)")

    # 逐源那行由主循环统一打（它才有全局进度上下文）。
    # 之前 collect_source 自己打一遍、主循环再打一遍，同一个源出现两行。
    return {"source_id": source_id, "records": records, "dropped": dropped,
            "notes": notes, "seconds": round(time.time() - started, 1)}


# ------------------------------------------------------------------ 主流程

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="灯塔采集工具 lighthouse")
    parser.add_argument("--source", action="append", default=[],
                        help="只采指定源 id，可重复")
    parser.add_argument("--all", action="store_true", help="采全部 enabled 源")
    parser.add_argument("--all-statuses", action="store_true",
                        help="连 blocked / pending_reprobe 一起试（复探用）")
    parser.add_argument("--status", default="",
                        help="逗号分隔的状态筛选，如 blocked,pending_reprobe。复探用这个")
    parser.add_argument("--limit", type=int, default=20, help="每源最多取多少条")
    parser.add_argument("--workers", type=int, default=0, help="0 = 用配置里的值")
    parser.add_argument("--beat", type=float, default=20.0,
                        help="心跳间隔秒。多久打一次进度行（即使一个源都没完成）")
    parser.add_argument("--max-minutes", type=float, default=30.0,
                        help="整轮硬时限（分钟）。到点不再采新源，已有结果照常落盘。"
                             "0 = 不限。默认 30：109 源 / 6 并发 / 单源 300 秒最坏要 91 分钟")
    parser.add_argument("--dry-run", action="store_true", help="只写报告，不写 markdown")
    parser.add_argument("--fresh", action="store_true",
                        help="忽略已入库 URL，强制重采。重新过一遍新门禁时必须加，"
                             "否则全库都会被 known-url 早停跳掉")
    parser.add_argument("--max-stale", type=int, default=0,
                        help="连续遇到多少个已入库 URL 才早停。0 = 用配置里的值（2）。"
                             "通宵跑要加大：列表按时间倒序，前几十条都是已知的，"
                             "卡在 2 的话整夜都在跳过已知 URL，什么也采不到")
    parser.add_argument("--archive-pages", type=int, default=0,
                        help="覆盖配置里的翻页深度。0 = 用配置值。通宵调度会在产出"
                             "衰减时自动加大——源被采浅了就往更深的年份挖")
    parser.add_argument("--tag", default="",
                        help="给影子报告文件名加个标签，如 probe。通宵的自迭代复采只探"
                             "几个源，会把整轮的『最新报告』顶掉，report.py 于是把"
                             "3 个源的探针结果当成一轮来看")
    parser.add_argument("--ingest", action="store_true",
                        help="采完直接写进 data/beacon.db（走 storage/db.py 的 upsert，"
                             "摘要落 extra.summary，键入 key_points）")
    parser.add_argument("--full", action="store_true",
                        help="采集完再跑主项目 pipeline.py --from refill")
    parser.add_argument("--config", default=None, help="web_collector_config.yaml 路径")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if not args.source and not args.all and not args.all_statuses and not args.status:
        parser.error("必须给 --source <id> / --all / --all-statuses / --status ...")

    if args.status:
        statuses = tuple(s.strip() for s in args.status.split(",") if s.strip())
    elif args.all_statuses:
        statuses = ALL_STATUSES
    else:
        statuses = ("enabled",)
    sources = load_sources(statuses)
    if args.source:
        # 支持 --source a,b,c 和 --source a --source b 两种写法。
        # 只支持后者的时候，PowerShell 里 $(...) 拼出来的逗号串会被当成一个
        # 整体参数传进来，结果是「未找到源」——踩过一次。
        raw: list[str] = []
        for chunk in args.source:
            raw.extend(part.strip() for part in chunk.split(",") if part.strip())
        wanted = set(raw)
        sources = [s for s in sources if s.get("id") in wanted]
        missing = wanted - {str(s.get("id")) for s in sources}
        if missing:
            log(f"未找到源：{sorted(missing)}")

    if not sources:
        log("没有可采的源")
        return 0

    workers = args.workers or int((cfg.get("rate_limit") or {}).get("workers", 6))
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    log(f"灯塔 lighthouse  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"源 {len(sources)} 个 | 每源上限 {args.limit} | 并发 {workers} | "
        f"整轮时限 {args.max_minutes if args.max_minutes > 0 else '不限'} 分钟 | "
        f"早停阈值 {args.max_stale or (cfg.get('routes') or {}).get('max_stale_pages', 2)} | "
        f"{'影子模式(不写 markdown)' if args.dry_run else '写 markdown' + ('+入库' if args.ingest else '')}")
    log("-" * 66)

    # 抢主项目同一把锁：通宵采集正在跑的时候，本工具必须让路
    #
    # 先清僵尸锁。collectors/lock.py 的 _is_stale 判定是 3 小时，进程被 kill
    # 之后留下的锁要等 3 小时才自动失效——那会让「刚才那条命令跑挂了，重试一下」
    # 完全跑不了。scripts/pipeline.py:108 的 clear_stale_lock() 是按 PID 判活性的，
    # 直接复用，不重写一遍锁逻辑（两份锁代码必然漂移）。
    try:
        _clear_stale_lock()
        acquire("lighthouse", wait_seconds=0, lock_dir=LOCK_DIR)
    except CollectLockBusy:
        log("主项目采集正在运行（collect.lock 被持有），本轮不启动。")
        return 2

    try:
        known = set() if args.fresh else _known_urls()
        if args.fresh:
            log("强制重采模式：忽略已入库 URL")
        else:
            log(f"已入库 URL {len(known)} 个，将跳过（要重采加 --fresh）")

        progress = Progress(len(sources), interval=float(args.beat),
                            deadline=(time.time() + args.max_minutes * 60)
                            if args.max_minutes > 0 else None)
        stop = threading.Event()
        # 心跳必须跑在独立线程：as_completed 阻塞等下一个源，主线程没机会打进度。
        # 不开线程时一个源卡 300 秒就是 5 分钟白屏，看起来像死机。
        beater = threading.Thread(target=monitor, args=(progress, stop), daemon=True)
        beater.start()
        results: list[dict[str, Any]] = []
        deadline = progress.deadline
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(collect_source, source, cfg, args.limit, known,
                                args.dry_run, deadline, args.max_stale,
                                args.archive_pages): source
                    for source in sources
                }
                for source in sources:
                    progress.start_source(str(source.get("id") or "?"))
                for future in as_completed(futures):
                    source_id = str(futures[future].get("id") or "?")
                    try:
                        outcome = future.result()
                    except Exception as exc:                   # noqa: BLE001
                        log(f"  [{source_id}] 崩溃: {type(exc).__name__}: {exc}"[:200])
                        outcome = {"source_id": source_id, "records": [], "notes": [],
                                   "dropped": [{"url": "", "reason": f"crash:{exc}"}],
                                   "seconds": 0}
                    results.append(outcome)
                    progress.finish_source(source_id, len(outcome.get("records") or []),
                                           len(outcome.get("dropped") or []))
                    log(f"  [{source_id}] {len(outcome.get('records') or [])} 条 / "
                        f"丢弃 {len(outcome.get('dropped') or [])} / "
                        f"{outcome.get('seconds')}s  "
                        f"{(outcome.get('notes') or ['-'])[-1]}")
                    progress.tick(force=True)
        finally:
            stop.set()
        return _finish(cfg, results, stamp, args)
    finally:
        release(LOCK_DIR)
        _close_browser()


def _close_browser() -> None:
    """退出前显式关掉共享浏览器，并**注销 playwright_worker 的 atexit**。

    两个坑叠在一起才造成「跑完整轮但进程 20 多分钟不退出」：

    1. collectors/playwright_worker.py:379 用 atexit.register(shutdown_renderer)
       兜底，但那条路会卡住。
    2. 如果自己起线程去调 shutdown_renderer，而它卡在 _RENDER_LOCK 或
       Renderer.close() 里没返回，_RENDERER 还是非 None；随后 atexit 又去调
       一次，拿同一把锁 → 退出时死锁。上一版就是死在这里。

    所以顺序很重要：**先注销 atexit，再自己关**。关不掉也不阻塞——
    最坏情况就是残留一个浏览器进程，下一轮采集自己的浏览器复用/覆盖它。
    真正的兜底是 overnight.py 的 proc.wait(timeout=...) —— 一轮卡死只损失
    一轮，不会让整夜静默停摆。
    """
    import atexit

    try:
        from collectors.playwright_worker import shutdown_renderer
    except Exception:                                          # noqa: BLE001
        return
    try:
        # 必须先注销，否则下面即使卡住，atexit 也会再拿一次锁
        atexit.unregister(shutdown_renderer)
    except Exception:                                          # noqa: BLE001
        pass
    try:
        shutdown_renderer()
    except Exception:                                          # noqa: BLE001
        pass


def _clear_stale_lock() -> None:
    """复用主项目的僵尸锁清理（scripts/pipeline.py:108，按 PID 判活性）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_beacon_pipeline", _ROOT / "scripts" / "pipeline.py")
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    note = module.clear_stale_lock()
    if note and "不��" not in note and "ALIVE" not in note:
        log(f"锁：{note}")


def _collector_notes(collector: Any) -> list[str]:
    """采集器自己记的原因，转成可读文本。

    HtmlListCollector._note() 会记 static_list_failed / list_selector_missed /
    archive_render_failed 这类诊断。父类存的地方字段名各版本不一致，
    挨个 getattr 试一遍——猜错字段名只会让报告再次变成「一句话解释一切」。
    """
    out: list[str] = []
    # HtmlListCollector._note()（html_list.py:683）把诊断写进 self.errors，
    # 每项是 {"url": ..., "error": "kind: message"}。别的字段名也一并试，
    # 猜错只会让报告再次变成「一句话解释一切」。
    for attr in ("errors", "_notes", "notes", "diagnostics", "_diagnostics"):
        value = getattr(collector, attr, None)
        if not value:
            continue
        items = value.values() if isinstance(value, dict) else value
        try:
            for item in list(items)[:6]:
                if isinstance(item, dict):
                    out.append(str(item.get("error") or item)[:160])
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    out.append(f"{item[0]}: {item[1]}"[:160])
                else:
                    out.append(str(item)[:160])
        except TypeError:
            continue
        break
    return out


def _known_urls() -> set[str]:
    """已入库的 URL，用来跳过重复采集。没库就返回空集。"""
    db_path = _ROOT / "data" / "beacon.db"
    if not db_path.exists():
        return set()
    try:
        from storage.db import Database

        return {u for group in Database(str(db_path)).known_urls_by_source().values() for u in group}
    except Exception:                                          # noqa: BLE001
        return set()


def _finish(cfg: Mapping[str, Any], results: list[dict[str, Any]], stamp: str,
            args: argparse.Namespace) -> int:
    records = [r for res in results for r in res["records"]]
    dropped = [d for res in results for d in res["dropped"]]
    by_route: dict[str, int] = {}
    for record in records:
        key = record.get("route_used") or "?"
        by_route[key] = by_route.get(key, 0) + 1

    # 影子报告：逐条记录尝试过哪一级、为什么没过。门禁丢弃的记录只进这里。
    out = report_dir(cfg) / f"shadow{'-' + args.tag if args.tag else ''}-{stamp}.json"
    out.write_text(json.dumps({
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": bool(args.dry_run),
        "records": len(records),
        "dropped": len(dropped),
        "by_route": by_route,
        "by_reason": _tally(d["reason"] for d in dropped if d.get("reason")),
        "sources": [{"source_id": r["source_id"], "records": len(r["records"]),
                     "dropped": len(r["dropped"]), "notes": r["notes"],
                     "seconds": r["seconds"]} for r in results],
        "dropped_detail": dropped[:2000],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    log("-" * 66)
    log(f"采到 {len(records)} 条 | 门禁丢弃 {len(dropped)} 条")
    log(f"路由分布 {by_route}")
    log(f"丢弃原因 {_tally(d['reason'] for d in dropped if d.get('reason'))}")

    # 零产出必须分类。「没有新东西」和「采不到东西」是两回事，混在一起看
    # 就只能看到一句「66 个源没产出」，既像坏了又不知道该修哪里。
    def classify(result: Mapping[str, Any]) -> str:
        if result.get("records"):
            return "ok"
        notes = " | ".join(result.get("notes") or [])
        if result.get("skipped") or "run time budget" in notes:
            # 到时限没轮到 / 采到一半到时限，都不是源的毛病。
            # 只认 entry 的 skipped 标志会漏掉后者：它在 URL 之间才触发。
            return "skipped"
        if "early stop" in notes:
            return "no_new"          # URL 都已入库，正常的增量空转
        if "no links" in notes:
            return "no_links"         # 列表页取不到链接：选择器/URL 真的坏了
        if "list_selector did not match" in notes or "fetch_list failed" in notes:
            # 配的 list_selector 在页面上找不到。站点改版了，或者选择器从来没配对。
            # 这类最值得修：一次 propose_source_config 就能救回一个源。
            return "no_links"
        if result.get("dropped"):
            return "all_dropped"      # 链接有但全被门禁拦下
        return "unknown"

    buckets: dict[str, list[str]] = {}
    for result in results:
        buckets.setdefault(classify(result), []).append(str(result.get("source_id")))
    log("-" * 66)
    log("逐源结果：")
    hints = {
        "ok": "采到数据",
        "skipped": "到时限未轮到（本轮没跑它，不是失败）",
        "no_new": "无新数据（已全部入库，正常增量空转）",
        "no_links": "列表页取不到链接 → 需修选择器/URL",
        "all_dropped": "链接有但全被门禁拦下",
        "unknown": "未分类",
    }
    for name in ("ok", "skipped", "no_new", "no_links", "all_dropped", "unknown"):
        ids = buckets.get(name) or []
        if not ids:
            continue
        log(f"  {name:<11} {len(ids):>3} 个   {hints[name]}")
        if name in ("no_links", "all_dropped", "unknown"):
            log(f"              {' '.join(ids)}")
    log(f"报告 {out}")

    if not args.dry_run and records:
        from output.markdown import write_markdown_records

        by_source: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            by_source.setdefault(record["source_id"], []).append(record)
        for source_id, group in by_source.items():
            target = markdown_dir(cfg) / source_id
            target.mkdir(parents=True, exist_ok=True)
            written = write_markdown_records(group, target)
            log(f"  markdown {source_id}: {written}")

        # 旁路 JSONL：入库的数据源。
        #
        # 不从 markdown 反解析——那是把「给人看的排版」当成「机器契约」，
        # 改一次模板就断一次。这里一行一条 JSON、字段全留，ingest.py 直接读；
        # markdown 继续只当交付物给人看。
        jsonl = report_dir(cfg) / f"collected-{stamp}.jsonl"
        with open(jsonl, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        log(f"旁路数据 {jsonl}（{len(records)} 行）")

        if args.ingest:
            from lighthouse.ingest import ingest

            return ingest(jsonl, db_path=str(_ROOT / "data" / "beacon.db"),
                          batch=int((cfg.get("general") or {}).get("ingest_batch", 50)))

    if args.full and records and not args.dry_run:
        import subprocess

        log("接着跑主项目库级流水线（refill → amounts → summary → classify → dedup → audit）")
        return subprocess.call(
            [sys.executable, "-u", "-B", str(_ROOT / "scripts" / "pipeline.py"),
             "--from", "refill"], cwd=str(_ROOT))
    return 0


def _tally(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
