"""
股票详情相关API
- 统一响应包: {success, data, message, timestamp}
- 所有端点均需鉴权 (Bearer Token)
- 路径前缀在 main.py 中挂载为 /api，当前路由自身前缀为 /stocks
"""
from typing import Optional, Dict, Any, List, Tuple
from fastapi import APIRouter, Depends, HTTPException, status, Query
import logging
import re

from app.routers.auth_db import get_current_user
from app.core.database import async_session_factory
from app.core.response import ok
from app.core.pg_models import StockBasicInfo, MarketQuotes, StockFinancialData, DailyData
from sqlalchemy import select, or_

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stocks", tags=["stocks"])


def _zfill_code(code: str) -> str:
    try:
        s = str(code).strip()
        if len(s) == 6 and s.isdigit():
            return s
        return s.zfill(6)
    except Exception:
        return str(code)


def _detect_market_and_code(code: str) -> Tuple[str, str]:
    """
    检测股票代码的市场类型并标准化代码

    Args:
        code: 股票代码

    Returns:
        (market, normalized_code): 市场类型和标准化后的代码
            - CN: A股（6位数字）
            - HK: 港股（4-5位数字或带.HK后缀）
            - US: 美股（字母代码）
    """
    code = code.strip().upper()

    # 港股：带.HK后缀
    if code.endswith('.HK'):
        return ('HK', code[:-3].zfill(5))  # 移除.HK，补齐到5位

    # 美股：纯字母
    if re.match(r'^[A-Z]+$', code):
        return ('US', code)

    # 港股：4-5位数字
    if re.match(r'^\d{4,5}$', code):
        return ('HK', code.zfill(5))  # 补齐到5位

    # A股：6位数字
    if re.match(r'^\d{6}$', code):
        return ('CN', code)

    # 默认当作A股处理
    return ('CN', _zfill_code(code))


def _model_to_dict(model_instance):
    """将 SQLAlchemy 模型实例转换为字典，排除内部属性"""
    if model_instance is None:
        return {}
    return {c.name: getattr(model_instance, c.name)
            for c in model_instance.__table__.columns}


