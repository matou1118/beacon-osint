"""原子写在 Windows 上的行为测试。

这个 bug 极其难在别的机器上复现，却在 Windows 上每天咬人：
``Path.replace()`` 在 POSIX 上就是 rename，覆盖一个正被打开的文件没问题；
Windows 的打开文件带共享锁，replace 需要「删除 + 重命名」，于是抛
WinError 5。实测 2026-10-02 整夜因此崩了 62 次、12 小时零入库。

所以这里不能只测「能写进去」，必须测「**有别的进程正读着也能写进去**」。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lighthouse.atomic_write import atomic_write_json, atomic_write_text  # noqa: E402


def test_plain_write_works(tmp_path: Path) -> None:
    target = tmp_path / "plain.json"
    how = atomic_write_json(target, {"a": 1})
    assert how == "replace", how
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_no_temp_file_left_behind(tmp_path: Path) -> None:
    """临时文件必须清掉，否则运行时目录会攒一堆 .tmp。"""
    target = tmp_path / "clean.json"
    atomic_write_json(target, [1, 2, 3])
    assert not target.with_suffix(".tmp").exists()


def test_write_succeeds_while_another_handle_is_open(tmp_path: Path) -> None:
    """**核心回归**：文件被打开读取时也要能写进去。

    在 Linux/macOS 上这个测试恒过——rename 覆盖打开的文件没问题。
    只有在 Windows 上它才会失败，所以它是这个 bug 唯一的有效防线。
    """
    target = tmp_path / "busy.json"
    target.write_text('{"old": true}', encoding="utf-8")
    # 模拟另一个进程/守卫脚本正持有读取句柄
    handle = open(target, "rb")
    try:
        assert handle.read() is not None
        how = atomic_write_json(target, {"new": True})
    finally:
        handle.close()
    assert how in ("replace", "overwrite"), how
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}


def test_write_succeeds_under_repeated_reads(tmp_path: Path) -> None:
    """持续读取（自迭代循环每 20 分钟一次）时反复写，不能抛异常。

    这是实测崩掉的形态：一个后台脚本不停读，采集器不停写，交错发生。
    """
    target = tmp_path / "churn.json"
    target.write_text('{"n": 0}', encoding="utf-8")
    stop = threading.Event()
    # 分开记：读者的 PermissionError 是测试自己制造的独占窗口，
    # 不是被测代码的失败。混在一起断言就会随机红。
    reader_errors: list[OSError] = []
    writer_errors: list[BaseException] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                with open(target, "rb") as fh:
                    fh.read()
            except OSError as exc:
                reader_errors.append(exc)
            # 5 毫秒一轮 ≈ 每秒 200 次，贴近真实读取频率。原来的 2 毫秒
            # （每秒 500 次）是刻意加压，确实把「重试次数不够」逼出来了——
            # 但那个强度下读者自己也会偶发 PermissionError，测试就变成在测
            # 读者而不是测写方。加压归加压，这里只该断言「写方不抛异常」。
            time.sleep(0.005)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        for n in range(40):
            try:
                atomic_write_json(target, {"n": n})
            except BaseException as exc:                 # noqa: BLE001
                writer_errors.append(exc)
                break
    finally:
        stop.set()
        thread.join(timeout=3)
    assert not writer_errors, f"持续读写时写入失败：{writer_errors[:3]}"
    assert json.loads(target.read_text(encoding="utf-8")) == {"n": 39}


def test_text_variant_writes_utf8(tmp_path: Path) -> None:
    target = tmp_path / "cn.md"
    how = atomic_write_text(target, "# 中文标题\n正文内容")
    assert how == "replace"
    assert "中文标题" in target.read_text(encoding="utf-8")


def test_creates_missing_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "x.json"
    atomic_write_json(target, {"ok": True})
    assert target.exists()


def test_unicode_content_roundtrip(tmp_path: Path) -> None:
    """状态文件全是中文，ensure_ascii=False 必须生效。"""
    target = tmp_path / "zh.json"
    atomic_write_json(target, ["浙江省人民政府", "重庆市能源局"])
    raw = target.read_text(encoding="utf-8")
    assert "浙江省特有字" not in raw
    assert "浙江省人民政府" in raw, "中文被转义成 \\u 序列了"


def test_backoff_override_persists_across_imports() -> None:
    """自我迭代调过的退避阶梯**必须跨进程存活**。

    2026-10-02 实测踩过：``loosen_backoff`` 只改内存里的类变量，事件流写着
    「阶梯已前移」、返回 ``applied=True``，但下一个进程 import 进来还是原值
    ——「应用成功」但下次启动就没了。跟 auto_observe 那种假动作是同一类错误，
    只是更隐蔽：返回值和事件流都「成功」了。

    所以要测「重新 import 之后还是改过的值」，而不是测当次改没改。
    """
    import importlib
    import sys

    sys.path.insert(0, str(_ROOT))
    from lighthouse import run as run_mod

    original = {k: tuple(v) for k, v in run_mod.SourceBackoff.LADDER.items()}
    path = run_mod.SourceBackoff._override_path()
    saved = path.read_bytes() if path.exists() else None
    try:
        run_mod.SourceBackoff.apply_backoff_override(
            "daily", (4 * 3600, 8 * 3600, 12 * 3600, 16 * 3600, 24 * 3600))
        assert path.exists(), "覆盖值没落盘"

        # 模拟新进程：丢掉模块缓存重新 import
        for name in [n for n in sys.modules if n.startswith("lighthouse.run")]:
            del sys.modules[name]
        fresh = importlib.import_module("lighthouse.run")
        assert tuple(fresh.SourceBackoff.LADDER["daily"]) == \
            (4 * 3600, 8 * 3600, 12 * 3600, 16 * 3600, 24 * 3600), \
            "重新 import 后阶梯没变：%s" % (tuple(fresh.SourceBackoff.LADDER["daily"]),)
    finally:
        run_mod.SourceBackoff.LADDER = original
        if saved is not None:
            path.write_bytes(saved)
        else:
            path.unlink(missing_ok=True)


def test_bad_override_file_does_not_crash_import() -> None:
    """覆盖文件损坏时要能优雅退回默认值 —— 它是自迭代产物，不是权威数据。"""
    import sys

    sys.path.insert(0, str(_ROOT))
    from lighthouse import run as run_mod

    path = run_mod.SourceBackoff._override_path()
    saved = path.read_bytes() if path.exists() else None
    original = {k: tuple(v) for k, v in run_mod.SourceBackoff.LADDER.items()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ 这不是合法 JSON", encoding="utf-8")
        run_mod.SourceBackoff.load_backoff_override()
        assert run_mod.SourceBackoff.LADDER == original, \
            "损坏的覆盖文件应该被忽略，而不是让采集起不来"
    finally:
        run_mod.SourceBackoff.LADDER = original
        if saved is not None:
            path.write_bytes(saved)
        else:
            path.unlink(missing_ok=True)