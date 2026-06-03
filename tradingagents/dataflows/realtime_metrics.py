"""
实时估值指标计算模块
基于实时行情和财务数据计算PE/PB等指标
（从 MongoDB 迁移到 PostgreSQL）
"""
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


def calculate_realtime_pe_pb(
    symbol: str,
    db_client=None
) -> Optional[Dict[str, Any]]:
    """
    基于实时行情和 Tushare TTM 数据计算动态 PE/PB

    计算逻辑：
    1. 从 stock_basic_info 获取 Tushare 的 pe_ttm（基于昨日收盘价）
    2. 反推 TTM 净利润 = 总市值 / pe_ttm
    3. 使用实时股价计算实时市值
    4. 计算动态 PE_TTM = 实时市值 / TTM 净利润

    Args:
        symbol: 6位股票代码
        db_client: PG会话（可选，用于同步调用）

    Returns:
        {
            "pe": 22.5,              # 动态市盈率（基于 TTM）
            "pb": 3.2,               # 动态市净率
            "pe_ttm": 23.1,          # 动态市盈率（TTM）
            "price": 11.0,           # 当前价格
            "market_cap": 110.5,     # 实时市值（亿元）
            "ttm_net_profit": 4.8,   # TTM 净利润（亿元，从 Tushare 反推）
            "updated_at": "2025-10-14T10:30:00",
            "source": "realtime_calculated",
            "is_realtime": True
        }
        如果计算失败返回 None
    """
    from sqlalchemy import select, desc
    from app.core.pg_models import MarketQuotes, StockBasicInfo, StockFinancialData

    session = None

    try:
        if db_client is None:
            from app.core.database import sync_session_factory
            session = sync_session_factory()
        else:
            session = db_client

        code6 = str(symbol).zfill(6)

        logger.info(f"🔍 [实时PE计算] 开始计算股票 {code6}")

        # 1. 获取实时行情（market_quotes）
        stmt = select(MarketQuotes).where(MarketQuotes.code == code6)
        result = session.execute(stmt)
        quote = result.scalars().first()

        if not quote:
            logger.warning(f"⚠️ [实时PE计算-失败] 未找到股票 {code6} 的实时行情数据")
            return None

        realtime_price = quote.close
        pre_close = quote.pre_close
        quote_updated_at = quote.updated_at if hasattr(quote, 'updated_at') else "N/A"

        if not realtime_price or realtime_price <= 0:
            logger.warning(f"⚠️ [实时PE计算-失败] 股票 {code6} 的实时价格无效: {realtime_price}")
            return None

        logger.info(f"   ✓ 实时股价: {realtime_price}元 (更新时间: {quote_updated_at})")
        logger.info(f"   ✓ 昨日收盘价: {pre_close}元")

        # 2. 获取基础信息（stock_basic_info）- 优先查询 Tushare 数据源
        logger.info(f"🔍 [PG查询] 查询条件: code={code6}, source=tushare")
        stmt = select(StockBasicInfo).where(
            StockBasicInfo.code == code6,
            StockBasicInfo.source == "tushare"
        )
        result = session.execute(stmt)
        basic_info = result.scalars().first()

        if not basic_info:
            all_sources = session.execute(
                select(StockBasicInfo.source).where(StockBasicInfo.code == code6)
            ).scalars().all()
            logger.warning(f"⚠️ [动态PE计算] 未找到 Tushare 数据")
            logger.warning(f"   PG 中该股票的数据源: {all_sources}")

            basic_info = session.execute(
                select(StockBasicInfo).where(StockBasicInfo.code == code6)
            ).scalars().first()

            if not basic_info:
                logger.warning(f"⚠️ [动态PE计算-失败] 未找到股票 {code6} 的基础信息")
                return None
            else:
                logger.warning(f"⚠️ [动态PE计算] 使用其他数据源: {basic_info.source}")
                if basic_info.source != 'tushare':
                    logger.warning(f"⚠️ [动态PE计算-失败] 数据源 {basic_info.source} 不包含 pe_ttm 等字段")
                    return None

        pe_ttm_tushare = basic_info.pe_ttm
        pe_tushare = basic_info.pe
        pb_tushare = basic_info.pb
        total_mv_yi = basic_info.total_mv
        total_share = None
        basic_info_updated_at = basic_info.updated_at

        logger.info(f"   ✓ Tushare PE_TTM: {pe_ttm_tushare}倍")
        logger.info(f"   ✓ Tushare PE: {pe_tushare}倍")
        logger.info(f"   ✓ Tushare 总市值: {total_mv_yi}亿元")
        logger.info(f"   ✓ stock_basic_info 更新时间: {basic_info_updated_at}")

        # 3. 判断是否需要重新计算市值
        from datetime import datetime as dt, time as dtime
        from zoneinfo import ZoneInfo

        need_recalculate = True
        if basic_info_updated_at:
            if isinstance(basic_info_updated_at, dt):
                if basic_info_updated_at.tzinfo is None:
                    basic_info_updated_at = basic_info_updated_at.replace(tzinfo=ZoneInfo("Asia/Shanghai"))

                today = dt.now(ZoneInfo("Asia/Shanghai")).date()
                update_date = basic_info_updated_at.date()
                update_time = basic_info_updated_at.time()

                if update_date == today and update_time >= dtime(15, 0):
                    need_recalculate = False
                    logger.info(f"   💡 stock_basic_info 已在今天收盘后更新，直接使用其数据")

        if not need_recalculate:
            logger.info(f"   ✓ 使用 stock_basic_info 的最新数据（无需重新计算）")
            result = {
                "pe": round(pe_tushare, 2) if pe_tushare else None,
                "pb": round(pb_tushare, 2) if pb_tushare else None,
                "pe_ttm": round(pe_ttm_tushare, 2) if pe_ttm_tushare else None,
                "price": round(realtime_price, 2),
                "market_cap": round(total_mv_yi, 2) if total_mv_yi else None,
                "updated_at": quote.updated_at if hasattr(quote, 'updated_at') else None,
                "source": "stock_basic_info_latest",
                "is_realtime": False,
                "note": "使用stock_basic_info收盘后最新数据",
            }
            logger.info(f"✅ [动态PE计算-成功] 股票 {code6}: PE_TTM={result['pe_ttm']}倍, PB={result['pb']}倍 (来自stock_basic_info)")
            return result

        # 4. 计算总股本
        total_shares_wan = None
        yesterday_mv_yi = None

        if total_share and total_share > 0:
            total_shares_wan = total_share
            logger.info(f"   ✓ 使用 stock_basic_info.total_share: {total_shares_wan:.2f}万股")
            if pre_close and pre_close > 0:
                yesterday_mv_yi = (total_shares_wan * pre_close) / 10000
                logger.info(f"   ✓ 昨日市值: {total_shares_wan:.2f}万股 × {pre_close:.2f}元 / 10000 = {yesterday_mv_yi:.2f}亿元")
            elif total_mv_yi and total_mv_yi > 0:
                yesterday_mv_yi = total_mv_yi
                logger.info(f"   ⚠️ market_quotes 中无 pre_close，使用 stock_basic_info 市值作为昨日市值: {yesterday_mv_yi:.2f}亿元")
            else:
                logger.warning(f"⚠️ [动态PE计算-失败] 无法获取昨日市值: pre_close={pre_close}, total_mv={total_mv_yi}")
                return None

        elif pre_close and pre_close > 0 and total_mv_yi and total_mv_yi > 0:
            is_yesterday_data = True
            if basic_info_updated_at and isinstance(basic_info_updated_at, dt):
                if basic_info_updated_at.tzinfo is None:
                    basic_info_updated_at = basic_info_updated_at.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                today = dt.now(ZoneInfo("Asia/Shanghai")).date()
                update_date = basic_info_updated_at.date()
                update_time = basic_info_updated_at.time()
                if update_date == today and update_time >= dtime(15, 0):
                    is_yesterday_data = False

            if is_yesterday_data:
                total_shares_wan = (total_mv_yi * 10000) / pre_close
                yesterday_mv_yi = total_mv_yi
                logger.info(f"   ✓ stock_basic_info 是昨天的数据，用 pre_close 反推总股本: {total_mv_yi:.2f}亿元 / {pre_close:.2f}元 = {total_shares_wan:.2f}万股")
            else:
                total_shares_wan = (total_mv_yi * 10000) / realtime_price
                yesterday_mv_yi = (total_shares_wan * pre_close) / 10000
                logger.info(f"   ✓ stock_basic_info 是今天的数据，用 realtime_price 反推总股本")

        elif total_mv_yi and total_mv_yi > 0:
            total_shares_wan = (total_mv_yi * 10000) / realtime_price
            yesterday_mv_yi = total_mv_yi
            logger.warning(f"   ⚠️ market_quotes 中无 pre_close，假设 stock_basic_info.total_mv 是昨日市值")
            logger.info(f"   ✓ 用 realtime_price 反推总股本: {total_mv_yi:.2f}亿元 / {realtime_price:.2f}元 = {total_shares_wan:.2f}万股")
            logger.info(f"   ✓ 昨日市值（假设）: {yesterday_mv_yi:.2f}亿元")

        else:
            logger.warning(f"⚠️ [动态PE计算-失败] 无法获取总股本数据")
            return None

        # 5. 从 Tushare pe_ttm 反推 TTM 净利润
        if not pe_ttm_tushare or pe_ttm_tushare <= 0 or not yesterday_mv_yi or yesterday_mv_yi <= 0:
            logger.warning(f"⚠️ [动态PE计算-失败] 无法反推TTM净利润")
            return None

        ttm_net_profit_yi = yesterday_mv_yi / pe_ttm_tushare
        logger.info(f"   ✓ 反推 TTM净利润: {yesterday_mv_yi:.2f}亿元 / {pe_ttm_tushare:.2f}倍 = {ttm_net_profit_yi:.2f}亿元")

        # 6. 计算实时市值
        realtime_mv_yi = (realtime_price * total_shares_wan) / 10000
        logger.info(f"   ✓ 实时市值: {realtime_price:.2f}元 × {total_shares_wan:.2f}万股 / 10000 = {realtime_mv_yi:.2f}亿元")

        # 7. 计算动态 PE_TTM
        dynamic_pe_ttm = realtime_mv_yi / ttm_net_profit_yi
        logger.info(f"   ✓ 动态PE_TTM计算: {realtime_mv_yi:.2f}亿元 / {ttm_net_profit_yi:.2f}亿元 = {dynamic_pe_ttm:.2f}倍")

        # 8. 获取财务数据（用于计算 PB）
        stmt = (
            select(StockFinancialData)
            .where(StockFinancialData.code == code6)
            .order_by(desc(StockFinancialData.report_period))
            .limit(1)
        )
        result = session.execute(stmt)
        financial_data = result.scalars().first()

        pb = None
        total_equity_yi = None

        if financial_data:
            total_equity = financial_data.total_equity
            if total_equity and total_equity > 0:
                total_equity_yi = total_equity / 100000000
                pb = realtime_mv_yi / total_equity_yi
                logger.info(f"   ✓ 动态PB计算: {realtime_mv_yi:.2f}亿元 / {total_equity_yi:.2f}亿元 = {pb:.2f}倍")
            else:
                logger.warning(f"   ⚠️ PB计算失败: 净资产无效 ({total_equity})")
        else:
            logger.warning(f"   ⚠️ 未找到财务数据，无法计算PB")
            if pb_tushare:
                pb = pb_tushare
                logger.info(f"   ✓ 使用 Tushare PB: {pb}倍")

        result = {
            "pe": round(dynamic_pe_ttm, 2),
            "pb": round(pb, 2) if pb else None,
            "pe_ttm": round(dynamic_pe_ttm, 2),
            "price": round(realtime_price, 2),
            "market_cap": round(realtime_mv_yi, 2),
            "ttm_net_profit": round(ttm_net_profit_yi, 2),
            "updated_at": quote.updated_at if hasattr(quote, 'updated_at') else None,
            "source": "realtime_calculated_from_market_quotes",
            "is_realtime": True,
            "note": "基于market_quotes实时股价和pre_close计算",
            "total_shares": round(total_shares_wan, 2),
            "yesterday_close": round(pre_close, 2) if pre_close else None,
            "tushare_pe_ttm": round(pe_ttm_tushare, 2),
            "tushare_pe": round(pe_tushare, 2) if pe_tushare else None,
        }

        logger.info(f"✅ [动态PE计算-成功] 股票 {code6}: 动态PE_TTM={result['pe_ttm']}倍, PB={result['pb']}倍")
        return result

    except Exception as e:
        logger.error(f"计算股票 {symbol} 的实时PE/PB失败: {e}", exc_info=True)
        return None
    finally:
        if session and db_client is None:
            session.close()


