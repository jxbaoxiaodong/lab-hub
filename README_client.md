# Lab 客户端仓库

> **注意**：这是客户端仓库，包含客户端代码。用户只需运行客户端即可使用系统，无需服务端代码。

> 服务端代码由管理员部署，用户无需关注。

---

## 快速开始

### 1. 下载源码

```bash
git clone https://github.com/jxbaoxiaodong/lab-hub.git
cd lab-hub
```

### 2. 安装依赖

```bash
cd client
pip install -r requirements.txt
```

### 3. 启动客户端

```bash
# Linux/macOS
python app.py

# Windows
python app.py
# 或双击 start.bat
```

客户端将自动连接默认服务端（需确保服务端已运行）。

### 4. 访问客户端

浏览器访问显示的本地地址（端口动态分配，如 http://127.0.0.1:xxxxx）

---

## 客户端功能

- **标准号提取**：从 PDF/Word/Excel/图片文件自动提取标准号
- **标准有效性查询**：查询标准的有效性状态（现行/废止）
- **标准电子版下载**：下载标准文档或获取在线阅读链接

---

## 系统要求

- Python 3.8+
- 操作系统：Windows / Linux / macOS
- 网络：需要持续联网（密钥续期机制）

---

## 详细说明

请参阅 [主仓库 README](../README.md) 了解更多详情。