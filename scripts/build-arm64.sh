#!/bin/bash
# tradeToolkit ARM64 架构 Docker 镜像构建脚本
# 适用于：ARM 服务器、树莓派 4/5、NVIDIA Jetson 等

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 版本信息
VERSION="${VERSION:-v1.0.0-preview}"
REGISTRY="${REGISTRY:-}"  # 留空表示本地构建，设置为 Docker Hub 用户名可推送到远程

# 镜像名称（ARM64 独立仓库）
BACKEND_IMAGE="tradingagents-backend-arm64"
FRONTEND_IMAGE="tradingagents-frontend-arm64"

# 目标架构
PLATFORM="linux/arm64"

# 基础镜像。Docker Hub 网络不稳定时，可以通过环境变量切换到镜像源。
PYTHON_BASE_IMAGE="${PYTHON_BASE_IMAGE:-python:3.10-slim-bookworm}"
NODE_BASE_IMAGE="${NODE_BASE_IMAGE:-node:22-alpine}"
NGINX_BASE_IMAGE="${NGINX_BASE_IMAGE:-nginx:alpine}"

print_build_failure_help() {
    echo ""
    echo -e "${YELLOW}排查建议:${NC}"
    echo -e "${YELLOW}1. 如果错误停在 load metadata / failed to fetch oauth token，说明 Docker Hub 连接超时。${NC}"
    echo -e "${YELLOW}   可重试，或临时切换基础镜像源后再构建，例如:${NC}"
    echo ""
    echo -e "  PYTHON_BASE_IMAGE=registry.cn-hangzhou.aliyuncs.com/library/python:3.10-slim-bookworm \\"
    echo -e "  NODE_BASE_IMAGE=registry.cn-hangzhou.aliyuncs.com/library/node:22-alpine \\"
    echo -e "  NGINX_BASE_IMAGE=registry.cn-hangzhou.aliyuncs.com/library/nginx:alpine \\"
    echo -e "  VERSION=${VERSION} ./scripts/build-arm64.sh"
    echo ""
    echo -e "${YELLOW}2. 如果错误是 COPY xxx not found，说明构建上下文缺少文件或被 .dockerignore 排除了。${NC}"
    echo -e "${YELLOW}3. 也可以在 Docker Desktop -> Settings -> Docker Engine 配置 registry-mirrors。${NC}"
}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}tradeToolkit ARM64 镜像构建${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}版本: ${VERSION}${NC}"
echo -e "${GREEN}架构: ${PLATFORM}${NC}"
echo -e "${GREEN}Python基础镜像: ${PYTHON_BASE_IMAGE}${NC}"
echo -e "${GREEN}Node基础镜像: ${NODE_BASE_IMAGE}${NC}"
echo -e "${GREEN}Nginx基础镜像: ${NGINX_BASE_IMAGE}${NC}"
echo -e "${GREEN}适用: ARM 服务器、树莓派、NVIDIA Jetson${NC}"
if [ -n "$REGISTRY" ]; then
    echo -e "${GREEN}仓库: ${REGISTRY}${NC}"
else
    echo -e "${YELLOW}仓库: 本地构建（不推送）${NC}"
fi
echo ""

# 检查 Docker 是否安装
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker 未安装${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker 已安装${NC}"

# 检查 Docker Buildx 是否可用
if ! docker buildx version &> /dev/null; then
    echo -e "${RED}❌ Docker Buildx 未安装或不可用${NC}"
    echo -e "${YELLOW}请升级到 Docker 19.03+ 或安装 Buildx 插件${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker Buildx 可用${NC}"

# 创建或使用 buildx builder
echo ""
echo -e "${BLUE}配置 Docker Buildx...${NC}"
BUILDER_NAME="tradingagents-builder-arm64"

if docker buildx inspect "$BUILDER_NAME" &> /dev/null; then
    echo -e "${GREEN}✅ Builder '$BUILDER_NAME' 已存在${NC}"
else
    echo -e "${YELLOW}创建新的 Builder '$BUILDER_NAME'...${NC}"
    docker buildx create --name "$BUILDER_NAME" --use --platform "$PLATFORM"
    echo -e "${GREEN}✅ Builder 创建成功${NC}"
fi

# 使用指定的 builder
docker buildx use "$BUILDER_NAME"

# 启动 builder（如果未运行）
docker buildx inspect --bootstrap

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}开始构建镜像${NC}"
echo -e "${BLUE}========================================${NC}"

# 构建后端镜像
echo ""
echo -e "${YELLOW}📦 构建后端镜像 (ARM64)...${NC}"
BACKEND_TAG="${BACKEND_IMAGE}:${VERSION}"
if [ -n "$REGISTRY" ]; then
    BACKEND_TAG="${REGISTRY}/${BACKEND_TAG}"
fi

BUILD_ARGS="--platform ${PLATFORM} --build-arg PYTHON_BASE_IMAGE=${PYTHON_BASE_IMAGE} -f Dockerfile.backend -t ${BACKEND_TAG}"

if [ -n "$REGISTRY" ]; then
    # 推送到远程仓库
    BUILD_ARGS="${BUILD_ARGS} --push"
    echo -e "${YELLOW}将推送到: ${BACKEND_TAG}${NC}"
