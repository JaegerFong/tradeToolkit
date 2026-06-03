"""
Data sources subpackage.
Expose adapters and manager for backward-compatible imports.
"""
from .base import DataSourceAdapter
from .akshare_adapter import AKShareAdapter
from .tdx_adapter import TDXAdapter
from .manager import DataSourceManager

