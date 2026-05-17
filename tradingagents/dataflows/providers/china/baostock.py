"""BaoStock stub — this data source has been removed from the project."""
import logging
logger = logging.getLogger(__name__)
logger.debug("BaoStock provider has been removed")

BAOSTOCK_AVAILABLE = False


class BaostockProvider:
    """Stub provider — always returns empty/unavailable."""
    def __init__(self):
        self.connected = False

    async def test_connection(self):
        return False


_baostock_provider = None


def get_baostock_provider():
    global _baostock_provider
    if _baostock_provider is None:
        _baostock_provider = BaostockProvider()
    return _baostock_provider
