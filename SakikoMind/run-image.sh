#!/bin/bash

# SakikoMind 镜像运行脚本
# 提供多种运行配置选项

set -e

# 配置
IMAGE_NAME="sakikomind"
CONTAINER_NAME="sakikomind-app"
VERSION=${VERSION:-latest}
REGISTRY=""  # 如果镜像在私有仓库，设置为 registry.example.com/

# 默认端口映射
API_PORT=8000
PROMETHEUS_PORT=9090

# 默认卷映射
DATA_DIR="./data"
LOGS_DIR="./logs"
CONFIG_DIR="./config"

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_help() {
    cat << EOF
SakikoMind Docker 镜像运行工具

用法: ./run-image.sh [命令] [选项]

命令:
    run             运行容器（默认模式）
    run-dev         运行开发模式容器
    run-test        运行测试容器
    stop            停止容器
    restart         重启容器
    logs            查看容器日志
    shell           进入容器 shell
    status          查看容器状态
    clean           清理容器和数据
    help            显示此帮助

选项:
    --detach        后台运行
    --ports         自定义端口映射
    --env-file      指定环境变量文件
    --volume        自定义卷映射
    --name          自定义容器名称
    --network       自定义网络

示例:
    ./run-image.sh run
    ./run-image.sh run-dev --detach
    ./run-image.sh run --env-file .env.prod
    ./run-image.sh logs
    ./run-image.sh shell

生产环境运行:
    ./run-image.sh run \\
        --env-file .env.prod \\
        --detach \\
        --restart unless-stopped

开发环境运行:
    ./run-image.sh run-dev \\
        --volume ./src:/app/src \\
        --detach

EOF
}

# 创建必要的目录
ensure_directories() {
    mkdir -p "$DATA_DIR"
    mkdir -p "$LOGS_DIR"
    mkdir -p "$CONFIG_DIR"
}

# 运行容器
run_container() {
    local mode=$1
    shift || true

    local detach=false
    local env_file=".env"
    local custom_ports=""
    local custom_volumes=""
    local container_name="$CONTAINER_NAME"
    local restart_policy="no"

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            --detach|-d)
                detach=true
                shift
                ;;
            --env-file)
                env_file="$2"
                shift 2
                ;;
            --ports|-p)
                custom_ports="-p $2"
                shift 2
                ;;
            --volume|-v)
                custom_volumes="$custom_volumes -v $2"
                shift 2
                ;;
            --name)
                container_name="$2"
                shift 2
                ;;
            --restart)
                restart_policy="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    ensure_directories

    # 检查环境文件
    if [ ! -f "$env_file" ]; then
        print_warn "环境文件 $env_file 不存在，使用默认配置"
        env_file=""
    else
        env_file="--env-file $env_file"
    fi

    # 基础配置
    local image_tag="${REGISTRY}${IMAGE_NAME}:${VERSION}"
    local default_ports="-p ${API_PORT}:8000 -p ${PROMETHEUS_PORT}:9090"
    local default_volumes="-v ${DATA_DIR}:/app/data -v ${LOGS_DIR}:/app/logs -v ${CONFIG_DIR}:/app/config"

    # 根据模式调整配置
    case $mode in
        dev)
            print_info "运行开发模式容器"
            default_ports="$default_ports -p 5678:5678"  # 添加调试端口
            restart_policy="no"
            ;;
        test)
            print_info "运行测试容器"
            restart_policy="no"
            ;;
        prod)
            print_info "运行生产模式容器"
            restart_policy="unless-stopped"
            ;;
        *)
            print_info "运行标准容器"
            ;;
    esac

    # 构建运行命令
    local run_cmd="docker run"

    if [ "$detach" = true ]; then
        run_cmd="$run_cmd -d"
    fi

    run_cmd="$run_cmd --name $container_name"
    run_cmd="$run_cmd --restart $restart_policy"
    run_cmd="$run_cmd $default_ports $custom_ports"
    run_cmd="$run_cmd $default_volumes $custom_volumes"
    run_cmd="$run_cmd $env_file"
    run_cmd="$run_cmd $image_tag"

    print_info "启动容器: $container_name"
    print_info "镜像: $image_tag"

    # 检查容器是否已存在
    if docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"; then
        print_warn "容器 $container_name 已存在，先停止并删除"
        docker stop "$container_name" 2>/dev/null || true
        docker rm "$container_name" 2>/dev/null || true
    fi

    # 运行容器
    eval $run_cmd

    if [ $? -eq 0 ]; then
        print_info "✓ 容器启动成功"
        print_info "API地址: http://localhost:${API_PORT}"
        print_info "Prometheus: http://localhost:${PROMETHEUS_PORT}"

        if [ "$detach" = true ]; then
            print_info "容器在后台运行"
            print_info "查看日志: ./run-image.sh logs"
        fi
    else
        print_error "✗ 容器启动失败"
        exit 1
    fi
}

# 停止容器
stop_container() {
    local name=${1:-$CONTAINER_NAME}

    print_info "停止容器: $name"

    if docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
        docker stop "$name"
        print_info "✓ 容器已停止"
    else
        print_warn "容器 $name 未运行"
    fi
}

# 重启容器
restart_container() {
    local name=${1:-$CONTAINER_NAME}

    print_info "重启容器: $name"

    if docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
        docker restart "$name"
        print_info "✓ 容器已重启"
    else
        print_error "容器 $name 不存在"
        exit 1
    fi
}

# 查看日志
view_logs() {
    local name=${1:-$CONTAINER_NAME}
    local follow=${2:-true}

    if [ "$follow" = "true" ]; then
        docker logs -f "$name"
    else
        docker logs "$name"
    fi
}

# 进入容器 shell
enter_shell() {
    local name=${1:-$CONTAINER_NAME}

    print_info "进入容器 shell: $name"

    if docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
        docker exec -it "$name" /bin/bash
    else
        print_error "容器 $name 未运行"
        exit 1
    fi
}

# 查看状态
show_status() {
    local name=${1:-$CONTAINER_NAME}

    print_info "容器状态: $name"

    if docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
        docker ps -a --filter "name=$name" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    else
        print_warn "容器 $name 不存在"
    fi
}

# 清理容器
clean_container() {
    local name=${1:-$CONTAINER_NAME}

    print_warn "清理容器和数据卷"

    read -p "确认清理？这将删除容器和数据 (y/N): " -n 1 -r
    echo

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        docker stop "$name" 2>/dev/null || true
        docker rm "$name" 2>/dev/null || true
        print_info "✓ 容器已清理"
    else
        print_info "清理已取消"
    fi
}

# 主函数
main() {
    local command=${1:-run}
    shift || true

    case $command in
        run)
            run_container "" "$@"
            ;;
        run-dev)
            run_container "dev" "$@"
            ;;
        run-test)
            run_container "test" "$@"
            ;;
        run-prod)
            run_container "prod" "$@"
            ;;
        stop)
            stop_container "$1"
            ;;
        restart)
            restart_container "$1"
            ;;
        logs)
            if [ "$1" = "--no-follow" ]; then
                view_logs "$2" "false"
            else
                view_logs "$1" "true"
            fi
            ;;
        shell)
            enter_shell "$1"
            ;;
        status)
            show_status "$1"
            ;;
        clean)
            clean_container "$1"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            print_error "未知命令: $command"
            show_help
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"