@router.get("/{code}/quote", response_model=dict)
async def get_quote(
    code: str,
    force_refresh: bool = Query(False, description="是否强制刷新（跳过缓存）"),
    current_user: dict = Depends(get_current_user)
):
    """
    获取股票实时行情（支持A股/港股/美股）

    自动识别市场类型：
    - 6位数字 → A股
    - 4位数字或.HK → 港股
    - 纯字母 → 美股

    参数：
    - code: 股票代码
    - force_refresh: 是否强制刷新（跳过缓存）

    返回字段（data内，蛇形命名）:
      - code, name, market
      - price(close), change_percent(pct_chg), amount, prev_close(估算)
      - turnover_rate, amplitude（振幅，替代量比）
      - trade_date, updated_at
    """
    # 检测市场类型
    market, normalized_code = _detect_market_and_code(code)

    # 港股和美股：使用新服务（暂时保持 MongoDB 依赖，ForeignStockService 内部使用 db）
    if market in ['HK', 'US']:
        from app.services.foreign_stock_service import ForeignStockService
        from app.core.database import async_session_factory
        service = ForeignStockService(db=async_session_factory)

        try:
            quote = await service.get_quote(market, normalized_code, force_refresh)
            return ok(data=quote)
        except Exception as e:
            logger.error(f"获取{market}股票{code}行情失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"获取行情失败: {str(e)}"
            )

    # A股：使用 PostgreSQL
    code6 = normalized_code

    async with async_session_factory() as session:
        # 行情
        q_result = await session.execute(
            select(MarketQuotes).where(MarketQuotes.code == code6)
        )
        q = q_result.scalar_one_or_none()

        # 调试日志
        logger.info(f"查询 market_quotes: code={code6}")
        if q:
            logger.info(f" 找到数据: volume={q.volume}, amount={q.amount}")
        else:
            logger.info(f" 未找到数据")

        # 基础信息 - 按数据源优先级查询
        from app.core.unified_config import UnifiedConfigManager
        config = UnifiedConfigManager()
        data_source_configs = await config.get_data_source_configs_async()

        # 提取启用的数据源，按优先级排序
        enabled_sources = [
            ds.type.lower() for ds in data_source_configs
            if ds.enabled and ds.type.lower() in ['akshare', 'baostock']
        ]

        if not enabled_sources:
            enabled_sources = ['akshare', 'baostock']

        # 按优先级查询基础信息
        b = None
        for src in enabled_sources:
            b_result = await session.execute(
                select(StockBasicInfo).where(
                    StockBasicInfo.code == code6,
                    StockBasicInfo.source == src
                )
            )
            b = b_result.scalar_one_or_none()
            if b:
                break

        # 如果所有数据源都没有，尝试不带 source 条件查询（兼容旧数据）
        if not b:
            b_result = await session.execute(
                select(StockBasicInfo).where(StockBasicInfo.code == code6)
            )
            b = b_result.scalar_one_or_none()

    if not q and not b:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该股票的任何信息")

    close = q.close if q else None
    pct = q.pct_chg if q else None
    pre_close_saved = q.pre_close if q else None
    prev_close = pre_close_saved
    if prev_close is None:
        try:
            if close is not None and pct is not None:
                prev_close = round(float(close) / (1.0 + float(pct) / 100.0), 4)
        except Exception:
            prev_close = None

    # 优先从 market_quotes 获取 turnover_rate（实时数据）
    turnover_rate = q.turnover_rate if q and hasattr(q, 'turnover_rate') else None
    if q and q.extra:
        turnover_rate = q.extra.get('turnover_rate') if turnover_rate is None else turnover_rate

    turnover_rate_date = None
    if turnover_rate is None and b:
        turnover_rate = b.turnover_rate
        turnover_rate_date = b.updated_at
    elif q:
        turnover_rate_date = q.trade_date

    # 计算振幅（amplitude）替代量比（volume_ratio）
    amplitude = None
    amplitude_date = None
    try:
        high = q.high if q else None
        low = q.low if q else None
        logger.info(f"计算振幅: high={high}, low={low}, prev_close={prev_close}")
        if high is not None and low is not None and prev_close is not None and prev_close > 0:
            amplitude = round((float(high) - float(low)) / float(prev_close) * 100, 2)
            amplitude_date = q.trade_date if q else None
            logger.info(f" 振幅计算成功: {amplitude}%")
        else:
            logger.warning(f" 数据不完整，无法计算振幅")
    except Exception as e:
        logger.warning(f" 计算振幅失败: {e}")
        amplitude = None

    data = {
        "code": code6,
        "name": b.name if b else None,
        "market": b.market if b else None,
        "price": close,
        "change_percent": pct,
        "amount": q.amount if q else None,
        "volume": q.volume if q else None,
        "open": q.open if q else None,
        "high": q.high if q else None,
        "low": q.low if q else None,
        "prev_close": prev_close,
        "turnover_rate": turnover_rate,
        "amplitude": amplitude,
        "turnover_rate_date": turnover_rate_date,
        "amplitude_date": amplitude_date,
        "trade_date": q.trade_date if q else None,
        "updated_at": q.updated_at.isoformat() if q and q.updated_at else None,
    }

    return ok(data)


