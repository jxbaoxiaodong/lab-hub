# Lab 分布式检测工具箱 v2.0.0

> 专为检测计量行业设计的分布式标准查询与下载系统

[![版本](https://img.shields.io/badge/version-2.0.0-blue.svg)]()
[![Python](https://img.shields.io/badge/python-3.8+-green.svg)](https://www.python.org/)
[![平台](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)]()

---

## ✨ 核心功能

### 🌐 分布式架构
- 服务端集中管理配置，客户端分布式执行
- **加密配置分发**（乱码保存，需密钥解密）
- **心跳续期机制**（每30秒续期密钥）
- 断网5分钟后自动失效，无法继续使用
- 支持多客户端同时在线

### 📄 智能文件识别
- 支持 PDF、Word、Excel、CSV、TXT、图片格式
- 自动提取文件中的标准号
- OCR识别扫描件中的文字
- 智能去重和标准化格式

### 🔍 标准批量查询
- 支持 GB、JJG、ISO、IEC、ASTM 等多种标准
- 多平台自动轮询（湖南/深圳/陕西/江西/聊城/六安/厦门）
- 查询标准状态（现行/废止/替代）
- 显示发布/实施日期

### ⬇️ 自动下载
- 下载源已收口为 2 个：**食品伙伴网** + **国家标准全文公开系统**
- 规则：优先食品伙伴网；仅当食品伙伴网无结果且属于国标（GB/GB-T）时，才查询国家标准平台；其他标准直接无结果
- 在线预览链接返回

### 👮 实时封禁控制
- 服务端可随时封禁客户端
- 封禁后心跳不再续期密钥
- 5分钟后客户端自动失效停止运行
- 无需许可证检查，配置即许可

### 📢 广播消息系统
- 服务端可向所有客户端发送广播
- 广播显示在客户端顶部走马灯
- 支持历史广播管理

### 💬 在线客服
- 客户端可直接联系管理员
- 消息实时同步
- 历史消息本地保存

---

## 🚀 快速开始

### 服务端部署

```bash
# 1. 进入服务端目录
cd backend

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务端
python server_hub.py

# 服务将运行在 0.0.0.0:14114
```

### 客户端启动

```bash
# 1. 进入客户端目录
cd client

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动客户端
python app.py

# 客户端将自动打开浏览器
```

---

## 📦 发布与打包

项目现在只保留一个正式发布入口：

```bash
cd /home/bob/projects/lab
./release.sh v2.1.2 "release: v2.1.2"
```

这一个脚本会串起完整发布链路：

1. 同步本地 `backend/data/client_bootstrap_secret.txt` 和 `client/bootstrap_secret.txt`
2. 同步 GitHub Actions secret `LAB_CLIENT_AUTH_SECRET`
3. 只提交允许进入客户端仓库的文件：`.github/`、`.gitignore`、`README.md`、`README_client.md`、`client/`
4. 推送到 GitHub，触发 Actions 打包 `Windows / Linux / macOS`
5. 等待 GitHub Release 生成后，把 `lab-windows.exe`、`lab-linux`、`lab-macos` 下载到 `landing_page/download/`
6. 同步代码和 tag 到 Gitee，并更新 Gitee Release 附件

不再保留其他发布脚本，所有发布、查状态、补下载、补 Gitee、同步 secret 都统一通过 `./release.sh` 完成。

---

## 📖 使用说明

### 服务端管理后台

浏览器访问：http://localhost:14114/

**功能模块：**
- 📊 **统计面板** - 实时显示在线客户端数、总客户端数、封禁数
- 👥 **客户端管理** - 查看所有客户端状态，支持封禁/解封操作
- 📢 **广播消息** - 向所有在线客户端发送广播，显示在客户端顶部走马灯
- 💬 **消息中心** - 查看和回复客户端消息

### 客户端界面

浏览器访问：http://localhost:8080/ (端口自动分配)

**功能模块：**
- 📄 **智能提取** - 上传文件自动提取标准号
- 🔍 **批量查询** - 多平台自动轮询查询标准信息
- ⬇️ **标准下载** - 双源下载（食品伙伴网优先，国标兜底国家标准平台在线阅读）
- 💬 **联系作者** - 与管理员实时沟通

---

## 🛠️ 安装部署

### 系统要求

| 项目 | 服务端要求 | 客户端要求 |
|-----|-----------|-----------|
| 操作系统 | Linux / Windows / macOS | Windows 7+ / Linux / macOS |
| 内存 | 1GB+ | 2GB+ |
| 磁盘空间 | 500MB | 1GB+ |
| Python | 3.8+ | 3.8+ |
| 网络 | 公网IP或内网穿透 | 能访问服务端 |

### 服务端详细部署

```bash
# 1. 克隆代码
git clone <repository-url>
cd lab/backend

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置（可选）
# 修改 server_hub.py 中的端口配置（默认14114）

# 5. 启动
python server_hub.py

# 6. 配置公网访问（推荐Cloudflare Tunnel）
# 安装 cloudflared
# 创建 tunnel 配置文件 cloudflared_lab.yml
# 启动: 使用 systemd user service `cloudflared-testmumu.service`（避免重复启动）
```

### 客户端详细部署

```bash
# 1. 进入客户端目录
cd lab/client

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置服务端地址
# 修改 app.py 中的 HUB_URL（默认连接本地服务端）

# 5. 启动
python app.py
```

---

## 🔒 安全特性（加密配置机制）

### 新架构：临时密钥 + 加密配置

**原理：**
1. 客户端启动时向服务端请求配置
2. 服务端返回 **加密配置（~4KB乱码）** + **临时密钥（5分钟有效）**
3. 客户端本地保存加密配置（无法直接使用）
4. 密钥保存在内存中，每30秒心跳续期
5. 心跳断了 → 密钥过期 → 配置失效 → 停止服务

**优势：**
- ✅ 配置本地是加密乱码，即使保存也无法使用
- ✅ 密钥必须通过心跳持续获取
- ✅ 服务端封禁只需停止心跳续期
- ✅ 5分钟后客户端自动停止运行
- ✅ 无需复杂的许可证验证系统
- ✅ 传输量大幅减少（80KB → 5KB）

### 封禁机制

```
服务端封禁客户端
    ↓
心跳不再返回新密钥
    ↓
客户端5分钟后密钥过期
    ↓
配置无法解密
    ↓
服务自动停止
```

---

## 📁 项目结构

```
lab/
├── README.md                 # 本文档
├── cloudflared_lab.yml       # Cloudflare Tunnel配置
│
├── backend/                  # 服务端
│   ├── server_hub.py         # 主程序（配置分发中心）
│   ├── config_crypto.py      # 配置加密模块
│   ├── static/               # 管理后台前端
│   │   └── index.html        # 管理界面
│   ├── distributed_code/     # 配置模板
│   │   ├── config_template.json  # 平台配置模板
│   │   ├── extractor.py      # 标准提取模块（内置）
│   │   ├── query_service.py  # 标准查询模块（内置）
│   │   └── downloader.py     # 标准下载模块（内置）
│   └── data/                 # 数据目录
│       ├── clients.json      # 客户端注册信息
│       ├── messages.json     # 消息记录
│       └── bans.json         # 封禁列表
│
└── client/                   # 客户端
    ├── app.py                # 主程序
    ├── extractor.py          # 提取器代码（内置）
    ├── query_service.py      # 查询服务代码（内置）
    ├── downloader.py         # 下载器代码（内置）
    ├── static/               # 客户端前端
    │   └── index.html        # 用户界面
    └── cache/                # 本地缓存
        └── config.enc        # 加密配置（乱码）
```

---

## 🔌 API 接口

### 服务端 API

| 接口 | 方法 | 说明 |
|-----|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/client/register` | POST | 客户端注册 |
| `/api/client/heartbeat` | POST | 客户端心跳（返回新密钥续期） |
| `/api/code/all` | POST | 获取加密配置和密钥 |
| `/api/admin/clients` | GET | 获取所有客户端 |
| `/api/admin/broadcast` | POST | 发送广播 |
| `/api/admin/clients/{id}/ban` | POST | 封禁客户端 |
| `/api/admin/clients/{id}/unban` | POST | 解封客户端 |
| `/api/admin/config/summary` | GET | 获取配置概览 |

### 客户端 API

| 接口 | 方法 | 说明 |
|-----|------|------|
| `/api/status` | GET | 获取客户端状态 |
| `/api/extract` | POST | 执行文件提取 |
| `/api/query` | POST | 执行标准查询 |
| `/api/download` | POST | 执行标准下载 |
| `/api/messages` | GET/POST | 消息接口 |

---

## 🧪 测试

```bash
# 测试服务端API
curl http://localhost:14114/api/health

# 测试客户端注册
curl -X POST http://localhost:14114/api/client/register \
  -d '{"client_id":"test123","mac":"00:11:22:33:44:55"}'

# 测试加密配置获取
curl -X POST http://localhost:14114/api/code/all \
  -d '{"client_id":"test123"}'

# 发送广播测试
curl -X POST http://localhost:14114/api/admin/broadcast \
  -d '{"message":"测试广播"}'
```

---

## 🐛 常见问题

### Q: 客户端无法连接到服务端？

A: 检查以下几点：
- 服务端是否已启动（`curl http://localhost:14114/api/health`）
- 客户端配置的 HUB_URL 是否正确
- 防火墙是否允许端口访问
- 如果使用域名，DNS解析是否正常

### Q: 为什么客户端需要持续联网？

A: 采用加密配置机制：
- 配置本地是加密乱码，必须联网获取密钥解密
- 密钥5分钟过期，需心跳续期
- 断网5分钟后自动失效，这是设计特性

### Q: 广播发送成功但客户端看不到？

A: 检查：
- 客户端是否在线（心跳正常）
- 客户端是否已刷新页面接收最新消息
- 浏览器控制台是否有JavaScript错误

### Q: 如何封禁恶意客户端？

A: 在管理后台的"👥 客户端管理"区域：
- 找到要封禁的客户端
- 点击"封禁"按钮
- 被封禁的客户端心跳不再获得密钥续期
- 5分钟后自动停止运行

### Q: 可以离线使用吗？

A: 不可以。本系统设计为必须持续联网，原因：
- 加密配置需要密钥才能使用
- 密钥必须通过心跳续期
- 这是为了服务端能够实时控制客户端使用权

---

## 📝 更新日志

### v2.1.0 (2026-03-26)
- ✅ **全新加密配置机制**：配置本地是乱码，需密钥解密
- ✅ **心跳续期系统**：每30秒续期密钥，5分钟过期
- ✅ **实时封禁控制**：停止心跳续期即可封禁
- ✅ **移除许可证系统**：简化架构，配置即许可
- ✅ **传输优化**：80KB代码包 → 5KB加密配置
- ✅ **管理界面配置管理模块**：实时显示配置状态和平台列表
- ✅ **清理版本控制代码**：移除强制升级功能

### v2.0.0 (2026-03-25)
- ✅ 全新分布式架构，支持多客户端
- ✅ 代码自动分发与热更新
- ✅ 广播消息系统
- ✅ 在线客服功能
- ✅ 客户端封禁管理
- ✅ Web管理后台

---

## 🤝 贡献

欢迎提交Issue和Pull Request。

---

## 📄 许可

本软件仅供内部使用。

---

<p align="center">
  <b>Lab 分布式检测工具箱 - 让标准查询更高效</b>
</p>
