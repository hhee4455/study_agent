"""Core 인프라.

모든 에이전트(`agents/*`, `lead/*`)가 의존하는 공통 기반 모듈.
"""

from . import schemas

try:
    from .auto_merge import MergeResult, try_auto_merge
    from .budget import (
        BudgetExceeded,
        BudgetLimits,
        BudgetManager,
        estimate_eta,
        format_status_line,
        get_recent_rate,
        get_totals,
        record_usage,
        set_state_dir,
    )
except ImportError:  # 격리 ws 에서는 일부 sibling 모듈이 빠져있을 수 있음
    pass

__all__ = [
    "BudgetExceeded",
    "BudgetLimits",
    "BudgetManager",
    "MergeResult",
    "estimate_eta",
    "format_status_line",
    "get_recent_rate",
    "get_totals",
    "record_usage",
    "schemas",
    "set_state_dir",
    "try_auto_merge",
]
