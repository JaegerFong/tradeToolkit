"""
配置桥接模块
将统一配置系统的配置桥接到环境变量，供 TradingAgents 核心库使用
PostgreSQL 版本
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("app.config_bridge")


def bridge_config_to_env():
    """
    将统一配置桥接到环境变量

    这个函数会：
    1. 从数据库读取大模型厂家配置（API 密钥、超时、温度等）
    2. 将配置写入环境变量
    3. 将默认模型写入环境变量
    4. 将数据源配置写入环境变量（API 密钥、超时、重试等）
    5. 将系统运行时配置写入环境变量

    这样 TradingAgents 核心库就能通过环境变量读取到用户配置的数据
    """
    try:
        from app.core.unified_config import unified_config
        from app.services.config_service import config_service

        logger.info("🔧 开始桥接配置到环境变量...")
        bridged_count = 0

        # PG 连接字符串（供 tradingagents 包使用）
        from app.core.config import settings
        pg_uri = settings.PG_URI
        os.environ["TRADINGAGENTS_PG_URI"] = pg_uri
        os.environ["TRADINGAGENTS_PG_DATABASE"] = settings.PG_DATABASE
        logger.info(f"  ✓ 桥接 PG 连接配置")
        bridged_count += 2

        # 1. 桥接大模型配置（基础 API 密钥）
        try:
            from sqlalchemy import select
            from app.core.database import sync_session_factory
            from app.core.pg_models import LLMProvider as LLMProviderModel

            session = sync_session_factory()
            try:
                result = session.execute(select(LLMProviderModel))
                providers = result.scalars().all()
                logger.info(f"  📊 从数据库读取到 {len(providers)} 个厂家配置")

                for provider in providers:
                    if not provider.is_active:
                        logger.debug(f"  ⏭️  厂家 {provider.name} 未启用，跳过")
                        continue

                    env_key = f"{provider.name.upper()}_API_KEY"
                    existing_env_value = os.getenv(env_key)

                    if existing_env_value and not existing_env_value.startswith("your_"):
                        logger.info(f"  ✓ 使用 .env 文件中的 {env_key} (长度: {len(existing_env_value)})")
                        bridged_count += 1
                    elif provider.api_key and not provider.api_key.startswith("your_"):
                        os.environ[env_key] = provider.api_key
                        logger.info(f"  ✓ 使用数据库厂家配置的 {env_key} (长度: {len(provider.api_key)})")
                        bridged_count += 1
                    else:
                        logger.debug(f"  ⏭️  {env_key} 未配置有效的 API Key")
            finally:
                session.close()

        except Exception as e:
            logger.error(f"❌ 从数据库读取厂家配置失败: {e}", exc_info=True)
            logger.warning("⚠️  将尝试从 JSON 文件读取配置作为后备方案")
            # 后备方案：从 JSON 文件读取
            llm_configs = unified_config.get_llm_configs()
            for llm_config in llm_configs:
                env_key = f"{llm_config.provider.upper()}_API_KEY"
                existing_env_value = os.getenv(env_key)
                if existing_env_value and not existing_env_value.startswith("your_"):
                    logger.info(f"  ✓ 使用 .env 文件中的 {env_key} (长度: {len(existing_env_value)})")
                    bridged_count += 1
                elif llm_config.enabled and llm_config.api_key:
                    if not llm_config.api_key.startswith("your_"):
                        os.environ[env_key] = llm_config.api_key
                        logger.info(f"  ✓ 使用 JSON 文件中的 {env_key} (长度: {len(llm_config.api_key)})")
                        bridged_count += 1
                    else:
                        logger.warning(f"  ⚠️  {env_key} 在 .env 和 JSON 文件中都是占位符，跳过")
                else:
                    logger.debug(f"  ⏭️  {env_key} 未配置")

        # 2. 桥接默认模型配置
        default_model = unified_config.get_default_model()
        if default_model:
            os.environ['TRADINGAGENTS_DEFAULT_MODEL'] = default_model
            logger.info(f"  ✓ 桥接默认模型: {default_model}")
            bridged_count += 1

        quick_model = unified_config.get_quick_analysis_model()
        if quick_model:
            os.environ['TRADINGAGENTS_QUICK_MODEL'] = quick_model
            logger.info(f"  ✓ 桥接快速分析模型: {quick_model}")
            bridged_count += 1

        deep_model = unified_config.get_deep_analysis_model()
        if deep_model:
            os.environ['TRADINGAGENTS_DEEP_MODEL'] = deep_model
            logger.info(f"  ✓ 桥接深度分析模型: {deep_model}")
            bridged_count += 1

        # 3. 桥接数据源配置（基础 API 密钥）
        try:
            from sqlalchemy import select
            from app.core.database import sync_session_factory
            from app.core.pg_models import SystemConfig as SystemConfigModel
            from app.models.config import SystemConfig as SystemConfigSchema

            session = sync_session_factory()
            try:
                result = session.execute(
                    select(SystemConfigModel)
                    .where(SystemConfigModel.is_active == True)
                    .order_by(SystemConfigModel.version.desc())
                    .limit(1)
                )
                config_row = result.scalar_one_or_none()

                if config_row and config_row.data_source_configs:
                    ds_configs_raw = config_row.data_source_configs
                    data_source_configs = [SystemConfigSchema.get_ds_config(d) for d in ds_configs_raw] if hasattr(SystemConfigSchema, 'get_ds_config') else []
                    logger.info(f"  📊 从数据库读取到 {len(data_source_configs)} 个数据源配置")
                else:
                    logger.warning("  ⚠️  数据库中没有数据源配置，使用 JSON 文件配置")
                    data_source_configs = unified_config.get_data_source_configs()
            finally:
                session.close()

        except Exception as e:
            logger.error(f"❌ 从数据库读取数据源配置失败: {e}", exc_info=True)
            logger.warning("⚠️  将尝试从 JSON 文件读取配置作为后备方案")
            data_source_configs = unified_config.get_data_source_configs()

        for ds_config in data_source_configs:
            if ds_config.enabled and ds_config.api_key:
                if ds_config.type.value == 'tushare':
                    existing_token = os.getenv('TUSHARE_TOKEN')
                    if ds_config.api_key and not ds_config.api_key.startswith("your_"):
                        os.environ['TUSHARE_TOKEN'] = ds_config.api_key
                        logger.info(f"  ✓ 使用数据库中的 TUSHARE_TOKEN (长度: {len(ds_config.api_key)})")
                        if existing_token and existing_token != ds_config.api_key:
                            logger.info(f"  ℹ️  已覆盖 .env 文件中的 TUSHARE_TOKEN")
                    elif existing_token and not existing_token.startswith("your_"):
                        logger.info(f"  ✓ 使用 .env 文件中的 TUSHARE_TOKEN (长度: {len(existing_token)})")
                    else:
                        logger.warning(f"  ⚠️  TUSHARE_TOKEN 在数据库和 .env 中都未配置有效值")
                        continue
                    bridged_count += 1

                elif ds_config.type.value == 'finnhub':
                    existing_key = os.getenv('FINNHUB_API_KEY')
                    if ds_config.api_key and not ds_config.api_key.startswith("your_"):
                        os.environ['FINNHUB_API_KEY'] = ds_config.api_key
                        logger.info(f"  ✓ 使用数据库中的 FINNHUB_API_KEY (长度: {len(ds_config.api_key)})")
                        if existing_key and existing_key != ds_config.api_key:
                            logger.info(f"  ℹ️  已覆盖 .env 文件中的 FINNHUB_API_KEY")
                    elif existing_key and not existing_key.startswith("your_"):
                        logger.info(f"  ✓ 使用 .env 文件中的 FINNHUB_API_KEY (长度: {len(existing_key)})")
                    else:
                        logger.warning(f"  ⚠️  FINNHUB_API_KEY 在数据库和 .env 中都未配置有效值")
                        continue
                    bridged_count += 1

        # 4. 桥接数据源细节配置（超时、重试、缓存等）
        bridged_count += _bridge_datasource_details(data_source_configs)

        # 5. 桥接系统运行时配置
        bridged_count += _bridge_system_settings()

        # 6. 重新初始化 tradingagents 库的 PG 存储
        try:
            from tradingagents.config.config_manager import config_manager
            logger.info("🔄 重新初始化 tradingagents PG 存储...")

            use_pg_storage = os.getenv("USE_PG_STORAGE", "true")
            pg_conn = os.getenv("TRADINGAGENTS_PG_URI", settings.PG_URI)
            pg_db = os.getenv("TRADINGAGENTS_PG_DATABASE", settings.PG_DATABASE)
            logger.info(f"  📋 USE_PG_STORAGE: {use_pg_storage}")
            logger.info(f"  📋 PG 数据库: {pg_db}")

            if use_pg_storage.lower() == "true":
                try:
                    from tradingagents.config.pg_storage import PgStorage
                    config_manager.pg_storage = PgStorage(
                        connection_uri=pg_conn,
                        database_name=pg_db,
                    )
                    logger.info("✅ tradingagents PG 存储已启用")
                except Exception as e:
                    logger.error(f"❌ 创建 PgStorage 实例失败: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    config_manager.pg_storage = None
            else:
                logger.info("ℹ️ USE_PG_STORAGE 未启用，将使用 JSON 文件存储")
        except Exception as e:
            logger.error(f"❌ 重新初始化 tradingagents PG 存储失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

        # 7. 同步定价配置
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_sync_pricing_config_from_db())
            task.add_done_callback(_handle_sync_task_result)
            logger.info("🔄 定价配置同步任务已创建（后台执行）")
        except RuntimeError:
            asyncio.run(_sync_pricing_config_from_db())

        logger.info(f"✅ 配置桥接完成，共桥接 {bridged_count} 项配置")
        return True

    except Exception as e:
        logger.error(f"❌ 配置桥接失败: {e}", exc_info=True)
        logger.warning("⚠️  TradingAgents 将使用 .env 文件中的配置")
        return False


def _bridge_datasource_details(data_source_configs) -> int:
    bridged_count = 0
    for ds_config in data_source_configs:
        if not ds_config.enabled:
            continue
        source_type = ds_config.type.value.upper()
        if ds_config.timeout:
            env_key = f"{source_type}_TIMEOUT"
            os.environ[env_key] = str(ds_config.timeout)
            bridged_count += 1
        if ds_config.rate_limit:
            env_key = f"{source_type}_RATE_LIMIT"
            os.environ[env_key] = str(ds_config.rate_limit / 60.0)
            bridged_count += 1
        if ds_config.config_params and 'max_retries' in ds_config.config_params:
            env_key = f"{source_type}_MAX_RETRIES"
            os.environ[env_key] = str(ds_config.config_params['max_retries'])
            bridged_count += 1
        if ds_config.config_params and 'cache_ttl' in ds_config.config_params:
            env_key = f"{source_type}_CACHE_TTL"
            os.environ[env_key] = str(ds_config.config_params['cache_ttl'])
            bridged_count += 1
        if ds_config.config_params and 'cache_enabled' in ds_config.config_params:
            env_key = f"{source_type}_CACHE_ENABLED"
            os.environ[env_key] = str(ds_config.config_params['cache_enabled']).lower()
            bridged_count += 1
    if bridged_count > 0:
        logger.info(f"  ✓ 桥接数据源细节配置: {bridged_count} 项")
    return bridged_count


def _bridge_system_settings() -> int:
    try:
        from sqlalchemy import select
        from app.core.database import sync_session_factory
        from app.core.pg_models import SystemConfig as SystemConfigModel

        session = sync_session_factory()
        try:
            result = session.execute(
                select(SystemConfigModel).where(SystemConfigModel.is_active == True)
            )
            config_doc = result.scalar_one_or_none()

            if not config_doc or not config_doc.system_settings:
                logger.debug("  ⚠️  系统设置为空，跳过桥接")
                return 0

            system_settings = config_doc.system_settings
        finally:
            session.close()

        if not system_settings:
            return 0

        bridged_count = 0

        ta_settings = {
            'ta_hk_min_request_interval_seconds': 'TA_HK_MIN_REQUEST_INTERVAL_SECONDS',
            'ta_hk_timeout_seconds': 'TA_HK_TIMEOUT_SECONDS',
            'ta_hk_max_retries': 'TA_HK_MAX_RETRIES',
            'ta_hk_rate_limit_wait_seconds': 'TA_HK_RATE_LIMIT_WAIT_SECONDS',
            'ta_hk_cache_ttl_seconds': 'TA_HK_CACHE_TTL_SECONDS',
            'ta_use_app_cache': 'TA_USE_APP_CACHE',
        }

        token_tracking_settings = {
            'enable_cost_tracking': 'ENABLE_COST_TRACKING',
            'auto_save_usage': 'AUTO_SAVE_USAGE',
        }

        for setting_key, env_key in ta_settings.items():
            env_value = os.getenv(env_key)
            if env_value is not None:
                logger.info(f"  ✓ 使用 .env 文件中的 {env_key}: {env_value}")
                bridged_count += 1
            elif setting_key in system_settings:
                value = system_settings[setting_key]
                os.environ[env_key] = str(value).lower() if isinstance(value, bool) else str(value)
                logger.info(f"  ✓ 桥接 {env_key}: {value}")
                bridged_count += 1

        for setting_key, env_key in token_tracking_settings.items():
            if setting_key in system_settings:
                value = system_settings[setting_key]
                os.environ[env_key] = str(value).lower() if isinstance(value, bool) else str(value)
                logger.info(f"  ✓ 桥接 {env_key}: {value}")
                bridged_count += 1

        if 'app_timezone' in system_settings:
            os.environ['APP_TIMEZONE'] = system_settings['app_timezone']
            bridged_count += 1
        if 'currency_preference' in system_settings:
            os.environ['CURRENCY_PREFERENCE'] = system_settings['currency_preference']
            bridged_count += 1

        if bridged_count > 0:
            logger.info(f"  ✓ 桥接系统运行时配置: {bridged_count} 项")

        try:
            from app.core.unified_config import unified_config
            unified_config.save_system_settings(system_settings)
            logger.info(f"  ✓ 系统设置已同步到文件系统")
        except Exception as e:
            logger.warning(f"  ⚠️  同步系统设置到文件系统失败: {e}")

        return bridged_count

    except Exception as e:
        logger.warning(f"  ⚠️  桥接系统设置失败: {e}")
        return 0


def get_bridged_api_key(provider: str) -> Optional[str]:
    env_key = f"{provider.upper()}_API_KEY"
    return os.environ.get(env_key)


def get_bridged_model(model_type: str = "default") -> Optional[str]:
    if model_type == "quick":
        return os.environ.get('TRADINGAGENTS_QUICK_MODEL')
    elif model_type == "deep":
        return os.environ.get('TRADINGAGENTS_DEEP_MODEL')
    else:
        return os.environ.get('TRADINGAGENTS_DEFAULT_MODEL')


def clear_bridged_config():
    keys_to_clear = [
        'TRADINGAGENTS_DEFAULT_MODEL', 'TRADINGAGENTS_QUICK_MODEL', 'TRADINGAGENTS_DEEP_MODEL',
        'TUSHARE_TOKEN', 'FINNHUB_API_KEY',
        'APP_TIMEZONE', 'CURRENCY_PREFERENCE',
    ]
    providers = ['OPENAI', 'ANTHROPIC', 'GOOGLE', 'DEEPSEEK', 'DASHSCOPE', 'QIANFAN']
    for provider in providers:
        keys_to_clear.append(f'{provider}_API_KEY')
    data_sources = ['TUSHARE', 'AKSHARE', 'FINNHUB']
    for ds in data_sources:
        keys_to_clear.extend([f'{ds}_TIMEOUT', f'{ds}_RATE_LIMIT', f'{ds}_MAX_RETRIES', f'{ds}_CACHE_TTL', f'{ds}_CACHE_ENABLED'])
    ta_runtime_keys = [
        'TA_HK_MIN_REQUEST_INTERVAL_SECONDS', 'TA_HK_TIMEOUT_SECONDS',
        'TA_HK_MAX_RETRIES', 'TA_HK_RATE_LIMIT_WAIT_SECONDS', 'TA_HK_CACHE_TTL_SECONDS', 'TA_USE_APP_CACHE',
    ]
    keys_to_clear.extend(ta_runtime_keys)
    for key in keys_to_clear:
        if key in os.environ:
            del os.environ[key]


def reload_bridged_config():
    logger.info("🔄 重新加载配置桥接...")
    clear_bridged_config()
    return bridge_config_to_env()


def _sync_pricing_config_from_db():
    """从数据库同步定价配置（异步版本）"""
    async def _inner():
        try:
            from sqlalchemy import select
            from app.core.database import async_session_factory
            from app.core.pg_models import SystemConfig as SystemConfigModel
            from app.core.config import settings

            async with async_session_factory() as session:
                result = await session.execute(
                    select(SystemConfigModel)
                    .where(SystemConfigModel.is_active == True)
                    .order_by(SystemConfigModel.version.desc())
                    .limit(1)
                )
                config = result.scalar_one_or_none()

                if not config:
                    logger.warning("⚠️  未找到激活的配置")
                    return

                project_root = Path(__file__).parent.parent.parent
                config_dir = project_root / "config"
                config_dir.mkdir(exist_ok=True)

                pricing_file = config_dir / "pricing.json"
                pricing_configs = []

                for llm_config in config.llm_configs or []:
                    if llm_config.get('enabled', False):
                        provider = llm_config.get('provider')
                        if hasattr(provider, 'value'):
                            provider = provider.value
                        pricing_config = {
                            "provider": provider,
                            "model_name": llm_config.get('model_name'),
                            "input_price_per_1k": llm_config.get('input_price_per_1k') or 0.0,
                            "output_price_per_1k": llm_config.get('output_price_per_1k') or 0.0,
                            "currency": llm_config.get('currency') or "CNY",
                        }
                        pricing_configs.append(pricing_config)

                with open(pricing_file, 'w', encoding='utf-8') as f:
                    json.dump(pricing_configs, f, ensure_ascii=False, indent=2)

                logger.info(f"✅ 同步定价配置到 {pricing_file}: {len(pricing_configs)} 个模型")

        except Exception as e:
            logger.error(f"❌ 从数据库同步定价配置失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_inner())
    return _inner()


def _handle_sync_task_result(task):
    try:
        task.result()
    except Exception as e:
        logger.error(f"❌ 定价配置同步任务执行失败: {e}")


def sync_pricing_config_now():
    import asyncio
    try:
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_sync_pricing_config_from_db())
            task.add_done_callback(_handle_sync_task_result)
            logger.info("🔄 定价配置同步任务已创建（后台执行）")
            return True
        except RuntimeError:
            asyncio.run(_sync_pricing_config_from_db())
            return True
    except Exception as e:
        logger.error(f"❌ 立即同步定价配置失败: {e}")
        return False


__all__ = [
    'bridge_config_to_env',
    'get_bridged_api_key',
    'get_bridged_model',
    'clear_bridged_config',
    'reload_bridged_config',
    'sync_pricing_config_now',
]
