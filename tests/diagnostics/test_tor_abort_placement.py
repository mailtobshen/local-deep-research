"""Tor 电路中止必须位于预检 try/except 之外（源码结构断言）。

第一次实现（13:30 运行）把 ``raise ValueError`` 放在了预检的
``try`` 块内 —— 而 ``except Exception as preflight_err`` 的契约
是"探测框架异常绝不中止研究"，于是 ValueError 被吞、任务继续
跑了 3 分钟才被 no_results 哨兵中止。本测试钉住结构：中止
逻辑必须在 except 块结束之后、``system = AdvancedSearchSystem``
之前的位置独立存在，不能再放回 try 内。
"""

import inspect

import local_deep_research.web.services.research_service as rs


def test_abort_raise_lives_outside_preflight_try():
    src = inspect.getsource(rs.run_research_process)
    # Locate anchors in order.
    try_pos = src.index("except Exception as preflight_err")
    abort_pos = src.index("abort reason=tor_circuit_unavailable")
    system_pos = src.index("system = AdvancedSearchSystem(")
    # The abort marker must come AFTER the preflight except block and
    # BEFORE the search system spins up.
    assert try_pos < abort_pos < system_pos, (
        "tor-circuit abort must sit outside the preflight try/except "
        f"(except@{try_pos}, abort@{abort_pos}, system@{system_pos})"
    )


def test_abort_raises_valueerror_not_silent():
    """The abort must be a raise (which the outer except Exception
    converts into a failed-status error report), not a log-and-continue."""
    src = inspect.getsource(rs.run_research_process)
    abort_block = src[src.index("Hard abort on a dead Tor circuit") : src.index("system = AdvancedSearchSystem(")]
    assert "raise ValueError" in abort_block
    # and it must NOT be wrapped by the preflight except anymore
    # (count statement lines only — the block comment mentions
    # "try/except" in prose)
    import re

    except_lines = [
        l for l in abort_block.splitlines()
        if re.match(r"^\s*except\b", l)
    ]
    assert len(except_lines) == 1 and "NameError" in except_lines[0], (
        f"only the NameError guard may wrap the abort, got {except_lines}"
    )
