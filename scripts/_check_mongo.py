import os, sys
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# parse .env
env_file = os.path.join(project_root, ".env")
env = {}
with open(env_file, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")

host = env.get("MONGODB_HOST", "localhost")
port = env.get("MONGODB_PORT", "27017")
user = env.get("MONGODB_USERNAME", "")
pwd = env.get("MONGODB_PASSWORD", "")
auth = env.get("MONGODB_AUTH_SOURCE", "admin")
db_name = env.get("MONGODB_DATABASE", "tradingagentscn")

uri = f"mongodb://{user}:{pwd}@{host}:{port}/?authSource={auth}"
print(f"连接: {uri}")

from pymongo import MongoClient
c = MongoClient(uri, serverSelectionTimeoutMS=5000)
db = c[db_name]

# 查 users 集合
users = list(db.users.find({}, {"username": 1, "is_admin": 1, "is_active": 1, "email": 1, "_id": 0}))
print(f"\nusers 集合文档数: {len(users)}")
for u in users:
    print(f"  username={u.get('username')}, is_admin={u.get('is_admin')}, is_active={u.get('is_active')}, email={u.get('email')}")

# 列出所有集合
cols = db.list_collection_names()
print(f"\n所有集合: {cols}")