def validate_pe_pb(pe: Optional[float], pb: Optional[float]) -> bool:
    """验证PE/PB是否在合理范围内"""
    if pe is not None and (pe < -100 or pe > 1000):
        logger.warning(f"PE异常: {pe}")
        return False

    if pb is not None and (pb < 0.1 or pb > 100):
        logger.warning(f"PB异常: {pb}")
        return False

    return True


def get_pe_pb_with_fallback(
    symbol: str,
    db_client=None
) -> Dict[str, Any]:
    """
    获取PE/PB，智能降级策略

    策略：
    1. 优先使用动态 PE（基于实时股价 + Tushare TTM 净利润）
    2. 如果动态计算失败，降级到 Tushare 静态 PE（基于昨日收盘价）

    Args:
        symbol: 6位股票代码
        db_client: PG会话（可选）

    Returns:
        {
            "pe": 22.5,
            "pb": 3.2,
            "pe_ttm": 23.1,
            "pb_mrq": 3.3,
            "source": "realtime_calculated_from_tushare_ttm" | "daily_basic",
            "is_realtime": True | False,
            "updated_at": "2025-10-14T10:30:00",
            "ttm_net_profit": 4.8
        }
    """
    from sqlalchemy import select
    from app.core.pg_models import StockBasicInfo

    logger.info(f"🔄 [PE智能策略] 开始获取股票 {symbol} 的PE/PB")

    session = None
    try:
        if db_client is None:
            from app.core.database import sync_session_factory
            session = sync_session_factory()
        else:
            session = db_client

        # 1. 优先使用动态 PE 计算
        logger.info("   → 尝试方案1: 动态PE计算 (实时股价 + Tushare TTM净利润)")
        realtime_metrics = calculate_realtime_pe_pb(symbol, session)
        if realtime_metrics:
            pe = realtime_metrics.get('pe')
            pb = realtime_metrics.get('pb')
            if validate_pe_pb(pe, pb):
                logger.info(f"✅ [PE智能策略-成功] 使用动态PE: PE={pe}, PB={pb}")
                return realtime_metrics
            else:
                logger.warning(f"⚠️ [PE智能策略-方案1异常] 动态PE/PB超出合理范围 (PE={pe}, PB={pb})")

        # 2. 降级到 Tushare 静态 PE
        logger.info("   → 尝试方案2: Tushare静态PE (基于昨日收盘价)")

        code6 = str(symbol).zfill(6)
        stmt = select(StockBasicInfo).where(
            StockBasicInfo.code == code6,
            StockBasicInfo.source == "tushare"
        )
        result = session.execute(stmt)
        basic_info = result.scalars().first()

        if not basic_info:
            stmt = select(StockBasicInfo).where(StockBasicInfo.code == code6)
            result = session.execute(stmt)
            basic_info = result.scalars().first()

        if basic_info:
            pe_static = basic_info.pe
            pb_static = basic_info.pb
            pe_ttm = basic_info.pe_ttm
            pb_mrq = basic_info.pb_mrq
            updated_at = basic_info.updated_at

            if pe_ttm or pe_static or pb_static:
                logger.info(f"✅ [PE智能策略-成功] 使用Tushare静态PE: PE={pe_static}, PE_TTM={pe_ttm}, PB={pb_static}")
                return {
                    "pe": pe_static,
                    "pb": pb_static,
                    "pe_ttm": pe_ttm,
                    "pb_mrq": pb_mrq,
                    "source": "daily_basic",
                    "is_realtime": False,
                    "updated_at": updated_at,
                    "note": "使用Tushare最近一个交易日的数据（基于TTM）"
                }

        logger.warning("⚠️ [PE智能策略-方案2失败] Tushare静态数据不可用")

    except Exception as e:
        logger.warning(f"⚠️ [PE智能策略-方案2异常] {e}")

    logger.error(f"❌ [PE智能策略-全部失败] 无法获取股票 {symbol} 的PE/PB")
    return {}