@router.get("/{code}/fundamentals", response_model=dict)
async def get_fundamentals(
    code: str,
    source: Optional[str] = Query(None, description="数据源 (akshare/baostock/multi_source)"),
    force_refresh: bool = Query(False, description="是否强制刷新（跳过缓存）"),
    current_user: dict = Depends(get_current_user)
):
    """
    获取基础面快照（支持A股/港股/美股）

    数据来源优先级：
    1. stock_basic_info 集合（基础信息、估值指标）
    2. stock_financial_data 集合（财务指标：ROE、负债率等）

    参数：
    - code: 股票代码
    - source: 数据源（可选），默认按优先级：akshare > multi_source > baostock
    - force_refresh: 是否强制刷新（跳过缓存）
    """
    # 检测市场类型
    market, normalized_code = _detect_market_and_code(code)

    # 港股和美股：使用新服务
    if market in ['HK', 'US']:
        from app.services.foreign_stock_service import ForeignStockService
        from app.core.database import async_session_factory
        service = ForeignStockService(db=async_session_factory)

        try:
            info = await service.get_basic_info(market, normalized_code, force_refresh)
            return ok(data=info)
        except Exception as e:
            logger.error(f"获取{market}股票{code}基础信息失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"获取基础信息失败: {str(e)}"
            )

    # A股：使用 PostgreSQL
    code6 = normalized_code

    async with async_session_factory() as session:
        # 1. 获取基础信息（支持数据源筛选）
        if source:
            b_result = await session.execute(
                select(StockBasicInfo).where(
                    StockBasicInfo.code == code6,
                    StockBasicInfo.source == source
                )
            )
            b = b_result.scalar_one_or_none()
            if not b:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"未找到该股票在数据源 {source} 中的基础信息"
                )
        else:
            source_priority = ["tushare", "multi_source", "akshare", "baostock"]
            b = None

            for src in source_priority:
                b_result = await session.execute(
                    select(StockBasicInfo).where(
                        StockBasicInfo.code == code6,
                        StockBasicInfo.source == src
                    )
                )
                b = b_result.scalar_one_or_none()
                if b:
                    logger.info(f"使用数据源: {src} 查询股票 {code6}")
                    break

            # 如果所有数据源都没有，尝试不带 source 条件查询（兼容旧数据）
            if not b:
                b_result = await session.execute(
                    select(StockBasicInfo).where(StockBasicInfo.code == code6)
                )
                b = b_result.scalar_one_or_none()
                if b:
                    logger.warning(f"使用旧数据（无 source 字段）: {code6}")

            if not b:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该股票的基础信息")

        # 2. 尝试从 stock_financial_data 获取最新财务指标
        financial_data = None
        try:
            from app.core.unified_config import UnifiedConfigManager
            config = UnifiedConfigManager()
            data_source_configs = await config.get_data_source_configs_async()

            enabled_sources = [
                ds.type.lower() for ds in data_source_configs
                if ds.enabled and ds.type.lower() in ['akshare', 'baostock']
            ]

            if not enabled_sources:
                enabled_sources = ['akshare', 'baostock']

            for data_source in enabled_sources:
                fd_result = await session.execute(
                    select(StockFinancialData).where(
                        or_(
                            StockFinancialData.symbol == code6,
                            StockFinancialData.code == code6
                        ),
                        StockFinancialData.data_source == data_source
                    ).order_by(StockFinancialData.report_period.desc()).limit(1)
                )
                financial_data = fd_result.scalar_one_or_none()
                if financial_data:
                    logger.info(f"使用数据源 {data_source} 的财务数据 (报告期: {financial_data.report_period})")
                    break

            if not financial_data:
                logger.warning(f"未找到 {code6} 的财务数据")
        except Exception as e:
            logger.error(f"获取财务数据失败: {e}")

    # 3. 获取实时PE/PB（优先使用实时计算）
    # NOTE: get_pe_pb_with_fallback 还需要 MongoDB client, 暂时降级为空
    from tradingagents.dataflows.realtime_metrics import get_pe_pb_with_fallback
    import asyncio

    # 在线程池中执行同步的实时计算
    # NOTE: realtime metrics 通过 PG 异步查询
    realtime_metrics = {}
    try:
        realtime_metrics = await asyncio.to_thread(
            get_pe_pb_with_fallback,
            code6,
            None  # PG 版本不需要 MongoDB client
        )
    except Exception as e:
        logger.warning(f"获取实时指标失败: {e}")

    # 4. 构建返回数据
    realtime_market_cap = realtime_metrics.get("market_cap")
    total_mv = realtime_market_cap if realtime_market_cap else (b.total_mv if b else None)

    data = {
        "code": code6,
        "name": b.name if b else None,
        "industry": b.industry if b else None,
        "market": b.market if b else None,
        "sector": b.market if b else None,

        "pe": realtime_metrics.get("pe") or (b.pe if b else None),
        "pb": realtime_metrics.get("pb") or (b.pb if b else None),
        "pe_ttm": realtime_metrics.get("pe_ttm") or (b.pe_ttm if b else None),
        "pb_mrq": realtime_metrics.get("pb_mrq") or (b.pb_mrq if b else None),

        "ps": None,
        "ps_ttm": None,

        "pe_source": realtime_metrics.get("source", "unknown"),
        "pe_is_realtime": realtime_metrics.get("is_realtime", False),
        "pe_updated_at": realtime_metrics.get("updated_at"),

        "roe": None,
        "debt_ratio": None,

        "total_mv": total_mv,
        "circ_mv": b.circ_mv if b else None,

        "mv_is_realtime": bool(realtime_market_cap),

        "turnover_rate": b.turnover_rate if b else None,
        "volume_ratio": b.volume_ratio if b else None,

        "updated_at": b.updated_at.isoformat() if b and b.updated_at else None,
    }

    # 5. 从财务数据中提取 ROE、负债率和计算 PS
    if financial_data:
        # ROE
        data["roe"] = financial_data.roe

        # 负债率 (从 extra JSONB)
        if financial_data.extra:
            data["debt_ratio"] = financial_data.extra.get("debt_to_assets")
            # 如果 extra 中没有, 尝试直接用属性
        if data["roe"] is None and financial_data.extra:
            data["roe"] = financial_data.extra.get("roe")

        # 动态计算 PS（市销率）
        revenue_ttm = financial_data.extra.get("revenue_ttm") if financial_data.extra else None
        revenue = financial_data.revenue
        revenue_for_ps = revenue_ttm if revenue_ttm and revenue_ttm > 0 else revenue

        if revenue_for_ps and revenue_for_ps > 0:
            if total_mv and total_mv > 0:
                revenue_yi = revenue_for_ps / 100000000
                ps_calculated = total_mv / revenue_yi
                data["ps"] = round(ps_calculated, 2)
                data["ps_ttm"] = round(ps_calculated, 2) if revenue_ttm else None

    # 6. 如果财务数据中没有 ROE，使用 stock_basic_info 中的
    if data["roe"] is None and b:
        data["roe"] = b.roe

    return ok(data)