else
    # 本地构建并加载
    BUILD_ARGS="${BUILD_ARGS} --load"
    echo -e "${YELLOW}本地构建: ${BACKEND_TAG}${NC}"
fi

# 同时打上 latest 标签
BACKEND_TAG_LATEST="${BACKEND_IMAGE}:latest"
if [ -n "$REGISTRY" ]; then
    BACKEND_TAG_LATEST="${REGISTRY}/${BACKEND_TAG_LATEST}"
fi
BUILD_ARGS="${BUILD_ARGS} -t ${BACKEND_TAG_LATEST}"

echo -e "${BLUE}构建命令: docker buildx build ${BUILD_ARGS} .${NC}"
if ! docker buildx build $BUILD_ARGS .; then
    echo -e "${RED}❌ 后端镜像构建失败${NC}"
    print_build_failure_help
    exit 1
fi
echo -e "${GREEN}✅ 后端镜像构建成功${NC}"

# 构建前端镜像
echo ""
echo -e "${YELLOW}📦 构建前端镜像 (ARM64)...${NC}"
FRONTEND_TAG="${FRONTEND_IMAGE}:${VERSION}"
if [ -n "$REGISTRY" ]; then
    FRONTEND_TAG="${REGISTRY}/${FRONTEND_TAG}"
fi

BUILD_ARGS="--platform ${PLATFORM} --build-arg NODE_BASE_IMAGE=${NODE_BASE_IMAGE} --build-arg NGINX_BASE_IMAGE=${NGINX_BASE_IMAGE} -f Dockerfile.frontend -t ${FRONTEND_TAG}"

if [ -n "$REGISTRY" ]; then
    # 推送到远程仓库
    BUILD_ARGS="${BUILD_ARGS} --push"
    echo -e "${YELLOW}将推送到: ${FRONTEND_TAG}${NC}"
else
    # 本地构建并加载
    BUILD_ARGS="${BUILD_ARGS} --load"
    echo -e "${YELLOW}本地构建: ${FRONTEND_TAG}${NC}"
fi

# 同时打上 latest 标签
FRONTEND_TAG_LATEST="${FRONTEND_IMAGE}:latest"
if [ -n "$REGISTRY" ]; then
    FRONTEND_TAG_LATEST="${REGISTRY}/${FRONTEND_TAG_LATEST}"
fi
BUILD_ARGS="${BUILD_ARGS} -t ${FRONTEND_TAG_LATEST}"

echo -e "${BLUE}构建命令: docker buildx build ${BUILD_ARGS} .${NC}"
if ! docker buildx build $BUILD_ARGS .; then
    echo -e "${RED}❌ 前端镜像构建失败${NC}"
    print_build_failure_help
    exit 1
fi
echo -e "${GREEN}✅ 前端镜像构建成功${NC}"

# 构建完成
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}✅ ARM64 镜像构建完成！${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

if [ -n "$REGISTRY" ]; then
    echo -e "${GREEN}镜像已推送到远程仓库:${NC}"
    echo -e "  - ${BACKEND_TAG}"
    echo -e "  - ${BACKEND_TAG_LATEST}"
    echo -e "  - ${FRONTEND_TAG}"
    echo -e "  - ${FRONTEND_TAG_LATEST}"
    echo ""
    echo -e "${YELLOW}使用方法:${NC}"
    echo -e "  docker pull ${BACKEND_TAG}"
    echo -e "  docker pull ${FRONTEND_TAG}"
    echo ""
    echo -e "${GREEN}💡 独立仓库说明:${NC}"
    echo -e "  - ARM64 版本使用独立仓库: ${REGISTRY}/${BACKEND_IMAGE}"
    echo -e "  - AMD64 版本使用独立仓库: ${REGISTRY}/tradingagents-backend-amd64"
    echo -e "  - 可以独立更新，互不影响"
else
    echo -e "${GREEN}镜像已构建到本地:${NC}"
    echo -e "  - ${BACKEND_TAG}"
    echo -e "  - ${BACKEND_TAG_LATEST}"
    echo -e "  - ${FRONTEND_TAG}"
    echo -e "  - ${FRONTEND_TAG_LATEST}"
    echo ""
    echo -e "${YELLOW}使用方法:${NC}"
    echo -e "  docker-compose -f docker-compose.v1.0.0.yml up -d"
fi

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${YELLOW}💡 提示${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${YELLOW}1. 推送到 Docker Hub:${NC}"
echo -e "   REGISTRY=your-dockerhub-username VERSION=v1.0.0 ./scripts/build-arm64.sh"
echo ""
echo -e "${YELLOW}2. 本地构建:${NC}"
echo -e "   ./scripts/build-arm64.sh"
echo ""
echo -e "${YELLOW}3. 查看镜像:${NC}"
echo -e "   docker images | grep tradingagents"
echo ""
echo -e "${YELLOW}4. 构建其他架构:${NC}"
echo -e "   AMD64: ./scripts/build-amd64.sh"
echo -e "   Apple Silicon: ./scripts/build-apple-silicon.sh"
echo ""
echo -e "${YELLOW}5. 性能优化建议:${NC}"
echo -e "   - ARM 设备构建较慢，建议使用预构建镜像"
echo -e "   - 或在 x86 机器上交叉编译后推送到仓库"
echo ""
