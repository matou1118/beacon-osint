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
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml                                                    # noqa: E402

from collectors.base import BaseCollector                      # noqa: E402
from extractors.clean_summarize import MIN_SENTENCE           # noqa: E402
from collectors.lock import CollectLockBusy, acquire, release  # noqa: E402
from lighthouse.routes import Ladder, build_collector         # noqa: E402
from lighthouse.winflags import install as _install_no_window  # noqa: E402
from lighthouse.winflags import ensure_stdio as _ensure_stdio  # noqa: E402
_install_no_window()  # 本进程 spawn 的任何子进程都不带控制台窗口

from lighthouse.quality import build_summary, extract_fields, judge, load_config  # noqa: E402
from extractors.category_rules import classify  # noqa: E402

SOURCES_YAML = _ROOT / "config" / "sources.yaml"
LOCK_DIR = _ROOT / "data" / "runtime" / "collect.lock"

# ★ 排在任何 log() 之前。理由见 lighthouse/winflags.py 的 ensure_stdio 文档：
# 计划任务用 pythonw.exe 拉起整条链且不给控制台，sys.stdout 是 None，
# 第一个 print() 就抛 AttributeError，进程在写第一行日志之前就退出
# （实测 LastTaskResult=2、日志零新增，看着像「正常退出没活干」）。
_ensure_stdio(_ROOT / "data" / "runtime" / "lighthouse" / "scheduler.out")

# ladder 的 route_used → announcements.content_source。主流水线
# （collectors/base.py:483）只写 "static_html" 一种，这里按真实降级级对齐，
# 否则「正文到底是谁抽的」在库里分不出来（scripts/audit_pipeline_path.py 就靠它）。
_CONTENT_SOURCE_BY_ROUTE = {
    "rss": "rss",
    "api": "api",
    "static": "static_html",
    "render": "render_html",
    "markdown": "crawl4ai_fit_markdown",
    "pdf": "public_download",
}