@router.get("/{code}/kline", response_model=dict)
async def get_kline(
    code: str,
    period: str = "day",
    limit: int = 120,
    adj: str = "none",
    force_refresh: bool = Query(False, description="是否强制刷新（跳过缓存）"),
    current_user: dict = Depends(get_current_user)
):
    """
    获取K线数据（支持A股/港股/美股）

    period: day/week/month/5m/15m/30m/60m
    adj: none/qfq/hfq
    force_refresh: 是否强制刷新（跳过缓存）
    """
    import logging
    from datetime import datetime, timedelta, time as dtime
    from zoneinfo import ZoneInfo
    logger = logging.getLogger(__name__)

    valid_periods = {"day","week","month","5m","15m","30m","60m"}
    if period not in valid_periods:
        raise HTTPException(status_code=400, detail=f"不支持的period: {period}")

    # 检测市场类型
    market, normalized_code = _detect_market_and_code(code)

    # 港股和美股：使用新服务
    if market in ['HK', 'US']:
        from app.services.foreign_stock_service import ForeignStockService
        from app.core.database import async_session_factory
        service = ForeignStockService(db=async_session_factory)

        try:
            kline_data = await service.get_kline(market, normalized_code, period, limit, force_refresh)
            return ok(data={
                'code': normalized_code,
                'period': period,
                'items': kline_data,
                'source': 'cache_or_api'
            })
        except Exception as e:
            logger.error(f"获取{market}股票{code}K线数据失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"获取K线数据失败: {str(e)}"
            )

    # A股：使用现有逻辑
    code_padded = normalized_code
    adj_norm = None if adj in (None, "none", "", "null") else adj
    items = None
    source = None

    # 周期映射：前端 -> MongoDB
    period_map = {
        "day": "daily",
        "week": "weekly",
        "month": "monthly",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "60m": "60min"
    }
    mongodb_period = period_map.get(period, "daily")

    # 获取当前时间（北京时间）
    from app.core.config import settings
    tz = ZoneInfo(settings.TIMEZONE)
    now = datetime.now(tz)
    today_str_yyyymmdd = now.strftime("%Y%m%d")
    today_str_formatted = now.strftime("%Y-%m-%d")

    # 1. 优先从 MongoDB 缓存获取 (K线数据仍使用 mongodb_cache_adapter)
    try:
        from tradingagents.dataflows.cache.mongodb_cache_adapter import get_mongodb_cache_adapter
        adapter = get_mongodb_cache_adapter()

        end_date = now.strftime("%Y-%m-%d")
        start_date = (now - timedelta(days=limit * 2)).strftime("%Y-%m-%d")

        logger.info(f"尝试从 MongoDB 获取 K 线数据: {code_padded}, period={period} (MongoDB: {mongodb_period}), limit={limit}")
        df = adapter.get_historical_data(code_padded, start_date, end_date, period=mongodb_period)

        if df is not None and not df.empty:
            items = []
            for _, row in df.tail(limit).iterrows():
                items.append({
                    "time": row.get("trade_date", row.get("date", "")),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": float(row.get("volume", row.get("vol", 0))),
                    "amount": float(row.get("amount", 0)) if "amount" in row else None,
                })
            source = "mongodb"
            logger.info(f"从 MongoDB 获取到 {len(items)} 条 K 线数据")
    except Exception as e:
        logger.warning(f"MongoDB 获取 K 线失败: {e}")

    # 2. 如果 MongoDB 没有数据，降级到外部 API
    if not items:
        logger.info(f"MongoDB 无数据，降级到外部 API")
        try:
            import asyncio
            from app.services.data_sources.manager import DataSourceManager

            mgr = DataSourceManager()
            items, source = await asyncio.wait_for(
                asyncio.to_thread(mgr.get_kline_with_fallback, code_padded, period, limit, adj_norm),
                timeout=10.0
            )
        except asyncio.TimeoutError:
            logger.error(f"外部 API 获取 K 线超时（10秒）")
            raise HTTPException(status_code=504, detail="获取K线数据超时，请稍后重试")
        except Exception as e:
            logger.error(f"外部 API 获取 K 线失败: {e}")
            raise HTTPException(status_code=500, detail=f"获取K线数据失败: {str(e)}")

    # 3. 检查是否需要添加当天实时数据（仅针对日线）
    if period == "day" and items:
        try:
            has_today_data = any(
                item.get("time") in [today_str_yyyymmdd, today_str_formatted]
                for item in items
            )

            current_time = now.time()
            is_weekday = now.weekday() < 5

            is_trading_time = (
                is_weekday and (
                    (dtime(9, 30) <= current_time <= dtime(11, 30)) or
                    (dtime(13, 0) <= current_time <= dtime(15, 30))
                )
            )

            should_fetch_realtime = is_trading_time

            if should_fetch_realtime:
                logger.info(f"尝试从 market_quotes 获取当天实时数据: {code_padded}")

                async with async_session_factory() as session:
                    q_result = await session.execute(
                        select(MarketQuotes).where(MarketQuotes.code == code_padded)
                    )
                    realtime_quote = q_result.scalar_one_or_none()

                if realtime_quote:
                    today_kline = {
                        "time": today_str_formatted,
                        "open": float(realtime_quote.open or 0),
                        "high": float(realtime_quote.high or 0),
                        "low": float(realtime_quote.low or 0),
                        "close": float(realtime_quote.close or 0),
                        "volume": float(realtime_quote.volume or 0),
                        "amount": float(realtime_quote.amount or 0),
                    }

                    if has_today_data:
                        items[-1] = today_kline
                        logger.info(f"替换当天K线数据: {code_padded}")
                    else:
                        items.append(today_kline)
                        logger.info(f"追加当天K线数据: {code_padded}")

                    source = f"{source}+market_quotes"
                else:
                    logger.warning(f"market_quotes 中未找到当天数据: {code_padded}")
        except Exception as e:
            logger.warning(f"获取当天实时数据失败（忽略）: {e}")

    data = {
        "code": code_padded,
        "period": period,
        "limit": limit,
        "adj": adj if adj else "none",
        "source": source,
        "items": items or []
    }
    return ok(data)


