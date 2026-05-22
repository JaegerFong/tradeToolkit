#!/usr/bin/env python3
"""
创建默认管理员用户

功能：
- 创建默认管理员用户（admin/admin123）
- 如果用户已存在，可选择覆盖或跳过

使用方法：
    python scripts/create_default_admin.py
    python scripts/create_default_admin.py --overwrite
    python scripts/create_default_admin.py --username myuser --password mypass123
"""

import sys
import os
import hashlib
from datetime import datetime
from pathlib import Path
import argparse

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from pymongo import MongoClient


def _load_env(env_path: Path):
    """简单解析 .env 文件，避免依赖 python-dotenv"""
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value


_load_env(project_root / ".env")


def hash_password(password: str) -> str:
    """使用 SHA256 哈希密码（与 user_service 一致，不加 salt）"""
    return hashlib.sha256(password.encode()).hexdigest()


def get_mongo_config():
    """从 .env 读取 MongoDB 配置，与 app/core/config.py 保持一致"""
    host = os.getenv("MONGODB_HOST", "localhost")
    port = os.getenv("MONGODB_PORT", "27017")
    username = os.getenv("MONGODB_USERNAME", "")
    password = os.getenv("MONGODB_PASSWORD", "")
    auth_source = os.getenv("MONGODB_AUTH_SOURCE", "admin")
    database = os.getenv("MONGODB_DATABASE", "tradingagentscn")
    scope = os.getenv("MONGODB_DATABASE_SCOPE", "auto")
    debug = os.getenv("DEBUG", "true").lower() == "true"

    if username and password:
        uri = f"mongodb://{username}:{password}@{host}:{port}/?authSource={auth_source}"
    else:
        uri = f"mongodb://{host}:{port}/"

    # scope 计算，与 config.py MONGO_DB 属性一致
    if scope == "explicit" or not debug:
        db_name = database
    else:
        db_name = database  # 简化：explicit 模式直接使用 database

    return uri, db_name


def connect_mongodb(uri: str) -> MongoClient:
    """连接到 MongoDB"""
    print(f"🔌 连接到 MongoDB: {uri}")

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        print(f"✅ MongoDB 连接成功")
        return client

    except Exception as e:
        print(f"❌ 错误: MongoDB 连接失败: {e}")
        print(f"   请检查 .env 中的 MongoDB 配置")
        sys.exit(1)


def create_admin_user(
    db: any,
    username: str,
    password: str,
    email: str,
    overwrite: bool = False
) -> bool:
    """创建管理员用户"""
    users_collection = db.users
    
    # 检查用户是否已存在
    existing_user = users_collection.find_one({"username": username})
    
    if existing_user:
        if not overwrite:
            print(f"⚠️  用户 '{username}' 已存在")
            print(f"   如需覆盖，请使用 --overwrite 参数")
            return False
        else:
            print(f"⚠️  用户 '{username}' 已存在，将覆盖")
            users_collection.delete_one({"username": username})
    
    # 创建用户文档
    user_doc = {
        "username": username,
        "email": email,
        "hashed_password": hash_password(password),
        "is_active": True,
        "is_verified": True,
        "is_admin": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "last_login": None,
        "preferences": {
            "default_market": "A股",
            "default_depth": "深度",
            "ui_theme": "light",
            "language": "zh-CN",
            "notifications_enabled": True,
            "email_notifications": False
        },
        "daily_quota": 10000,
        "concurrent_limit": 10,
        "total_analyses": 0,
        "successful_analyses": 0,
        "failed_analyses": 0,
        "favorite_stocks": []
    }
    
    users_collection.insert_one(user_doc)
    
    print(f"✅ 管理员用户创建成功")
    print(f"   用户名: {username}")
    print(f"   密码: {password}")
    print(f"   邮箱: {email}")
    print(f"   角色: 管理员")
    print(f"   配额: {user_doc['daily_quota']} 次/天")
    print(f"   并发: {user_doc['concurrent_limit']} 个")
    
    return True


def list_users(db: any):
    """列出所有用户"""
    users_collection = db.users
    users = list(users_collection.find({}, {
        "username": 1,
        "email": 1,
        "is_admin": 1,
        "is_active": 1,
        "created_at": 1
    }))
    
    if not users:
        print("📋 当前没有用户")
        return
    
    print(f"📋 当前用户列表 ({len(users)} 个):")
    print(f"{'用户名':<15} {'邮箱':<30} {'角色':<10} {'状态':<10} {'创建时间'}")
    print("-" * 90)
    
    for user in users:
        username = user.get("username", "N/A")
        email = user.get("email", "N/A")
        role = "管理员" if user.get("is_admin", False) else "普通用户"
        status = "激活" if user.get("is_active", True) else "禁用"
        created_at = user.get("created_at", "N/A")
        if isinstance(created_at, datetime):
            created_at = created_at.strftime("%Y-%m-%d %H:%M")
        
        print(f"{username:<15} {email:<30} {role:<10} {status:<10} {created_at}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="创建默认管理员用户",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 创建默认管理员（admin/admin123）
  python scripts/create_default_admin.py
  
  # 覆盖已存在的用户
  python scripts/create_default_admin.py --overwrite
  
  # 创建自定义管理员
  python scripts/create_default_admin.py --username myuser --password mypass123 --email myuser@example.com
  
  # 列出所有用户
  python scripts/create_default_admin.py --list
        """
    )
    
    parser.add_argument(
        "--username",
        default="admin",
        help="用户名（默认: admin）"
    )
    parser.add_argument(
        "--password",
        default="admin123",
        help="密码（默认: admin123）"
    )
    parser.add_argument(
        "--email",
        help="邮箱（默认: <username>@tradingagents.cn）"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已存在的用户"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有用户"
    )
    
    args = parser.parse_args()
    
    # 设置默认邮箱
    if not args.email:
        args.email = f"{args.username}@tradingagents.cn"
    
    print("=" * 80)
    print("👤 创建默认管理员用户")
    print("=" * 80)
    print()
    
    # 从 .env 读取连接配置
    mongo_uri, db_name = get_mongo_config()

    # 连接数据库
    client = connect_mongodb(mongo_uri)
    db = client[db_name]
    
    # 列出用户
    if args.list:
        print()
        list_users(db)
        client.close()
        return
    
    # 创建用户
    print()
    success = create_admin_user(
        db,
        args.username,
        args.password,
        args.email,
        args.overwrite
    )
    
    # 列出所有用户
    print()
    list_users(db)
    
    # 关闭连接
    client.close()
    
    if success:
        print()
        print("=" * 80)
        print("✅ 操作完成！")
        print("=" * 80)
        print()
        print("🔐 登录信息:")
        print(f"   用户名: {args.username}")
        print(f"   密码: {args.password}")
        print()
        print("📝 后续步骤:")
        print("   1. 访问前端并使用上述账号登录")
        print("   2. 建议登录后立即修改密码")


if __name__ == "__main__":
    main()