def _needs_deeper_backfill(source_id: str, years: int = 5) -> bool:
    """这个源的库内最老记录是否还不到 N 年（是的话历史没采完，该加深翻页）。

    判据是**库内已有多少年历史**，而不是「翻了多少页」—— 页数只是手段，
    年份才是目的（用户要求部委/省市采 5 年以上）。实测 mee_gov 翻到 30 页已有
    2022~2026 四年 37 条、mohrss_gov 有 2021~2026，它们是「当期没更新」而不是
    「历史没采完」，加深翻页对它们无效。

    读不到（首次采集 / 库异常）时返回 False：宁可不做，也不白改配置。
    """
    import sqlite3

    try:
        con = sqlite3.connect(
            f"file:{_ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)
        row = con.execute(
            "SELECT MIN(publish_date) FROM announcements WHERE source_id = ?",
            (source_id,)).fetchone()
        con.close()
    except Exception:                                   # noqa: BLE001
        return False
    oldest = str(row[0] or "")[:4] if row and row[0] else ""
    if not oldest.isdigit():
        return False
    try:
        gap = datetime.now().year - int(oldest)
    except (TypeError, ValueError):
        return False
    return gap >= years


def _failure_types(outcome: Mapping[str, Any], source_id: str = "") -> list[str]:
    """把一个源的采集结果翻译成原则表里用的 failure_type 列表。

    原则表按 (scope_type, failure_type) 索引，所以这里的取值必须和
    scripts/seed_principles.py 里种的键对得上。映射依据全是实测（2026-09-30）：

      drained_sources   notes 里有 early stop / all N already known
                        —— 首屏采完了但历史没采完，该加深翻页
      budget_exhausted  source time budget Ns exhausted —— 预算不够
      dead_urls         dropped 里有 404/410/451（死链，逐条烧三级升级）
      link_flood        dropped 里有 markdown:link_flood
      no_links          notes 里有 no links / list_selector did not match
    """
    out: list[str] = []
    notes = [str(n) for n in (outcome.get("notes") or [])]
    dropped = [d for d in (outcome.get("dropped") or [])
               if isinstance(d, dict)]
    blob = " | ".join(notes)

    if any("early stop" in n or "already known" in n for n in notes):
        # 采干有两种，后果不同（2026-09-30 拆开）：
        #   deep_backfill_needed —— 历史还没采完（库内最老记录比 5 年早）→ 该加深
        #   drained_sources      —— 当期真的没新公告 → 加深无用，该放宽早停
        # 之前两者共用一个名，动作一律 deepen_pages，结果对已采干的源白改一次
        # 配置（实测 archive_pages 30→42，mee_gov/nea_gov 记录数一条没涨，
        # 动作 fail=2）。而 mee_gov 实际已有 2022~2026 四年 37 条 —— 深翻已经
        # 到位，是源本身没更新。
        if source_id and _needs_deeper_backfill(source_id):
            out.append("deep_backfill_needed")
        out.append("drained_sources")
    if any("time budget" in n for n in notes):
        out.append("budget_exhausted")
    reasons = [str(d.get("reason", "")) for d in dropped]
    if any("link_flood" in r for r in reasons):
        out.append("link_flood")
    if any(re.search(r"HTTP (404|410|451)", r) for r in reasons):
        out.append("dead_urls")
    if "no links" in blob or "list_selector did not match" in blob:
        out.append("no_links")
    return out


def _content_source(route: str | None) -> str:
    return _CONTENT_SOURCE_BY_ROUTE.get(str(route or ""), "static_html")


# 深翻游标的落盘位置。跟着 source_backoff.json 放同一个目录——都是「每个源
# 上次到哪了」这类轮次间状态。
CURSOR_FILE = _ROOT / "data" / "runtime" / "lighthouse" / "list_cursor.json"


def _cursor_path() -> Path:
    return CURSOR_FILE


def _cursor_load() -> dict[str, int]:
    """每个源扫到第几个列表链接了。读不到就当没有游标（从头扫，最坏只是慢）。"""
    path = _cursor_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def _cursor_save(source_id: str, position: int) -> None:
    """记录游标。写失败只记一笔，不许因此让整轮采集失败。"""
    state = _cursor_load()
    state[source_id] = max(0, int(position))
    path = _cursor_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from lighthouse.atomic_write import atomic_write_json

        atomic_write_json(path, state)
    except Exception:                                    # noqa: BLE001
        log(f"  游标写入失败（不影响采集）：{source_id}")


def _summary_points(summary: str) -> list[str]:
    """把摘要拆成要点列表。

    ``key_points`` 这一列有两种产出方，必须同构，否则互相覆盖：

    * ``extractors/summary_refine.build_summary()`` —— ``summary`` 是
      ``"".join(points)``，每点之间**不留分隔符**，靠句子自带的句末标点断开；
    * ``scripts/resummarize.py`` —— 直接写 ``result["key_points"]``，是列表。

    采集链路原先写 ``[summary]``（整块当一条），于是每次采集都把上一轮
    resummarize 拆好的列表压回单块，两个脚本来回改写同一列。实测 196 条
    记录存着**过期摘要**：正文被后续清洗改过，摘要没跟着重算，于是摘要里
    的话在 content 里找不到。

    拆法只能按句末标点——摘要本身没有分隔符可用。这里保守地只按强标点断，
    断不出多段就还回单条，形态仍然和 build_summary 一致（都是「列表」）。
    """
    text = str(summary or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[。！？；])", text) if p.strip()]
    if len(parts) <= 1:
        return [text]
    # 过短的碎片多半是切错了位置，**并回前一条**而不是丢掉——丢掉就等于
    # 静默丢内容（实测第一版就是这么把「财政部拟发行本期国债」弄没的）。
    out: list[str] = []
    for part in parts:
        if out and len(part) < MIN_SENTENCE:
            out[-1] += part
        else:
            out.append(part)
    return out


# 重试和换路由都救不回来的状态码。命中它们继续往下走只是烧预算。
#
# 404/410：页面被删或路径变了（实测 mct_gov 翻页后返回旧站点路径，整页 404）
# 401/403：权限/反爬。换渲染方式没用，还会把反爬当得更狠
# 451：因法律原因不可用 —— 同样换路由无解
_DEAD_STATUS = (404, 410, 401, 403, 451)

# attempts 里 reason 文本的形态（Ladder 的真实返回）：
#   {'level': 'static', 'reason': 'error:RuntimeError:HTTP 404 for https://...'}
# **没有结构化 status 字段**，只能从文本里认。
_DEAD_RE = re.compile("HTTP (%s)\\b" % "|".join(str(c) for c in _DEAD_STATUS))


def dead_status(detail: Any) -> int | None:
    """这个 URL 的死状态码（404/410/401/403/451），活着返回 None。

    只看**第一级 static** 的 attempt：那才是拿到真实 HTTP 状态码的地方。
    后面几级的 reason 只有 too_short / chrome 之类，看它们会误判 ——
    实测 404 的 URL 在 render/markdown 级的 reason 也写着 'too_short'，
    和一个正常但简短的页面完全分不出来。
    """
    attempts = getattr(detail, "attempts", None) or []
    for a in attempts:
        if not isinstance(a, dict):
            continue
        if str(a.get("level")) != "static":
            continue
        m = _DEAD_RE.search(str(a.get("reason") or ""))
        return int(m.group(1)) if m else None      # 第一级过了就不必看后面
    for a in attempts:                              # 没有 static 记录时的兜底
        if isinstance(a, dict):
            m = _DEAD_RE.search(str(a.get("reason") or ""))
            if m:
                return int(m.group(1))
    return None


def _is_dead_url(detail: Any) -> bool:
    """这个 URL 是不是「死了」—— 重试和换路由都救不回来。

    实测 2026-09-30（mct_gov）：第 1 页 URL 域名对（zwgk.mct.gov.cn），翻到
    第 2~6 页时站点返回**另一个旧站点**的链接（www.mct.gov.cn 的
    /ghjh/2020..、/tjxx/2025.. 等），全部 404。那 14+ 条 404 逐条走
    「静态 30s → 渲染 60s → markdown 60s」三级升级，一个源就烧光 300 秒
    预算却产出 0 条。404 是不死的信号，性质和「已入库的 URL」一样：
    后面还剩多少条都无关紧要。
    """
    return dead_status(detail) is not None


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
        # 按插入顺序取，也就是采集优先档位的顺序。之前 sorted() 按字母序截前 4 个，
        # 于是一轮里明明在跑的是国家部委，进度行却显示 ah_fgw,ah_gxt 这类排在
        # 最后档位的源 —— 进度面板因此指向错误的源，看起来像优先级没生效。
        running = ",".join(list(self.inflight)[:4]) or "-"
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

def _focus_tier(source: Mapping[str, Any], cfg: Mapping[str, Any]) -> int:
    """这个源在采集顺序里排第几档。0 是最高优先。

    档位表来自 config 的 ``focus_order``，不在代码里写死地区清单。
    """
    order = (cfg.get("focus_order") or [])
    gov_level = str(source.get("gov_level") or "")
    province = str(source.get("region_province") or "")
    city = str(source.get("region_city") or "")
    source_type = str(source.get("source_type") or "")
    functions = source.get("functions") or []
    if isinstance(functions, str):
        functions = [functions]
    functions = {str(item) for item in functions}

    for index, rule in enumerate(order):
        if not isinstance(rule, Mapping):
            continue
        wanted = rule.get("gov_level")
        if wanted and gov_level in {str(item) for item in wanted}:
            return index
        wanted = rule.get("source_type")
        if wanted and source_type in {str(item) for item in wanted}:
            return index
        wanted = rule.get("region")
        if wanted:
            targets = {str(item) for item in wanted}
            # 先精确匹配，再前缀匹配：region_city 可能是"成都市"，也可能是
            # "成都市锦江区"这类带后缀的值。
            if province in targets or city in targets:
                return index
            if any(province.startswith(t) or city.startswith(t) for t in targets if province or city):
                return index
        wanted = rule.get("functions_any")
        if wanted and functions & {str(item) for item in wanted}:
            return index
    return len(order)


def sort_sources(sources: list[dict[str, Any]], cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """按采集优先档位排序。

    之前 load_sources 只按 status 过滤、**完全不排序**，源按 sources.yaml 的
    原始顺序提交给线程池。叠加整轮时限之后，后面的源每轮都轮不到 ——
    优先级写在 yaml 里是装饰，因为没有任何代码读它。
    """
    def rank(source: Mapping[str, Any]) -> tuple[int, int, str, str]:
        focus = {"critical": 0, "high": 1, "standard": 2}.get(
            str(source.get("focus_level") or ""), 3)
        prio = {"P0": 0, "P1": 1, "P2": 2}.get(str(source.get("priority") or ""), 3)
        return (_focus_tier(source, cfg), focus, prio, str(source.get("id") or ""))

    return sorted(sources, key=rank)


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


# 各 cadence_tier 的最小回访间隔（秒）。registry 里的 cadence_tier 一直没人读 ——
# 源自己声明「我一周更一次」就该按一周来，而不是每 2 小时敲一次。
#
# daily 从 2 小时提到 8 小时，依据是实测（2026-09-30，34 个 enabled 源一轮）：
#
#   采到 15 条 / 耗时 45 分钟 / 丢弃 73 条
#   其中 15 个源报 "all N list urls already known"（已全部入库），
#   每个烧 38~369 秒，合计约 24 分钟 —— 也就是**过半预算花在确认「还是那些」上**。
#
# 2 小时这道闸挡不住它：一轮只要 10~45 分钟，一天能跑十几轮，于是采干的源一天被
# 敲十几次门。政府站一天最多发一条公告，2 小时的增量空间是假的，8 小时足够 ——
# 采干源每天少敲 3 遍（约省 1 小时带宽），而真发了公告的源最多晚几小时被看到。
CADENCE_SECONDS = {
    "daily": 8 * 3600,
    "weekly": 7 * 24 * 3600,
    "monthly": 30 * 24 * 3600,
    "never": 365 * 24 * 3600,
}

# 零产出退避阶梯，**绝对秒数**，不是倍率。
#
# ⚠️ 2026-10-02 改。之前是 `SLEEP_ROUNDS=(2,4,6,12)` 乘在 CADENCE_SECONDS 上：
#     backoff_seconds = sleep_for * floor
# 那个写法有两个数相乘，于是**改 floor 会等比放大退避**。实测 2026-10-01
# 整夜：同一天把 daily floor 从 2 小时提到 8 小时（礼貌，每天最多敲一次门），
# 退避就从 6 小时悄悄变成 24 小时，34 个源整夜全被闸挡住、零入库，而看门狗
# 每轮都报「正常完成（无源可采）」。
#
# 现在阶梯直接写死绝对值，逐档和旧行为完全一致（测试里钉住了）：
#     dry 0/1 → 8h    dry 2 → 16h   dry 3 → 32h   dry 4 → 48h   dry≥5 → 96h
#
# 关键差别：改 CADENCE_SECONDS 不再影响退避。两件事从此解耦。
# 改阶梯请直接改下面这张表，不要指望调 floor 能顺带调整它。
#
# 各档含义：
#   8h  = 正常节流（源刚采过，别每天敲十几次门）
#   16h = 连续 2 轮零产出：源更新稀疏，一天回访一次
#   32h = 连续 3 轮：约一天半
#   48h = 连续 4 轮：两天
#   96h = 连续 5 轮以上：四天（封顶，再久源就饿死了）
BACKOFF_LADDER = {
    "daily": (8 * 3600, 16 * 3600, 32 * 3600, 48 * 3600, 96 * 3600),
    "weekly": (7 * 24 * 3600, 14 * 24 * 3600, 28 * 24 * 3600,
               42 * 24 * 3600, 84 * 24 * 3600),
    "monthly": (30 * 24 * 3600, 60 * 24 * 3600, 90 * 24 * 3600,
                120 * 24 * 3600, 180 * 24 * 3600),
    "never": (365 * 24 * 3600,) * 5,
}


# ------------------------------------------------------------------ 采干退避

class SourceBackoff:
    """连续零产出的源退避几轮。

    存 ``{"<source_id>": [连续零产出轮数, 上次被采的轮次序号]}``。轮次序号用
    ``runs`` 计数，不依赖时钟：改时间、跨天、重启都不影响退避判断。

    为什么需要：部委这类源公告更新以周/月计，采干后每轮仍要花 90~250 秒抓一次
    列表页来「确认还是已知的」，采到 0 条。实测 2026-09-29：13 个部委源占掉
    一轮 60 分钟预算的三分之一，产出 0。退避把预算让给还有新内容的源，期满
    自动回访，不会真的漏掉。
    """

    DRY_STREAK_LIMIT = 2                    # 连续几轮零产出才开始退避

    # 零产出退避阶梯。**绝对秒数**，逐档含义写在 BACKOFF_LADDER 的注释里。
    #
    # 原来是 `SLEEP_ROUNDS=(2,4,6,12)` 乘在 cadence 墙钟下限上。两个数相乘的
    # 后果是「改 floor 会等比放大退避」——2026-10-01 整夜把 daily floor 从 2 小时
    # 提到 8 小时，退避跟着从 6 小时变 24 小时，34 个源整夜全被挡、零入库，
    # 而看门狗每轮都报「正常完成（无源可采）」。两件事现在解耦了。
    #
    # 逐档和旧行为完全一致（tests/test_self_tune.py 里有等价性测试钉住）：
    #     dry 0/1 → 8h   dry 2 → 16h   dry 3 → 32h   dry 4 → 48h   dry≥5 → 96h
    LADDER = BACKOFF_LADDER
    # 退避强度，**乘在 cadence_tier 的墙钟下限上**（见 is_sleeping_for 的闸 2）。
    #
    # ⚠️ 这个元组是倍率不是「几轮」。daily 档 floor = 8 小时，所以：
    #     (2, 4, 6, 12) → 16 / 32 / 48 / 96 小时
    # 改 floor 时必须同时重算这个元组，否则间隔会等比例放大。
    #
    # 2026-10-02 实测踩坑：同一天把 daily floor 从 2 小时提到 8 小时（礼貌，
    # 每天最多敲一次门），却没动这个元组，退避从 6 小时变成 24 小时。后果是
    # 34 个源全部被挡在闸内，**整夜零入库、12 小时只完成 1 轮**，而看门狗
    # 每次都报「正常完成（无源可采）」——看起来像健康，实际上什么都没干。
    #
    # 零产出退避阶梯。**绝对秒数**，逐档含义写在 BACKOFF_LADDER 的注释里。
    #
    # 原来是 `SLEEP_ROUNDS=(2,4,6,12)` 乘在 cadence 墙钟下限上。两个数相乘的
    # 后果是「改 floor 会等比放大退避」——2026-10-01 整夜把 daily floor 从 2 小时
    # 提到 8 小时，退避跟着从 6 小时变 24 小时，34 个源整夜全被挡、零入库，
    # 而看门狗每轮都报「正常完成（无源可采）」。两件事现在解耦了。
    #
    # 逐档和旧行为完全一致（tests/test_self_tune.py 里有等价性测试钉住）。
    LADDER = BACKOFF_LADDER

    @classmethod
    def backoff_interval(cls, dry_streak: int, tier: str = "daily") -> int:
        """连续零产出 ``dry_streak`` 轮后，这次要等多久（秒）。

        纯查表，**不乘任何东西**。这是它和旧写法唯一的区别，也是关键：
        旧写法 ``SLEEP_ROUNDS[i] * CADENCE_SECONDS[tier]`` 让改 floor 意外地
        改掉退避，一次整夜零入库就是这么来的。

        索引映射（照旧行为反推，见等价性测试）：
          dry 0/1 → 档 0（还没到退避门槛，就是正常节流）
          dry 2   → 档 1     dry 3 → 档 2   dry 4 → 档 3   dry>=5 → 档 4（封顶）
        """
        ladder = cls.LADDER.get(str(tier or "daily")) or cls.LADDER["daily"]
        index = 0 if dry_streak < cls.DRY_STREAK_LIMIT else \
            min(dry_streak - 1, len(ladder) - 1)
        return int(ladder[index])

    # 自我迭代可以调这个阶梯（``principle_applier.loosen_backoff``），但必须
    # **落盘**。2026-10-02 实测踩过：动作只改了内存里的类变量，事件流写着
    # 「SLEEP_ROUNDS [2,4,6,12] -> [1,3,5,11]」、返回 applied=True，
    # 可下一个进程 import 进来还是 [2,4,6,12]——「应用成功」但下次启动就
    # 没了。这跟之前 auto_observe 那种假动作是同一类错误，只是更隐蔽：
    # 事件流和返回值都「成功」了。
    @classmethod
    def apply_backoff_override(cls, tier: str, values: Sequence[int]) -> None:
        ladder = dict(cls.LADDER)
        ladder[str(tier)] = tuple(int(v) for v in values)
        cls.LADDER = ladder
        path = cls._override_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        from lighthouse.atomic_write import atomic_write_json

        atomic_write_json(path, {"ladder": {k: list(v) for k, v in ladder.items()},
                                 "at": int(time.time())})

    @classmethod
    def load_backoff_override(cls) -> None:
        """进程启动时读回上次的自动调整。没有这一句，改动就是一次性的。"""
        path = cls._override_path()
        if not path.exists():
            return
        try:
            import json

            raw = json.loads(path.read_text(encoding="utf-8")).get("ladder")
            if isinstance(raw, dict) and raw:
                merged = dict(cls.LADDER)
                for tier, values in raw.items():
                    if (isinstance(values, list) and values
                            and all(isinstance(v, int) and v > 0 for v in values)):
                        merged[str(tier)] = tuple(values)
                cls.LADDER = merged
        except (OSError, ValueError, TypeError):
            pass          # 读不到就用默认值，别因此起不来

    @staticmethod
    def _override_path() -> Path:
        return _ROOT / "data" / "runtime" / "lighthouse" / "backoff_override.json"
    # 熔断：被真实访问过这么多次仍然零产出，且最后一次零产出距今这么久，
    # 就别再当它是好源了 —— 自适应地把它踢出本趟采集。
    #
    # 为什么按「访问次数」而不是「轮次」：回访间隔闸会把源挡掉很多轮，用轮次
    # 计数的话，被挡的轮次也算「没产出」，一个健康但只是没到点的源会被误熔断。
    CIRCUIT_VISITS = 4                      # 至少被真实访问过这么多次
    CIRCUIT_ZERO_STREAK = 4                 # 其中这么多次零产出
    CIRCUIT_IDLE_SECONDS = 3 * 24 * 3600    # 且已经这么长时间没出过货
    # 采集源等级 -> 最小回访间隔（秒）。等级只降频、不放行。
    #
    # 依据：2026-10-04 实测 143 个源里 70 个连续零产出 4 次，而同一时刻
    # 有 126 个源本周内刚产出过 —— 说明绝大多数源是活的，被当成「采干」
    # 反复回访。等级用来把预算还给健康源。
    TIER_MIN_SECONDS = {
        "T0": 0,             # 主力源：不额外限制，走退避表
        "T1": 57600,         # 16 小时：采得到但稀薄
        "T2": 0,             # 需渲染：间隔不变，路由不同（跳过静态尝试）
        "T3": 43200,         # 12 小时：正文是栏目页，低频试探
        "T4": 604800,        # 7 天：久没产出
        "T5": 2592000,       # 30 天：明确失效，只做低频探活
    }

    TERMINAL_NEVER = 100 * 365 * 24 * 3600  # 终态源的「永不再访」占位

    # 字段下标访问器。别再直接用 entry[2]/entry[3]/entry[4] 硬编码 ——
    # 那三个位置存的是语义不同的量，加字段时一错位就出「访问 2 次被判熔断、
    # 访问 6 次反而没熔断」这种自相矛盾的行为（真的发生过）。
    F_DRY_STREAK = 0      # 连续零产出轮数（不含被回访闸挡掉的轮次）
    F_LAST_RUN = 1        # 上次被访问时的 run 序号
    F_LAST_EPOCH = 2      # 上次被访问的 epoch 秒
    F_VISITS = 3          # 一共被真实访问过多少次
    F_ZERO_VISITS = 4     # 其中零产出多少次
    F_LAST_PRODUCED = 5   # 最后一次真采到东西的 epoch 秒（0 = 从来没有）
    F_TERMINAL = 6          # 终态标记（1 = 永不再访问。目前只有白名单拒绝会触发）
    FIELDS = 7

    def __init__(self, data: dict[str, list[int]] | None = None) -> None:
        self.data: dict[str, list[int]] = {}
        for key, value in (data or {}).items():
            if not isinstance(value, list):
                continue
            row = [int(x) for x in value[:self.FIELDS] if isinstance(x, int)]
            row.extend([0] * (self.FIELDS - len(row)))
            self.data[key] = row
        self.runs = max((row[self.F_LAST_RUN] for row in self.data.values()),
                        default=0)

    @classmethod
    def path(cls) -> Path:
        return _ROOT / "data" / "runtime" / "lighthouse" / "source_backoff.json"

    @classmethod
    def tier_path(cls) -> Path:
        """采集源等级表（scripts/source_tiers.py 生成）。"""
        return _ROOT / "data" / "runtime" / "lighthouse" / "source_tiers.json"

    @classmethod
    def tiers(cls) -> dict:
        """加载等级表。**读不到就返回空字典**，让所有源按原来的间隔走。

        故意不做「缺文件时报错」：等级表是**优化**，不是**前置依赖**。
        它缺失时降级到旧行为，比让采集整个起不来好得多。
        """
        path = cls.tier_path()
        if not path.exists():
            return {}
        try:
            return (json.loads(path.read_text(encoding="utf-8"))
                    or {}).get("tiers", {}) or {}
        except (OSError, ValueError):
            return {}

    @classmethod
    def tier_policy(cls) -> dict:
        """等级表的 ``policy`` 段（T0–T5 的标准回访间隔等）。

        和 :meth:`tiers` 同源同降级：读不到就返回空字典。存在的理由是
        ``TIER_MIN_SECONDS`` 里 T0/T2 是 0（"不额外限制，走退避表"），
        单靠它算不出一个源的**标准间隔**，而 ``source_sync_state`` 那一列要
        填的正是标准间隔，不是这次实际等了多久。
        """
        path = cls.tier_path()
        if not path.exists():
            return {}
        try:
            return (json.loads(path.read_text(encoding="utf-8"))
                    or {}).get("policy", {}) or {}
        except (OSError, ValueError):
            return {}

    @classmethod
    def tier_of(cls, source_id: str) -> str:
        """这个源的等级；查不到当 T0（主力，最保守的间隔）。"""
        return (cls.tiers().get(source_id) or {}).get("tier") or "T0"

    @classmethod
    def load(cls) -> "SourceBackoff":
        if not cls.path().exists():
            return cls()
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls(raw)

    def next_run(self) -> int:
        self.runs += 1
        return self.runs


    def is_sleeping(self, source_id: str) -> bool:
        return self.is_sleeping_for(source_id, tier="daily")

    def is_sleeping_for(self, source_id: str, tier: str = "daily") -> bool:
        """这个源现在该不该跳过（按源自己的 cadence_tier 决定回访间隔）。

        registry 里的 ``cadence_tier``（daily/weekly）一直没人读 —— 采集链
        把它当装饰。源自己声明「我一周更一次」就按一周来，而不是每 2 小时
        敲一次。

        两道闸：

        1. **最小回访间隔**（墙钟，不看轮数）。政府站最多一天发一次公告，
           但一轮只要 10~15 分钟，一天能跑 30 轮 —— 等于每天去敲 30 遍门
           确认"还是那些"。既浪费预算，也不礼貌（AGENTS.md §3.2 的频率红线）。

        2. **采干退避**（轮数）。连续零产出越多，间隔拉得越长，封顶 6 轮。

        两道都过才采。用轮数算退避在"轮次很快"时会失效 —— 这正是之前
        4 轮只退避掉 2 个源、27 个源被反复访问的原因。

        **两道闸取「更严的」，不是「或」（2026-09-30 修）**：

        原判据是「时钟闸过 且 轮次闸过 才采」，于是轮次闸能压住时钟闸。
        实测 mca_gov / mct_gov 的时钟早已到期（13.4 小时 > 8 小时门槛）却仍
        被挡 —— 因为 `dry_streak` 只增不减、`sleep_for` 封顶 12 轮，而 `runs`
        每轮才 +1（早退轮次甚至不 +1）。结果是**采干的源被永久挡住**，哪怕
        过了一个月也不会再访问，而这恰恰是最需要它的时刻（周/月更新）。

        现在改成：轮次闸算出还差几轮，折算成墙钟下限（按 daily 门槛 ×
        轮数），与时钟闸**取大者**。这样「连续 6 轮零产出」的真实含义
        「至少 6 个回访周期内别来」被保住了，而不会因为轮次计数器推进慢
        就锁死到永远。
        """
        row = self.data.get(source_id)
        if not row:
            return False
        if len(row) > self.F_TERMINAL and row[self.F_TERMINAL]:
            return True
        last_epoch = row[self.F_LAST_EPOCH]
        if not last_epoch:
            return False
        # 单闸：查表得间隔，不再是「墙钟闸 + 轮次折算取大者」两道。
        # 两道闸的取大者等价于「用较大的那个间隔」，而那个间隔现在由
        # backoff_interval() 一次给出——它已经把 dry_streak 考虑进去了。
        #
        # ★ 再叠一层**采集源等级**（scripts/source_tiers.py）。
        #
        # 为什么要有等级：2026-10-04 实测 143 个源里 70 个「连续零产出 4 次」，
        # 而 daily 档每天回访 3 次 —— 等于把大部分预算花在死源上。实测那一刻
        # 有 126 个源是**本周内刚产出过**的，也就是说绝大多数源是活的，
        # 只是被当成「采干」反复回访。
        #
        # 等级取自实测证据（见 source_tiers.py 的 THRESHOLDS，每条都有依据）：
        #   T0 主力   稳定产出        -> 标准间隔
        #   T1 低产   采得到但稀薄    -> 16 小时
        #   T2 需渲染 静态拿不到      -> 标准间隔（但路由不同，跳过静态尝试）
        #   T3 形态可疑 正文是栏目页  -> 12 小时
        #   T4 长眠   久没产出        -> 7 天
        #   T5 不可采 明确失效        -> 30 天
        #
        # 间隔取「等级下限」和「退避间隔」的**较大者**——等级只降频、
        # 不放行，所以一个被判 T4 的源不会因为退避表刚好到期就被高频回访。
        #
        # 等级表读不到时退化成原来的行为（不改变任何现有语义）。
        base = self._gate_seconds(source_id, row[self.F_DRY_STREAK], tier)
        return (time.time() - last_epoch) < base

    def _gate_seconds(self, source_id: str, dry_streak: int,
                      tier: str = "daily") -> int:
        """一个源两次回访之间的**真实**间隔（秒）。

        ``max(退避间隔, 等级下限)``。这是唯一的判据来源：:meth:`is_sleeping_for`
        拿它判「现在能不能采」，:meth:`due_at` 拿它算「最早什么时候能采」。

        为什么不直接各写一份：2026-10-06 实测踩过 —— ``due_at`` 漏了等级下限，
        健康源被少报到 8 小时（实际闸门 720 小时）。两份判据一旦分叉，
        症状是「调度器算的等待时间和实际能不能采对不上」，排查时极难定位，
        因为两边各自都「看起来合理」。合成一个函数后这个 bug 就不可能复发。
        """
        base = self.backoff_interval(dry_streak, tier)
        floor = self.TIER_MIN_SECONDS.get(self.tier_of(source_id)) or 0
        return max(base, floor)

    def due_at(self, source_id: str, tier: str = "daily") -> int:
        """这个源最早什么时候可以再访问（epoch 秒）。没被退避就返回 0。

        终态源返回 ``TERMINAL_NEVER``（一个很远的未来），这样调度器算 next_due
        时不会因为「还有几个源在终态里」而反复空转。

        ★ 必须和 :meth:`is_sleeping_for` 用**同一个间隔**，即
        ``max(退避间隔, 等级下限)``。两者算得不一样时，调度器会被骗。

        2026-10-06 修。此前这里只算 ``backoff_interval(...)``，漏了
        ``TIER_MIN_SECONDS`` 的等级下限，于是**健康源被严重少报**：

            档   零产出  退避间隔  等级下限   due_at 报   实际闸门   少报
            T1     0/1      8h      16h       8h        16h        8h
            T3     0/1      8h      12h       8h        12h        4h
            T4       0      8h      168h      8h       168h      160h
            T5       0      8h      720h      8h       720h      712h

        触发条件是「等级下限 > 退避间隔」，也就是**采集刚恢复**的那段时期 ——
        恰恰是最该准时回访的时候。2026-10-06 实测那一刻全库 147 个源无一命中
        （`dry_streak` 普遍 ≥4，退避间隔已经盖过等级下限），所以这是个潜伏
        bug，不是正在造成损失的 bug。但它会在采集回暖时发作。

        症状可辨认：调度器算出一个很近的 `next_due`，准点醒来，却发现所有源
        都被闸门挡��，一轮 0.2 分钟、零入库 —— status.json 里 rounds 5/7/8
        正是这个形状。
        """
        row = self.data.get(source_id)
        if not row:
            return 0
        if len(row) > self.F_TERMINAL and row[self.F_TERMINAL]:
            return self.TERMINAL_NEVER
        last_epoch = row[self.F_LAST_EPOCH]
        if not last_epoch:
            return 0
        base = self._gate_seconds(source_id, row[self.F_DRY_STREAK], tier)
        return int(last_epoch + base)

    def record(self, source_id: str, produced: bool,
               terminal: bool = False) -> None:
        """记一次**真实访问**的结果。只对真正抓过的源调用。

        ``terminal=True`` 表示这个源**永远不该再被访问**。目前只有一种情况：
        链接超出了源自己的域名白名单（collectors/html_list.py 的
        ``_is_allowed_url``）—— 那是我们的配置边界，绕它等于越权采集。

        **robots.txt 拒绝不再是终态**（2026-09-30 改）。原先把 robots 拒绝
        当终态，业务决策是「重庆不能没有数据」，改为静态拿不到就升级到
        render/markdown（见 routes.py 的 PermissionError 分支与
        AGENTS.md §3.2）。robots 被拒但渲染也拿不到的源，按普通零产出走退避，
        不是永久拉黑。

        效率上的收益仍然在：被标为终态的源不再排进队列，不再每轮烧时间。
        """
        row = list(self.data.get(source_id) or [])
        row.extend([0] * (self.FIELDS - len(row)))
        row[self.F_VISITS] += 1
        if terminal:
            row[self.F_TERMINAL] = 1
        if produced:
            row[self.F_DRY_STREAK] = 0
            row[self.F_ZERO_VISITS] = 0
            row[self.F_LAST_PRODUCED] = int(time.time())
        else:
            row[self.F_DRY_STREAK] += 1
            row[self.F_ZERO_VISITS] += 1
        row[self.F_LAST_RUN] = self.runs
        row[self.F_LAST_EPOCH] = int(time.time())
        self.data[source_id] = row[:self.FIELDS]

    def is_terminal_blocked(self, source_id: str) -> bool:
        """这个源是终态吗（永不再访问）。目前只有白名单拒绝会置这个标记。"""
        row = self.data.get(source_id)
        if not row or len(row) <= self.F_TERMINAL:
            return False
        return bool(row[self.F_TERMINAL])

    def is_circuited(self, source_id: str) -> bool:
        """该不该熔断（踢出本趟采集）。

        判据：访问次数够多、其中零产出占绝大多数，且**已经 3 天没有任何出货**。

        ★ ``F_LAST_PRODUCED == 0`` 表示「**这个计数器不知道**」，不是「这个源
        从来没出过货」。

        2026-10-05 实测踩过这个混淆，代价很大。旧代码在 ``last_produced`` 为 0 时
        直接 ``return True``，理由是「访问过这么多次一次都没出过货」。但
        ``F_LAST_PRODUCED`` 只在 ``record(produced=True)`` 时写入，而早期回填
        进来的数据不经过它 —— 于是 143 个源里有 **65 个被判永久熔断**，
        包括库里已有几百条、且实测还有几百条没采的源：

            js_gxt    库内 484 条  实测未入库 316
            cq_fgw    库内 432 条  实测未入库 203
            cq_ny     库内 327 条  实测未入库 170
            cq_kjt    库内 268 条  实测未入库 219
            moa_gov   库内 290 条  实测未入库 205

        后果是采集几乎什么都不做：10-04 入库 0 条、10-05 只有 5 条，
        日志只留一行「61 个源被熔断」，看着像正常节流。

        修法是让「不知道」退回「上次访问时间」：没有出货证据时，
        从**上次被访问**起算 3 天。这样
          * 真死源 —— 照样 3 天后熔断，结论不变；
          * 只是计数器没数据的健康源 —— 不会被误杀。
        取 max 是保守方向：宁可多访问几次，也不要用缺失数据杀掉源。
        """
        row = self.data.get(source_id)
        if not row:
            return False
        if (row[self.F_VISITS] < self.CIRCUIT_VISITS
                or row[self.F_ZERO_VISITS] < self.CIRCUIT_ZERO_STREAK):
            return False
        last_produced = row[self.F_LAST_PRODUCED]
        reference = last_produced or row[self.F_LAST_EPOCH]
        if not reference:
            # 连访问时间都没有（纯手工写进 backoff 表的条目）：无证据，不判死。
            return False
        return (time.time() - reference) >= self.CIRCUIT_IDLE_SECONDS

    def circuit_list(self, candidates: Sequence[str]) -> list[str]:
        return [sid for sid in candidates if self.is_circuited(sid)]

    def health(self) -> list[dict[str, Any]]:
        """逐源健康快照，供自迭代报告和人工排查用。"""
        return [{
            "source_id": sid,
            "visits": row[self.F_VISITS],
            "zero_visits": row[self.F_ZERO_VISITS],
            "dry_streak": row[self.F_DRY_STREAK],
            "last_visited": row[self.F_LAST_EPOCH],
            "last_produced": row[self.F_LAST_PRODUCED],
            "circuited": self.is_circuited(sid),
        } for sid, row in sorted(self.data.items())]

    def prune(self, active: set[str]) -> None:
        """丢掉**注册表里已不存在**的源的记录。

        注意 ``active`` 必须是**未经回访闸过滤的**源全集。传过滤后的列表会
        造成死循环，2026-09-30 实测就是这样：源被回访闸挡住 → 不在列表里 →
        记录被删 → 下轮查不到、判定「没访问过」→ 放行 → 记录再被删。
        回访闸从来没有真正生效过：退避表跑了几十轮只剩 2 条，而 82% 的时间
        花在「已采干」源上反复确认。

        所以调用方传原始 source_ids，注册表里删掉的源自然会在下一轮清掉。
        """
        for key in [k for k in self.data if k not in active]:
            del self.data[key]

    def save(self) -> None:
        # 见 lighthouse/atomic_write.py：Windows 上替换正被读取的文件会抛
        # WinError 5，曾让调度器整夜崩溃 62 次。重试和降级都在那一个模块里。
        from lighthouse.atomic_write import atomic_write_json

        how = atomic_write_json(self.path(), self.data)
        if how == "overwrite":
            log(f"退避状态被占用，降级为直接覆盖写入：{self.path().name}")


# 自我迭代调整过的退避倍率在这里读回，**模块导入时就生效**。
#
# 为什么必须放在 import 时而不是 main() 里：``overnight.py``、``watchdog.py``、
# ``status_summary.py`` 全都直接 import 这个模块来算「哪些源能采」，只有
# ``run.py`` 的 main 会走一遍读回——那样调度器和采集器就会看到**不同的倍率**，
# 于是「可采源数」和「实际会不会采」对不上，正是 2026-10-02 那类静默错位的
# 来源。放在这里，全进程只有一个事实来源。
#
# 用 getattr 而不是直接调：测试会用桩类替换 SourceBackoff（只实现被判据用到的
# 方法），import 期无条件调用会让那些桩直接 AttributeError —— 2026-10-02
# 实测踩到，``test_all_sources_paused_round_does_not_crash`` 当场炸了。
_load_override = getattr(SourceBackoff, "load_backoff_override", None)
if callable(_load_override):
    _load_override()


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
    # 源里**显式写了**这两个字段，就当作作者的显式意图，下面的下限一律不碰它。
    #
    # 2026-10-02 实测踩过：想按产出把源分档——C 档（库内 >=100 条的高产源）
    # 深翻页 12、B 档其余 3 页保持快节奏。但 raise_archive_pages_below=30
    # 无条件往上抬，结果**每一档都被算成 30 页**，B 档的"3"完全无效，
    # 而每源 30 页正是那轮「145 源 / 30 分钟预算只跑完 36 个」的慢源根因。
    #
    # 只填空（没写就用默认）仍然要抬下限：那是防漏配，不是覆盖决定。
    explicit = {k for k in ("archive_pages", "max_items") if merged.get(k)}
    for key in ("archive_pages", "max_items"):
        value = defaults.get(key)
        if value and not merged.get(key):
            merged[key] = int(value)
    # 只填空不覆盖是不够的：52 个源在 sources.yaml 里已经写了 archive_pages: 6，
    # 它们拿不到「翻页多翻 3 倍未采条目」这个实测收益。所以两个字段都按下限抬。
    for key, knob in (("archive_pages", "raise_archive_pages_below"),
                      ("max_items", "raise_max_items_below")):
        floor = defaults.get(knob)
        if key in explicit or not floor:
            continue
        if int(merged.get(key) or 0) < int(floor):
            merged[key] = int(floor)
    # 命令行 --archive-pages 只当**下限**，不覆盖源里的显式声明。
    #
    # 原注释写的是「通宵调度在产出衰减时会显式加深，用命令行覆盖优先级最高」，
    # 那个语义会让按产出分档彻底失效：2026-10-03 实测
    #   js_gxt（源里写 12，C 档深翻）传 --depth 30 -> 30
    #   miit_gov（源里写 3，B 档快节奏）传 --depth 30 -> 30
    # overnight 固定传 ``--depth 30``（起始翻页深度），于是**每一档都被算成
    # 30 页**，B 档的 3 形同虚设。而每源 30 页正是那轮「145 源 / 30 分钟预算
    # 只跑完 36 个」的慢源根因——上一版只修了 ``raise_archive_pages_below``
    # 这道地板，命令行这条同样的漏洞没堵，B/C 分档等于从没生效过。
    #
    # 语义统一成一句话：**显式值是作者的意图，地板和命令行都只填空不覆盖。**
    # 真需要全局加深时改 config 的 collect_defaults，那本来就是干这个的位置。
    if archive_pages and "archive_pages" not in explicit:
        if int(merged.get("archive_pages") or 0) < int(archive_pages):
            merged["archive_pages"] = int(archive_pages)
    return merged


def apply_tier_policy(source: Mapping[str, Any],
                      tiers: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """把源等级的**采集方法**覆写到这个源的配置上。

    等级制度的意义是「回答这个源下一步该用什么方法试」，不是贴标签。
    ``SourceBackoff.is_sleeping_for`` 已经接了 ``revisit_seconds``（只降频），
    这里接上 ``routes``（尝试顺序）。

    ★ 不再收窄 ``archive_pages`` / ``max_items``。

    2026-10-05 实测推翻了原来「T3 收窄深度、每档压 max_items」的设计假设。
    ``scripts/measure_pagination_headroom.py`` 量出来的增量空间：

        moa_gov   库内 290 条
                  depth  3 →   59 URL，  未入库   0
                  depth 12 →  235 URL，  未入库   0
                  depth 30 →  495 URL，  未入库 205
        mct_gov   库内  59 条
                  depth  3 →   93 URL，  未入库  42
                  depth 12 →  120 URL，  未入库  63

    也就是说**深翻页能拿到几百条真实新公告**。而当时 135/143 个源卡在退避里、
    10-04 入库 0 条 —— 系统是**饿**的，不是**撑爆**的。在这个状态下按等级收窄
    深度和条数，等于在最需要深挖的时候把深挖 关掉，等级直接变成反效果。

    真要省预算，该动的是 ``revisit_seconds``（少回访几次），不是少翻几页：
    少回访只省时间，翻页深度直接决定还有多少数据没被采走。

    保留 ``depth`` 字段写入是因为它是缓存里的一部分（``SourceTier.to_dict``
    会存），但采集器读的是 ``archive_pages``，写 ``depth`` 不影响行为。
    """
    merged = dict(source)
    source_id = str(merged.get("id") or "")
    notes: list[str] = []
    tier = (tiers.get(source_id) or {}).get("tier")
    if not tier:
        return merged, notes

    tier_row = tiers.get(source_id) or {}
    # depth 只是记录，不参与采集（采集器读 archive_pages）。写它是为了让
    # source_tiers 的诊断输出和实际行为不互相误导。
    if tier_row.get("depth") is not None:
        merged["tier_depth_advisory"] = int(tier_row["depth"])

    routes = tier_row.get("routes")
    if isinstance(routes, list) and routes:
        merged["tier_routes"] = [str(r) for r in routes]
        notes.append(f"tier {tier} 路由: {'>'.join(str(r) for r in routes)}")
    return merged, notes


def _restrict_ladder(ladder: Any, routes: Any) -> bool:
    """按等级把 ladder 的尝试顺序**前移**到指定层。返回顺序是否真的变了。

    ★ 是「前移」不是「只留」。2026-10-05 实测踩过：
    一开始写成过滤（只保留 routes 里的层），ndrc_gov 立刻变成
    ``丢弃原因 {'render:too_short': 4}`` —— 0 条入库。
    原因是 T2 的 ``routes: ["render"]`` 语义是「静态拿不到，**优先**走渲染」，
    表达的是**顺序**，不是**排除**。过滤掉 static/markdown 之后，
    这个源只剩渲染一条路，而渲染恰好拿不到正文（too_short）——
    于是 26 个 T2 源会被静默废掉。

    所以这里只做「提到最前面」，其余层按原顺序保留兜底。
    等级是**推断**出来的，配置是**事实**：宁可多试两层，也不能让源因为
    推断错误而采不到数据。真正该熔断的 T5 用的是 ``routes: []``，
    空列表会让本函数直接返回 False，交给既有的 terminal 机制处理。
    """
    want = [str(r) for r in (routes or [])]
    if not want or not getattr(ladder, "order", None):
        return False
    head = [n for n in want if n in ladder.order]
    if not head or head == list(ladder.order):
        return False
    rest = [n for n in ladder.order if n not in head]
    ladder.order = head + rest
    return True


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
    # 等级覆写必须在 with_defaults **之后** —— 默认地板/命令行是给
    # 「还不知道等级的源」用的，等级是实测的，应当压过它们（见 apply_tier_policy）。
    _src, tier_notes = apply_tier_policy(with_defaults(source, cfg, archive_pages),
                                         SourceBackoff.tiers())
    notes.extend(tier_notes)
    collector = build_collector(_src, ladder.levels)
    ladder.collector = collector
    if _restrict_ladder(ladder, _src.get("tier_routes")):
        notes.append("tier routes 优先: " + ">".join(ladder.order))
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
    # ★ 按翻页深度分档给时间预算，别一刀切。
    #
    # 实测 2026-10-02（145 源一轮，105 源真跑）：
    #   中位 271s | 75% 662s | 90% 1200s | 95% 1403s | max 1805s（顶到上限）
    #   超 600s 的 31 个源吃掉 68% 的总时长
    #
    # 一刀切压低会砍错东西：那些慢源**确实有产出**（478 条），只是当时被
    # max_items=20 卡住。而一刀切抬高，B 档那 114 个「翻 3 页取当日增量」的
    # 源也跟着慢——它们压根不需要 30 分钟，一个卡死的源就吃掉整轮预算。
    #
    # 所以：翻页深的源给够（它要真的翻完），浅的源卡死（翻 3 页还花 25 分钟
    # 说明卡住了，不是在挖历史）。判据用已合并好的 archive_pages，
    # 不另设配置项——它本来就是「这个源要多深」的声明。
    rl_cfg = cfg.get("rate_limit") or {}
    deep_pages = int(_src.get("archive_pages") or 0)
    base_budget = float(rl_cfg.get("source_budget_seconds", 300))
    deep_budget = float(rl_cfg.get("deep_source_budget_seconds", 1200))
    stall_budget = float(rl_cfg.get("stall_source_budget_seconds", 420))
    if deep_pages >= 10:
        time_budget = max(base_budget, deep_budget)
    elif deep_pages <= 3:
        time_budget = min(base_budget, stall_budget)
    stale = 0
    taken = 0
    # 404/410 连续命中到阈值也早停。
    #
    # 回归 2026-09-30（mct_gov）：第 1 页 URL 域名对（zwgk.mct.gov.cn），
    # 翻到第 2~6 页时站点返回**另一个旧站点**的链接（www.mct.gov.cn 的
    # /ghjh/2020../ /tjxx/2025.. 等），全部 404。那 14+ 条 404 逐条走
    # 「静态 30s → 渲染 60s → markdown 60s」三级升级，一个源就烧光
    # source_budget_seconds=300 却产出 0 条。
    #
    # 404 是不死的信号（重试、换路由都没用），性质和「已入库的 URL」一样：
    # 后面还有多少条都无关紧要。所以要计入早停，而不是 continue 掉继续烧。
    dead = 0
    dead_limit = max(stale_limit, 5)

    # ── 深翻游标：记住上次 early stop 停在哪 ──────────────────────
    #
    # 为什么需要（2026-10-07 实测，这是「效率提不起来」的根因）：
    # 原判据是「连续遇到 max_stale 个已入库 URL 就收工」。它把「这个 URL 我
    # 已经有了」当成了「这条线后面都不用看了」——而这两件事在**采干的源**上
    # 完全不等价。列表页是倒序的：最新在最前面，于是每一轮都在重新爬同一段
    # 已知内容、每一轮都在同一个位置得出同一个「无新内容」的结论。
    #
    # 实测代价（近 40 轮）：93% 的时间（1843/1973 秒）花在零产出访问上，
    # moa_gov 一次 302 秒、cq_kjt 267 秒、mem_gov 247 秒，全是零产出。
    # 同一个 bug 还在**永久丢数据**：moa_gov 库内 290 条，而 depth=30 实测
    # 列表页有 495 条 URL、其中 205 条从未入库 —— 全在老尾，永远看不到。
    #
    # 修法：early stop 时记住「本轮扫到第几个」，下一轮从那个位置之后继续。
    # 已知 URL 用来**跳过**，不再用来**停止**。
    #
    # 只在「正常扫完 /early stop」时前进。撞时间预算、死链收工都是中途退出，
    # 那时位置不代表「这一段已经确认入库」，不能推进——否则会跳过没看过的新内容。
    cursor = _cursor_load().get(source_id, 0)
    if cursor and len(links) > cursor:
        skipped = cursor
        links = links[cursor:]
        notes.append(f"resume from cursor {cursor} ({skipped} links already swept)")
    elif cursor and len(links) <= cursor:
        # 列表变短了（站方改版/清理）。丢弃游标从头扫，否则会永远跳过。
        notes.append(f"cursor {cursor} >= list length {len(links)}, reset")
        cursor = 0

    position = 0
    for url in links:
        position += 1
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
                _cursor_save(source_id, cursor + position)
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
            if _is_dead_url(detail):
                dead += 1
                if dead >= dead_limit:
                    notes.append(
                        f"early stop after {dead} dead urls "
                        f"({detail.dropped_reason}) —— 列表页翻页后拿到的是"
                        f"过期/错域名链接，继续只会烧预算")
                    break
            continue


        fields = extract_fields(detail.content, cfg)
        # 发布机构以 sources.yaml 的人工登记值为准，正则抽的只作兜底。
        # 正则会把「准确获取四川省商务厅」这种动词+状语当成机构名。
        if source.get("name"):
            fields["issuing_body"] = str(source["name"])
        # 分类必须在这里补。lighthouse 是一条旁路：它自己抽字段、自己拼摘要，
        # 但从不经过 extractors/regex_fallback.py，也就从不调用 classify()。
        # 后果是 category_l1 以 NULL 入库，而 AGENTS.md §6.2 硬条件第 6 条要求
        # 「category_l1 必须命中用户订阅分类」——空分类的记录永远匹配不到用户。
        # 实测库里 3849/6339 条空分类全部来自这条链路（content_source 同为 NULL）。
        classified = classify({
            "title": detail.title,
            "content": detail.content,
            "issuing_body": fields.get("issuing_body") or source.get("name"),
            "source": source,
        })
        summary, reason = build_summary(detail.title, detail.content, cfg, fields)
        # 注意：summary 为空时**仍然入库**，不要在这里丢。
        #
        # 曾经改成「no_usable_sentence 就丢」，实测是错的：拿库里 71 条同判据的
        # 记录重跑摘要器逐条看过，69 条是真公告（正文是图片，所以抽不出句子）——
        #   新疆...关于党中央、国务院批准设立县级草湖市的公告
        #   《宁夏回族自治区政务数据共享管理办法（试行）》图解
        #   广东省基础与应用基础研究基金委员会关于...项目验收
        # 丢掉等于丢真政策。摘要空是**内容抽取能力**的边界（政务站大量用图解读
        # 策），不是该藏起来的脏数据；标题本身仍有价值，详情页也有正文原图。
        # 真正的修法是给图解读策做正文抽取，不是在入库前丢弃。
        content_hash = BaseCollector.compute_content_hash({
            "title": detail.title, "publish_date": detail.publish_date,
            "url": url, "content": detail.content,
        })
        # 纯正文指纹（不含 URL）。content_hash 把 url 算进去，所以同一篇文章
        # 挂 http/https 两个地址、或被两个源转载时会算出两个不同的 hash，
        # upsert 认不出来就各存一行。
        #
        # 实测这不只影响旧链路：lighthouse 从来不设这个字段，全库 9467 条里
        # 6980 条指纹是空的，dedup 因此只认出极少数重复。补上后跨 URL 重复
        # 才认得出来。
        content_fingerprint = BaseCollector.compute_content_fingerprint(
            {"content": detail.content})
        records.append({
            "source_id": source_id,
            "source_url": url,
            "url": url,
            "title": detail.title,
            "publish_date": detail.publish_date or fields.get("publish_date") or "",
            "content": detail.content,
            "summary": summary,
            # key_points 要的是**要点列表**，不是一整块摘要。
            #
            # output/markdown.py:18 的 build_rule_summary 只认 key_points，
            # 不看 record["summary"]，所以这里必须塞 —— 之前实测写出来的
            # markdown 里「规则摘要」只剩金额和截止时间。
            #
            # 但塞法有坑（2026-10-07 实测）：原先写的是 ``[summary]``，
            # 一整块当**单条**要点。而 scripts/resummarize.py 用
            # summary_refine.build_summary() 重算时写的是**逐句列表**。
            # 同一列两种形态，于是两边反复互相覆盖：采集写 blob → resummarize
            # 改列表 → 下一轮采集再改 blob。
            # 后果是 196 条记录存着**过期摘要**——摘要里的话在 content 里
            # 已经找不到了（content 被后续清洗改过，摘要没跟着重算）。
            #
            # 所以按句拆：``｜`` 是 build_summary 拼要点时的连接符（摘要本身
            # 就是 ``"".join(points)``），拆出来才能和 resummarize 产出同构。
            "key_points": _summary_points(summary),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "content_hash": content_hash,
            "content_fingerprint": content_fingerprint,
            "issuing_body": fields.get("issuing_body") or source.get("name"),
            "gov_level": source.get("gov_level"),
            "level": source.get("level"),
            # 地区必须在这里补。和上面的 category_l1 是同一个坑：lighthouse 自己抽
            # 字段、自己入库，不经过主链路的字段补全，于是 sources.yaml 里人工
            # 登记的 region_province 从来没被带进记录。后果是每条 lighthouse
            # 入库的记录 region_province 都是 NULL —— 实测 id>3434 的近 3000 条
            # 全空，首页地区分布、中国地图、地区归属覆盖率一起失真
            # （2026-09-29：库里 6403 条只有 1991 条有地区，而这 1991 条全是
            #  旧 batch_collect 链路写的）。取源登记值为准，不从正文猜。
            "region_province": source.get("region_province"),
            "region_city": source.get("region_city"),
            "region_county": source.get("region_county"),
            "category_l1": classified["category_l1"],
            "category_l2": classified["category_l2"],
            # 交叉验证和热度聚类都按 source_type 判定「这是不是一手官方源」。
            # 不带这个字段，media_cluster 的闸门会把整库当非媒体跳过，
            # heat_topics 就永远是空的。
            "source_type": source.get("source_type"),
            "content_source": _content_source(detail.route_used),
            "amount": fields.get("amount"),
            "deadline": fields.get("deadline"),
            "route_used": detail.route_used,
            "attachment_links": detail.attachments,
            "summary_status": "ok" if summary else f"skipped:{reason}",
        })
        if summary and summary[-1] not in "。！？；":
            notes.append("summary not terminated (unexpected)")

    # 「列表里的 URL 全是已知的」本来一条 note 都不留，shadow 报告里就显示成
    # 「-」，归类进 unknown，看着像源坏了。其实这是最健康的一种空转 ——
    # 增量采集的正常结果。实测 2026-09-30：13 个源里 4 个是这种，占 unknown 的
    # 大头；没有这条 note，tune_stats 就把它们当成没诊断出来的问题。
    if not notes and links and not records:
        notes.append(f"all {len(links)} list urls already known (no new content)")

    # 逐源那行由主循环统一打（它才有全局进度上下文）。
    # 之前 collect_source 自己打一遍、主循环再打一遍，同一个源出现两行。
    return {"source_id": source_id, "records": records, "dropped": dropped,
            "notes": notes, "seconds": round(time.time() - started, 1)}


# ------------------------------------------------------------------ 主流程

def main(argv: list[str] | None = None) -> int:
    # 读回自我迭代上次调过的退避倍率。没有这一步，自动调整就是一次性的
    # —— 进程一换就丢，等于没调过。（import 时已读过一次，这里是给测试
    # 替换过桩类的情况兜底。）
    _reload = getattr(SourceBackoff, "load_backoff_override", None)
    if callable(_reload):
        _reload()
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
    # stamp 必须在这里就定下来：「全部源都在回访间隔里」那条早退分支
    # （下面 sources 为空时）也要用 _finish 落一份报告，好让调度器知道下一个
    # 源什么时候到期。之前 stamp 定义在这之后，早退分支直接 UnboundLocalError，
    # 而「所有源都在间隔里」在密集轮次下是常态 —— 等于每轮必崩。
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
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

    sources = sort_sources(sources, cfg)

    # 采干退避：把预算从"已采干的源"挪给还有新内容的源。
    #
    # 实测（2026-09-29，60 分轮 / 9 并发）：前 18 个源里 13 个部委源
    # "early stop after 40 known urls"，采到 0 条 —— 它们早就采干了，公告更新
    # 周期以周/月计。但每次仍要为"确认还是已知的"抓一遍列表页，单个 90~250 秒，
    # 13 个源就是二三十分钟，占掉一轮预算的三分之一，产出 0。
    #
    # 这不是源的问题，是调度的问题。连续零产出的源退避几轮再回访：既省预算，
    # 又不会真的漏掉（退避期满自动回来）。退避状态存在 runtime 下，跨进程
    # 有效，watchdog 重启调度器也不会丢。
    if not args.dry_run and not args.fresh:
        backoff = SourceBackoff.load()
        # 过滤**之前**的源全集。prune() 用它，理由见 prune 的注释：传过滤后的
        # 列表会让回访闸的记录被自己擦掉，闸等于没装。
        all_source_ids = [str(s.get("id") or "") for s in sources]
        # 先记下被跳过的那些，后面算「下一个能访问的是谁、什么时候」
        sources_paused = [s for s in sources
                          if backoff.is_sleeping_for(str(s.get("id") or ""),
                                                     str(s.get("cadence_tier") or "daily"))]
        sources = [s for s in sources if s not in sources_paused]
        paused = len(sources_paused)
        if paused:
            log(f"回访间隔/采干退避：本轮跳过 {paused} 个源"
                f"（刚访问过、或连续零产出，等退避期满自动回来）")
        # 熔断：被真实访问过多次、绝大多数零产出、且长期没出货的源，踢出本趟。
        # 这是自迭代里第二个真能改变结果的动作 —— 回访间隔只是「等一等」，
        # 熔断是「别再来了」。政府站有淡旺季，所以判据要求长期没出货而不是
        # 「连续几轮没产出」，避免误伤只是这阵子没发文的源。
        ids = [str(s.get("id") or "") for s in sources]
        blown = set(backoff.circuit_list(ids))
        if blown:
            sources = [s for s in sources if str(s.get("id") or "") not in blown]
            log(f"熔断：本轮踢出 {len(blown)} 个长期零产出的源 {sorted(blown)}")
        # 下一个源什么时候可以再访问。调度器靠它决定「这一轮结束后该等多久」
        # —— 没有这个数，轮次就按固定节奏空转：实测源在 13/21 之间来回荡，
        # 每轮 5~7 分钟、产出 0 条，一天白跑 40 轮。见 overnight.py 的轮间节流。
        #
        # **语义 = 「下一次值得醒来的时刻」**，所以要分两种情况（2026-09-30 修）：
        #
        #   * 现在就有的源（未被闸挡住）→ 0，表示「别等了，接着采」
        #   * 全被挡住 → 被挡源里最早到期的那个
        #
        # 只算被挡源会漏掉第一种：实测 mca_gov/mct_gov 时钟早到期 5.5~5.7 小时
        # 且 streak 未达退避门槛，现在就能采；而 nea_gov 要等 2.1 小时。
        # 只取后者的 next_due=2.1 小时，等于让能采的源白等 2.1 小时。
        # 判「有没有源现在能采」必须查 **is_sleeping_for**，不只是终态标记。
        #
        # 2026-10-02 实测：这里原来只排除 terminal，于是 32 个**在退避闸内**
        # 的源也算 runnable，next_due 恒为 0。watchdog 读不到到期时间就退化成
        # 「睡 300 秒再试」，一夜空转 288 次，而且日志每次都报
        # 「无源可采」——看起来像正常节流，实际是永远等不到点。
        #
        # is_sleeping_for 自带 tier 参数，必须传该源自己的 cadence_tier
        # （weekly 源的下限是 7 天，用 daily 的 8 小时判会永远放行）。
        runnable = [
            s for s in sources
            if not backoff.is_terminal_blocked(str(s.get("id") or ""))
            and not backoff.is_sleeping_for(
                str(s.get("id") or ""), str(s.get("cadence_tier") or "daily"))
        ]
        if runnable:
            next_due = 0                 # 有源可采 → 调度器不该等
        else:
            # 终态源排除：due_at 返回 TERMINAL_NEVER（135 年后），算进
            # min() 之外就会让日志出现「约 22713866 分钟后可访问」这种天文数字。
            due_times = [backoff.due_at(str(s.get("id") or ""),
                                        str(s.get("cadence_tier") or "daily"))
                         for s in sources_paused
                         if not backoff.is_terminal_blocked(
                             str(s.get("id") or ""))]
            due_times = [t for t in due_times if t]
            next_due = int(min(due_times)) if due_times else 0
    else:
        sources_paused, next_due = [], 0
        all_source_ids = [str(s.get("id") or "") for s in sources]

    if not sources:
        # 全部源都在退避/间隔里。这一轮没有活干，但**必须把 next_due 报出去** ——
        # 调度器靠它决定等多久再起下一轮。没有这个数就会按固定节奏空转：
        # 实测源在 13/21 个之间来回荡，每轮 5~7 分钟、产出 0 条，一天白跑 40 轮。
        log(f"全部 {len(sources_paused)} 个源都在回访间隔/退避中，本轮无事可做")
        # 早退轮次**也要推进轮次计数器**（2026-09-30 修）。
        #
        # 不推进的话 `runs` 永远停在这里，而各源记录的 `F_LAST_RUN` 停在
        # 最后一次真采集的轮次上，于是 `is_sleeping_for` 的轮次退避判据
        # `(runs - F_LAST_RUN) < sleep_for` 永远成立 —— 回访闸会把每个源
        # 永久挡住，除非时钟闸（8 小时）先放行。
        #
        # 实测症状：13:23 那轮「跳过 34 个源」，而实际上 mca_gov / mct_gov
        # 时钟早已到期（13.4 小时 > 8 小时门槛）却被挡住；`runs` 停在 13
        # 不再前进，watchdog 只能干等 `pacing_wait` 的 187 分钟。
        if not args.dry_run and not args.fresh:
            backoff.next_run()
            backoff.save()
        _finish(cfg, [], stamp, args, next_due=next_due)
        return 0

    tiers: dict[int, int] = {}
    for source in sources:
        tier = _focus_tier(source, cfg)
        tiers[tier] = tiers.get(tier, 0) + 1
    log("采集档位分布：" + "  ".join(
        f"T{tier}={count}源" for tier, count in sorted(tiers.items())))
    log("前 10 个：" + "  ".join(str(s.get("id")) for s in sources[:10]))

    workers = args.workers or int((cfg.get("rate_limit") or {}).get("workers", 6))
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
        # 本轮把结果回灌给退避表：产出>0 清零连续计数，零产出累加。
        backoff = SourceBackoff.load()
        backoff.next_run()
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
                    # 回灌退避表。崩溃/被时限砍掉都算零产出 —— 那两种情况下一轮
                    # 再试一次是对的，不该因此判它采干。
                    crashed = any("崩溃" in str(n) for n in (outcome.get("notes") or []))
                    # 「被全局时限砍掉」和「崩溃」一样：源根本没被访问过。
                    #
                    # 这里以前只挡了 crashed，结果 --max-minutes 一到，
                    # 剩下没排到的源全部被 collect_source 以
                    # skipped=True 退回（run.py:828），然后照样走了
                    # record(produced=False) —— 那一行会干两件事：
                    #   1) dry_streak += 1
                    #   2) last_epoch = now
                    # 第 2 条直接把它们推进 8 小时退避，于是「预算不够」
                    # 变成了「这些源今天别采了」。145 源 / 30 分钟预算的实测：
                    # 105 个源 last_epoch 是「刚刚」，但一条都没真抓过，
                    # 下一轮「145 个源全部在回访间隔」就是这么来的。
                    #
                    # 上面那句注释（「下一轮再试一次是对的」）说的是对的，
                    # 代码没跟上。skipped 的源一次都不该进退避表。
                    if not crashed and not outcome.get("skipped"):
                        # 白名单拒绝是终态：标上之后永不再访问。
                        #
                        # 判据只认 **allowlist_denied**，不认 robots_denied。
                        # robots 已于 2026-09-30 改为「静态拿不到就升级到
                        # render/markdown」（routes.py 的 PermissionError 分支），
                        # 业务决策是重庆不能没有数据。所以 robots 拒绝现在**不是**
                        # 终态 —— 一个源被 robots 拒了但渲染也拿不到时，它只是
                        # 零产出，走正常退避，而不是永久拉黑。
                        #
                        # 白名单是我们自己的边界，绕它等于越权采集，保持终态。
                        blocked = any(
                            "allowlist_denied" in str(d.get("reason", ""))
                            for d in (outcome.get("dropped") or [])
                            if isinstance(d, dict))
                        if blocked:
                            log(f"  [{source_id}] 超出源白名单，标记终态，不再回访")
                        backoff.record(source_id, bool(outcome.get("records")),
                                       terminal=blocked)

                    # 镜像进 source_sync_state。这张表是灯塔**唯一**的对外状态
                    # 出口：Streamlit 指挥中心、overnight_health、五个审计脚本
                    # 和 web 的源健康页都读它，而它上一笔写入停在 2026-09-28 ——
                    # 灯塔接管采集后没人往这儿写，于是所有人看到的是 8 天前的快照。
                    #
                    # 放在退避 if 之外：崩了和被时限砍掉的源同样要留下痕迹，
                    # 那恰恰是最该被看见的两种情况。
                    #
                    # dry_run 不写：试跑的定义就是不动任何持久状态，
                    # 往 source_sync_state 写一行就把这条契约破了。
                    if not args.dry_run:
                        _mirror_sync_state(source_id, outcome, backoff)

                    # ── 自我迭代闭环：让蒸馏出的原则真正被执行 ──────────
                    #
                    # 这一段是 2026-09-30 补上的。此前原则表里 apply_count
                    # 累计到 137/167/263，而 success_count 和 fail_count 全是 0，
                    # best_action 全是 auto_observe（=什么都不做）—— 系统在
                    # 假装迭代：记了 137 次「应用」，实际零行动，utility 永远 0，
                    # 于是永远学不到东西。
                    #
                    # 现在按本源的真实失败类型去查原则表，命中可执行动作就真改
                    # 配置（有限额、会留痕），下一轮就能看到效果并让 success/fail
                    # 动起来 —— 那一列动起来，闭环才算真的通了。
                    try:
                        from lighthouse import principle_applier

                        for _ft in _failure_types(outcome, source_id):
                            _res = principle_applier.apply(source_id, _ft)
                            if _res.get("applied"):
                                log(f"  [{source_id}] 原则应用："
                                    f"{_res['action']}（{_res['reason']}）")
                    except Exception as _exc:               # noqa: BLE001
                        log(f"  [{source_id}] 原则应用异常（不影响采集）: "
                            f"{type(_exc).__name__}: {_exc}")

                    progress.finish_source(source_id, len(outcome.get("records") or []),
                                           len(outcome.get("dropped") or []))
                    log(f"  [{source_id}] {len(outcome.get('records') or [])} 条 / "
                        f"丢弃 {len(outcome.get('dropped') or [])} / "
                        f"{outcome.get('seconds')}s  "
                        f"{(outcome.get('notes') or ['-'])[-1]}")
                    progress.tick(force=True)
        finally:
            stop.set()
        if not args.dry_run:
            # 必须传**未过滤**的源全集：被回访闸挡住的源恰恰要保留记录，
            # 传过滤后的会让闸失效（见 prune 的注释）。
            # all_source_ids 已经是 id 字符串，不要再 s.get("id")。
            backoff.prune(set(all_source_ids))
            backoff.save()

            # 回填上一轮自动动作的效果 —— 闭环的最后一环。
            #
            # 没有这一步，原则表的 success_count / fail_count 恒为 0，
            # utility_score 永远是初值，distill 挑不出更好的动作，
            # best_action 永远不变：系统记了上千次「应用」却学不到东西
            # （2026-09-30 之前 apply_count 137/167/263，success/fail 全 0）。
            try:
                from lighthouse import principle_applier

                _ver = principle_applier.verify_pending()
                if _ver.get("success") or _ver.get("failed"):
                    log(f"  上一轮自动动作效果回填：成功 {_ver['success']} "
                        f"/ 失败 {_ver['failed']} / 待验证 {_ver['pending']}")
            except Exception as _exc:                   # noqa: BLE001
                log(f"  效果回填异常（不影响采集）: {type(_exc).__name__}: {_exc}")
            dry_now = sum(1 for v in backoff.data.values()
                          if v[0] >= SourceBackoff.DRY_STREAK_LIMIT)
            if dry_now:
                log(f"采干退避表：{dry_now} 个源连续零产出 >= "
                    f"{SourceBackoff.DRY_STREAK_LIMIT} 轮，下轮起按间隔回访")
        return _finish(cfg, results, stamp, args, next_due=next_due)
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


# ------------------------------------------------------------------ 源健康分类


def _mirror_error(outcome: Mapping[str, Any]) -> str | None:
    """把一次失败的**真实原因**压成一行，写进 ``source_sync_state.last_error``。

    为什么值得单独写：这张表里 55 个源的 ``last_error`` 全是字面量
    ``"unknown error"`` —— 那是 :meth:`Database.mark_source_result` 的兜底值，
    在 ``error=None`` 时原样落库。看着有错误信息，实际零信息量，排查时
    一无所获。这里用灯塔已经算好的失败类型和丢弃原因拼出真实原因。
    """
    parts: list[str] = []
    for kind in _failure_types(outcome)[:2]:
        parts.append(str(kind))
    reasons = [
        str(d.get("reason"))
        for d in (outcome.get("dropped") or [])
        if isinstance(d, dict) and d.get("reason")
    ]
    if reasons:
        # 同一个原因出现几十次时只算一次，否则这一行会长到没法看
        uniq = list(dict.fromkeys(reasons))[:2]
        parts.append("; ".join(uniq))
    if not parts:
        notes = outcome.get("notes") or []
        if notes:
            parts.append(str(notes[-1]))
    if not parts:
        return None
    return " | ".join(parts)[:500]


def _mirror_sync_state(source_id: str, outcome: Mapping[str, Any],
                       backoff: Any) -> None:
    """把一个源的结果镜像到 ``source_sync_state``。

    纯观测用途，时间全部由灯塔自己的退避表给出（:meth:`due_at`），这里不
    重新推导 —— 否则这张表就会长出第二套退避逻辑，和真正生效的那套对不上。

    整个函数吞掉所有异常：观测写不进去绝不能拖垮采集。写失败只留一行日志，
    下一轮还会再试；反过来如果这里抛异常，一个源的观测问题会掀掉整轮采集，
    那才是真的坏。
    """
    try:
        from storage.db import Database

        tier = SourceBackoff.tier_of(source_id)
        interval = int((SourceBackoff.tier_policy().get(tier) or {}).get("revisit_seconds")
                       or CADENCE_SECONDS["daily"])

        classify = _source_classify(outcome)
        # 分类到 status 的映射不能想当然。**no_new 不是失败** ——
        # _source_classify 的原话是「已全部入库，正常增量空转」，那是健康源的
        # 正常表现。把它算成 error，正是这张表当初 55 个 error 的由来：
        # 看着一片告警，其实大部分源好好的。
        #
        # skipped 也不是失败（「本轮没跑它，不是失败」），unknown 更不能算 ——
        # 源码注释原话：「unknown 一旦变多，真实故障就被淹了」。
        if classify in _REAL_FAILURES:
            status = "error"
        elif classify in ("skipped", "unknown"):
            status = classify
        else:
            status = "ok"

        dry = 0
        row = backoff.data.get(source_id) or []
        if len(row) > SourceBackoff.F_DRY_STREAK:
            dry = int(row[SourceBackoff.F_DRY_STREAK])
        # 连续失败只记真故障。dry_streak 对「健康但没新内容」的源也会累加
        # （灯塔靠它省预算），直接搬过来会让 151 个源看起来都在失败。
        # dry_streak 本身没有丢——看板上有「按空轮连续次数」那张图。
        failures = dry if status == "error" else 0

        due_epoch = backoff.due_at(source_id)
        Database(str(_ROOT / "data" / "beacon.db")).mirror_source_result(
            source_id,
            status=status,
            cadence_tier=tier or "daily",
            interval_seconds=interval,
            consecutive_failures=failures,
            error=_mirror_error(outcome) if status != "ok" else None,
            next_due_at=(datetime.fromtimestamp(due_epoch, timezone.utc)
                         if due_epoch else None),
        )
    except Exception as exc:                                   # noqa: BLE001
        log(f"  [{source_id}] 状态镜像失败（不影响采集）: {type(exc).__name__}: {exc}"[:200])


# _source_classify 的完整返回值见其 docstring / _finish 的 hints 表：
#   ok / skipped / no_new / no_links / broken_page / all_dropped / unknown
# 其中只有三个是真故障。no_new 是「已全部入库，正常增量空转」，
# skipped 是「到时限未轮到」，unknown 是「归不出类」—— 后者按源码注释的
# 要求刻意保留可见性，不能吞掉，否则真故障会被淹没。
_REAL_FAILURES = frozenset({"no_links", "broken_page", "all_dropped"})


def _source_classify(result: Mapping[str, Any]) -> str:
    """把一个源的采集结果归类，供逐源结果、自迭代、告警共用。

    零产出必须分类。「没有新东西」和「采不到东西」是两回事，混在一起看
    就只能看到一句「66 个源没产出」，既像坏了又不知道该修哪里。

    判据按**语义**归，不按某一句话归。教训来自 Agent Reach 的 probe.py：
    它把「看起来一样的失败」拆成 missing / broken / timeout / ok 四类，
    而不是笼统报错。分类同理——
    "early stop after 200 known urls" 和
    "all 29 list urls already known (no new content)" 是同一件事。

    实测 2026-10-02：只认前者时，最近一轮 17 个源里 9 个掉进 unknown，
    比任何真实故障都多。unknown 一旦变多，真实故障就被淹了。
    """
    if result.get("records"):
        return "ok"
    notes = " | ".join(result.get("notes") or [])
    if result.get("skipped") or "run time budget" in notes:
        # 到时限没轮到 / 采到一半到时限，都不是源的毛病。
        # 只认 entry 的 skipped 标志会漏掉后者：它在 URL 之间才触发。
        return "skipped"
    if "early stop" in notes or "already known" in notes:
        return "no_new"          # URL 都已入库，正常的增量空转
    if ("no links" in notes or "ladder found nothing" in notes
            or "list_selector" in notes or "fetch_list failed" in notes):
        return "no_links"        # 列表页取不到链接 → 选择器/URL 坏了
    if "static_page_failed" in notes or "archive_render_failed" in notes:
        return "broken_page"     # 单页 404 / 渲染失败 → 翻页地址或渲染链路
    if result.get("dropped"):
        return "all_dropped"     # 链接有但全被门禁拦下
    return "unknown"


def _finish(cfg: Mapping[str, Any], results: list[dict[str, Any]], stamp: str,
            args: argparse.Namespace, next_due: int = 0) -> int:
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
        # 下一个源最早可访问的时刻（epoch 秒，0 = 没有源在等）。调度器靠它节流，
        # 没有它轮次就会按固定节奏空转。
        "next_due_epoch": int(next_due or 0),
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

    buckets: dict[str, list[str]] = {}
    for result in results:
        buckets.setdefault(_source_classify(result), []).append(str(result.get("source_id")))
    log("-" * 66)
    log("逐源结果：")
    hints = {
        "ok": "采到数据",
        "skipped": "到时限未轮到（本轮没跑它，不是失败）",
        "no_new": "无新数据（已全部入库，正常增量空转）",
"no_links": "列表页取不到链接 → 需修选择器/URL",
    "broken_page": "页面报错（404 / 渲染失败）→ 需修翻页地址或渲染链路",
    "all_dropped": "链接有但全被门禁拦下",
    "unknown": "未分类",
    }
    for name in ("ok", "skipped", "no_new", "no_links", "broken_page",
                 "all_dropped", "unknown"):
        ids = buckets.get(name) or []
        if not ids:
            continue
        log(f"  {name:<11} {len(ids):>3} 个   {hints[name]}")
        if name in ("no_links", "broken_page", "all_dropped", "unknown"):
            log(f"              {' '.join(ids)}")
    log(f"报告 {out}")

    # 补写逐源分类计数。调度器靠 skipped 数判断"这轮有没有把源跑完"：
    # 没跑完时加深翻页是负反馈（单源更慢 -> 能碰到的源更少 -> 产量更低 -> 又加深）。
    # 实测 2026-09-29：depth 12->18->24，产量 49->7->3，13 个部委源早已采干，
    # 后面每轮全在 early stop 确认"还是已知的"。
    try:
        report = json.loads(out.read_text(encoding="utf-8"))
        report["buckets"] = {name: len(buckets.get(name) or [])
                             for name in ("ok", "skipped", "no_new", "no_links",
                                          "all_dropped", "unknown")}
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    except (OSError, ValueError):
        pass

    if not args.dry_run and records:
        from output.markdown import write_markdown_records

        # 交叉验证 + 聚簇。必须排在写 JSONL 之前：ingest 是从 JSONL 逐条入库的，
        # 这里算出的 corroboration_* / media_cluster_id / media_heat 才会跟着
        # 一起进 announcements。放在 ingest 之后就得再扫一遍全库补写。
        #
        # 之前这一段整个缺失，lighthouse 采进来的记录 corroboration_status
        # 全是默认 single_source、media_cluster_id 全空，heat_topics 永远 0 行。
        post = _postprocess(records, stamp)
        if post:
            log(f"后处理 交叉验证 {post['corroboration']} | 事件 {post['clusters']}")

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

            code = ingest(jsonl, db_path=str(_ROOT / "data" / "beacon.db"),
                          batch=int((cfg.get("general") or {}).get("ingest_batch", 50)))
            _persist_postprocess(jsonl, stamp)
            return code

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


def _postprocess(records: list[dict[str, Any]], stamp: str) -> dict[str, Any] | None:
    """交叉验证 + 聚簇（纯内存）。失败不能拖垮采集，返回 None 即可。"""
    from lighthouse.postprocess import analyze, load_rules

    try:
        outcome = analyze(records, load_rules(), now=datetime.now(timezone.utc),
                          sources=load_sources())
    except Exception as exc:                        # noqa: BLE001
        log(f"后处理失败（不影响本轮入库）：{type(exc).__name__}: {exc}")
        return None
    # analyze 返回的是带新字段的副本，换回原列表，JSONL 才写得到。
    enriched = outcome["records"]
    records[:] = enriched
    return outcome


def _persist_postprocess(jsonl: str, stamp: str) -> None:
    """ingest 之后补写热度表和图谱。失败只记日志。"""
    from lighthouse.postprocess import load_rules, persist
    from lighthouse.ingest import read_records
    from storage.db import Database

    try:
        records = list(read_records(jsonl))
        if not records:
            return
        from lighthouse.postprocess import analyze

        outcome = analyze(records, load_rules(), now=datetime.now(timezone.utc),
                          sources=load_sources())
        db = Database(str(_ROOT / "data" / "beacon.db"))
        try:
            persist(db, outcome["records"], outcome["clusters"],
                    now=datetime.now(timezone.utc))
            log(f"热度/图谱已写入：事件 {len(outcome['clusters'])}")
        finally:
            db.close()
    except Exception as exc:                        # noqa: BLE001
        log(f"热度/图谱写入失败（不影响本轮入库）：{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