@router.get("/{code}/news", response_model=dict)
async def get_news(code: str, days: int = 30, limit: int = 50, include_announcements: bool = True, current_user: dict = Depends(get_current_user)):
    """获取新闻与公告（支持A股、港股、美股）"""
    from app.services.foreign_stock_service import ForeignStockService

    # 检测股票类型
    market, normalized_code = _detect_market_and_code(code)

    if market == 'US':
        service = ForeignStockService()
        result = await service.get_us_news(normalized_code, days=days, limit=limit)
        return ok(result)
    elif market == 'HK':
        data = {
            "code": normalized_code,
            "days": days,
            "limit": limit,
            "source": "none",
            "items": []
        }
        return ok(data)
    else:
        # A股：使用新闻数据服务
        try:
            logger.info(f"=" * 80)
            logger.info(f"开始获取新闻: code={code}, normalized_code={normalized_code}, days={days}, limit={limit}")

            from app.services.news_data_service import get_news_data_service, NewsQueryParams
            from datetime import datetime, timedelta
            from app.worker.akshare_sync_service import get_akshare_sync_service

            service = await get_news_data_service()
            sync_service = await get_akshare_sync_service()

            params = NewsQueryParams(
                symbol=normalized_code,
                limit=limit,
                sort_by="publish_time",
                sort_order=-1
            )

            logger.info(f"查询参数: symbol={params.symbol}, limit={params.limit}")

            # 1. 先从数据库查询
            logger.info(f"步骤1: 从数据库查询新闻...")
            news_list = await service.query_news(params)
            logger.info(f"数据库查询结果: 返回 {len(news_list)} 条新闻")

            data_source = "database"

            # 2. 如果数据库没有数据，调用同步服务
            if not news_list:
                logger.info(f"数据库无新闻数据，调用同步服务获取: {normalized_code}")
                try:
                    logger.info(f"步骤2: 调用同步服务...")
                    await sync_service.sync_news_data(
                        symbols=[normalized_code],
                        max_news_per_stock=limit,
                        force_update=False,
                        favorites_only=False
                    )

                    logger.info(f"步骤3: 重新从数据库查询...")
                    news_list = await service.query_news(params)
                    logger.info(f"重新查询结果: 返回 {len(news_list)} 条新闻")
                    data_source = "realtime"

                except Exception as e:
                    logger.error(f"同步服务异常: {e}", exc_info=True)

            # 转换为旧格式（兼容前端）
            logger.info(f"步骤4: 转换数据格式...")
            items = []
            for news in news_list:
                publish_time = news.get("publish_time", "")
                if isinstance(publish_time, datetime):
                    publish_time = publish_time.isoformat()

                items.append({
                    "title": news.get("title", ""),
                    "source": news.get("source", ""),
                    "time": publish_time,
                    "url": news.get("url", ""),
                    "type": "news",
                    "content": news.get("content", ""),
                    "summary": news.get("summary", "")
                })

            logger.info(f"转换完成: {len(items)} 条新闻")

            data = {
                "code": normalized_code,
                "days": days,
                "limit": limit,
                "include_announcements": include_announcements,
                "source": data_source,
                "items": items
            }

            logger.info(f"最终返回: source={data_source}, items_count={len(items)}")
            logger.info(f"=" * 80)
            return ok(data)

        except Exception as e:
            logger.error(f"获取新闻失败: {e}", exc_info=True)
            data = {
                "code": normalized_code,
                "days": days,
                "limit": limit,
                "include_announcements": include_announcements,
                "source": None,
                "items": []
            }
            return ok(data)
