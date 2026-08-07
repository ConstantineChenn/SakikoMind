#!/bin/bash

# SakikoMind 智能客服系统 - Docker 部署脚本


set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 配置
PROJECT_NAME="sakikomind"
COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"

# 函数：打印信息
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 函数：检查依赖
check_dependencies() {
    print_info "检查依赖..."

    if ! command -v docker &> /dev/null; then
        print_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose 未安装，请先安装 Docker Compose"
        exit 1
    fi

    print_info "依赖检查完成"
}

# 函数：创建必要的目录
create_directories() {
    print_info "创建必要的目录..."

    mkdir -p data/chroma
    mkdir -p logs
    mkdir -p config/nginx/ssl
    mkdir -p config/grafana/provisioning
    mkdir -p config/grafana/dashboards
    mkdir -p config/alerts

    print_info "目录创建完成"
}

# 函数：检查环境变量
check_env_file() {
    print_info "检查环境变量配置..."

    if [ ! -f "$ENV_FILE" ]; then
        print_warn ".env 文件不存在，从 .env.example 创建..."

        if [ -f ".env.example" ]; then
            cp .env.example .env
            print_info "已创建 .env 文件，请编辑配置"
            print_warn "特别注意：请设置 ANTHROPIC_API_KEY"
        else
            print_error ".env.example 文件不存在"
            exit 1
        fi
    else
        print_info "环境变量配置文件已存在"
    fi
}

# 函数：构建镜像
build_images() {
    print_info "构建 Docker 镜像..."

    docker-compose build --no-cache

    print_info "镜像构建完成"
}

# 函数：启动服务
start_services() {
    print_info "启动服务..."

    docker-compose up -d

    print_info "服务启动完成"
}

# 函数：停止服务
stop_services() {
    print_info "停止服务..."

    docker-compose down

    print_info "服务已停止"
}

# 函数：重启服务
restart_services() {
    print_info "重启服务..."

    docker-compose restart

    print_info "服务已重启"
}

# 函数：查看服务状态
status_services() {
    print_info "服务状态:"

    docker-compose ps
}

# 函数：查看日志
view_logs() {
    local service=$1

    if [ -z "$service" ]; then
        print_info "查看所有服务日志..."
        docker-compose logs -f
    else
        print_info "查看 $service 服务日志..."
        docker-compose logs -f "$service"
    fi
}

# 函数：健康检查
health_check() {
    print_info "执行健康检查..."

    # 等待服务启动
    sleep 10

    # 检查主应用
    if curl -sf http://localhost:8000/health > /dev/null; then
        print_info "✓ 主应用健康"
    else
        print_error "✗ 主应用不健康"
    fi

    # 检查 Redis
    if docker-compose exec -T redis redis-cli ping | grep -q PONG; then
        print_info "✓ Redis 健康"
    else
        print_error "✗ Redis 不健康"
    fi

    # 检查 ChromaDB
    if curl -sf http://localhost:8001/api/v1/heartbeat > /dev/null; then
        print_info "✓ ChromaDB 健康"
    else
        print_error "✗ ChromaDB 不健康"
    fi

    # 检查 Prometheus
    if curl -sf http://localhost:9090/-/healthy > /dev/null; then
        print_info "✓ Prometheus 健康"
    else
        print_error "✗ Prometheus 不健康"
    fi
}

# 函数：清理资源
cleanup() {
    print_warn "清理所有资源（包括数据卷）..."

    read -p "确认清理？这将删除所有数据 (y/N): " -n 1 -r
    echo

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        docker-compose down -v
        print_info "清理完成"
    else
        print_info "清理已取消"
    fi
}

# 函数：备份数据
backup_data() {
    local backup_dir="backups/$(date +%Y%m%d_%H%M%S)"

    print_info "备份数据到 $backup_dir..."

    mkdir -p "$backup_dir"

    # 备份 Redis 数据
    docker-compose exec -T redis redis-cli SAVE
    docker cp sakikomind-redis:/data/dump.rdb "$backup_dir/"

    # 备份 ChromaDB 数据
    docker cp sakikomind-chromadb:/chroma/chroma "$backup_dir/"

    # 备份配置
    cp .env "$backup_dir/"
    cp -r config "$backup_dir/"

    print_info "备份完成: $backup_dir"
}

# 函数：恢复数据
restore_data() {
    local backup_dir=$1

    if [ -z "$backup_dir" ]; then
        print_error "请指定备份目录"
        exit 1
    fi

    if [ ! -d "$backup_dir" ]; then
        print_error "备份目录不存在: $backup_dir"
        exit 1
    fi

    print_warn "从 $backup_dir 恢复数据..."
    read -p "确认恢复？这将覆盖现有数据 (y/N): " -n 1 -r
    echo

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # 停止服务
        docker-compose stop

        # 恢复 Redis 数据
        docker cp "$backup_dir/dump.rdb" sakikomind-redis:/data/

        # 恢复 ChromaDB 数据
        docker cp "$backup_dir/chroma" sakikomind-chromadb:/chroma/

        # 恢复配置
        cp "$backup_dir/.env" .env
        rm -rf config
        cp -r "$backup_dir/config" config

        # 启动服务
        docker-compose start

        print_info "恢复完成"
    else
        print_info "恢复已取消"
    fi
}

# 函数：显示帮助信息
show_help() {
    cat << EOF
SakikoMind 智能客服系统 - Docker 部署脚本

用法: ./docker-deploy.sh [命令]

命令:
    install     初始化安装（检查依赖、创建目录、构建镜像）
    start       启动所有服务
    stop        停止所有服务
    restart     重启所有服务
    status      查看服务状态
    logs        查看服务日志（可选指定服务名）
    health      执行健康检查
    build       重新构建镜像
    cleanup     清理所有资源（包括数据）
    backup      备份数据
    restore     恢复数据（需指定备份目录）
    help        显示此帮助信息

示例:
    ./docker-deploy.sh install
    ./docker-deploy.sh start
    ./docker-deploy.sh logs sakikomind
    ./docker-deploy.sh backup
    ./docker-deploy.sh restore backups/20231201_120000

环境变量:
    在 .env 文件中配置相关参数

EOF
}

# 主函数
main() {
    case "${1:-help}" in
        install)
            check_dependencies
            check_env_file
            create_directories
            build_images
            print_info "安装完成！运行 './docker-deploy.sh start' 启动服务"
            ;;
        start)
            check_env_file
            start_services
            health_check
            ;;
        stop)
            stop_services
            ;;
        restart)
            restart_services
            ;;
        status)
            status_services
            ;;
        logs)
            view_logs "$2"
            ;;
        health)
            health_check
            ;;
        build)
            build_images
            ;;
        cleanup)
            cleanup
            ;;
        backup)
            backup_data
            ;;
        restore)
            restore_data "$2"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            print_error "未知命令: $1"
            show_help
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"