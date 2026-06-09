#!/bin/bash
# ═══════════════════════════════════════════════════════════
# OpenCloudOS / CentOS / RHEL 一键部署脚本
# 用法: chmod +x deploy.sh && ./deploy.sh
# ═══════════════════════════════════════════════════════════
set -e

APP_DIR="/opt/okx-trader"
PYTHON="python3"
LOG_DIR="/var/log/okx-trader"

echo "========================================"
echo "  OKX AI 交易系统 - 服务器部署"
echo "  OpenCloudOS / RHEL / CentOS 7/8/9"
echo "========================================"

# ── 1. 安装系统依赖 ──
echo ""
echo "[1/6] 安装系统依赖..."
sudo yum install -y python3 python3-pip python3-devel gcc git 2>/dev/null || \
sudo dnf install -y python3 python3-pip python3-devel gcc git 2>/dev/null || \
sudo apt-get install -y python3 python3-pip python3-dev gcc git 2>/dev/null

# ── 2. 升级 pip ──
echo "[2/6] 升级 pip..."
$PYTHON -m pip install --upgrade pip -q

# ── 3. 创建目录 ──
echo "[3/6] 创建数据目录..."
sudo mkdir -p /data/okx/history /data/okx/output
sudo mkdir -p $LOG_DIR
sudo chown -R $USER:$USER /data/okx $LOG_DIR

# ── 4. 克隆/复制项目 ──
if [ ! -d "$APP_DIR" ]; then
    echo "[4/6] 克隆项目..."
    # 如果项目已在 git，使用 git clone；否则手动 scp
    # git clone https://your-repo.git $APP_DIR
    echo "  ⚠ 请手动将项目文件复制到 $APP_DIR"
    echo "  方式1: scp -r ./OKX user@server:$APP_DIR"
    echo "  方式2: 如果已有 git 仓库，修改上方 git clone 命令"
else
    echo "[4/6] 项目目录已存在，跳过..."
fi

# ── 5. 安装 Python 依赖 ──
if [ -f "$APP_DIR/requirements_server.txt" ]; then
    echo "[5/6] 安装 Python 依赖..."
    cd $APP_DIR
    $PYTHON -m pip install -r requirements_server.txt -q
else
    echo "[5/6] ⚠ 未找到 requirements_server.txt，尝试 requirements.txt..."
    cd $APP_DIR
    $PYTHON -m pip install -r requirements.txt -q 2>/dev/null || echo "  部分包安装失败(可能含Windows专有包)，忽略"
fi

# ── 6. 配置 systemd 服务 ──
echo "[6/6] 配置 systemd 后台服务..."
sudo tee /etc/systemd/system/okx-trader.service > /dev/null << 'SERVICE_EOF'
[Unit]
Description=OKX AI Trading System
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/okx-trader
ExecStart=/usr/bin/python3 /opt/okx-trader/web_app.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/okx-trader/app.log
StandardError=append:/var/log/okx-trader/app_error.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE_EOF

sudo systemctl daemon-reload
sudo systemctl enable okx-trader

echo ""
echo "========================================"
echo "  部署完成!"
echo "========================================"
echo ""
echo "后续步骤:"
echo "  1. 编辑配置:  cp $APP_DIR/config_server.json $APP_DIR/config.json"
echo "     然后修改 API 密钥等敏感信息"
echo ""
echo "  2. 启动服务:  sudo systemctl start okx-trader"
echo "  3. 查看日志:  sudo journalctl -u okx-trader -f"
echo "  4. 访问面板:  http://服务器IP:8488"
echo ""
echo "常用命令:"
echo "  启动: sudo systemctl start okx-trader"
echo "  停止: sudo systemctl stop okx-trader"
echo "  重启: sudo systemctl restart okx-trader"
echo "  状态: sudo systemctl status okx-trader"
echo "  日志: tail -f $LOG_DIR/app.log"
echo ""
