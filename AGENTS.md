# Lab Hub 项目说明

## 项目概述

Lab Hub 是一个客户端-服务器系统，用于管理分布式客户端。客户端可以提取标准号、查询标准信息、下载标准文档。

**核心架构：加密配置 + 临时密钥机制**

---

## 架构

- **服务端** (`backend/server_hub.py`): FastAPI 应用，端口 14114
  - 管理客户端注册、心跳、封禁
  - 广播消息推送
  - **加密配置分发**（替代原代码分发）
  
- **客户端** (`client/app.py`): Flask 应用，动态端口
  - 标准号提取、查询、下载
  - 接收服务端广播
  - **密钥续期机制**（每30秒心跳续期）

---

## 启动命令

```bash
./start.sh
```

---

## 核心机制：加密配置 + 临时密钥

### 原理

1. **客户端启动**：向服务端请求配置
2. **服务端返回**：加密配置（~4KB乱码）+ 临时密钥（5分钟有效）
3. **客户端保存**：加密配置到本地（config.enc），**密钥仅在内存中**
4. **心跳续期**：每30秒心跳获取新密钥，刷新5分钟有效期
5. **失效机制**：心跳断了 → 密钥过期 → 配置无法解密 → 服务停止

### 封禁机制

```
服务端封禁客户端
    ↓
心跳不再返回新密钥
    ↓
5分钟后密钥过期
    ↓
配置无法解密
    ↓
客户端自动停止运行
```

### 优势

- ✅ 配置本地是加密乱码，即使保存也无法使用
- ✅ 密钥必须通过心跳持续获取
- ✅ 服务端实时控制客户端使用权
- ✅ 移除复杂的许可证系统
- ✅ 传输量大幅减少：80KB代码 → 5KB加密配置

---

## 主要功能

### 核心业务代码

业务代码**内置在客户端**，不再动态分发：

| 模块 | 文件 | 说明 |
|------|------|------|
| extractor | `client/extractor.py` | 标准号提取（内置） |
| query | `client/query_service.py` | 标准查询（内置，需Selenium） |
| download | `client/downloader.py` | 标准下载（内置） |

**配置动态分发**：`backend/config_crypto.py` 生成加密配置

### 配置文件

`backend/distributed_code/config_template.json` 包含：
- 查询平台配置（7个平台的URL、选择器）
- 下载平台配置（食品伙伴网、GBT等）
- 标准号提取规则（正则表达式）

### 标准号提取

- **PDF 文件**：逐页提取，记录页码
- **DOCX/DOC 文件**：自动转换为 PDF 后提取，记录页码
- **TXT 文件**：直接提取，无页码
- **CSV 导出**：包含标准号和所在页码

### 广播系统
- 管理面板发送广播，客户端走马灯显示
- 广播使用 `broadcast_id` 标识，支持批量删除同步
- 客户端每5秒轮询获取新广播和已删除广播列表

### 客户端管理
- 自动注册、心跳检测
- 支持禁用/解禁客户端
- MAC 地址上报
- **封禁后5分钟自动失效**

---

## 数据文件

- `backend/data/clients.json` - 客户端信息
- `backend/data/messages.json` - 消息记录
- `backend/data/deleted_broadcasts.json` - 已删除广播ID
- `backend/data/bans.json` - 封禁列表（**替代原licenses.json**）

---

## 备份

```bash
./backup.sh
```

备份目录：`/media/bob/System/LINUX_FILES/projects_backup`

---

## 已完成

1. 广播删除同步 - 使用 `broadcast_id` 标识
2. DOCX 文件支持 - 需安装 python-docx
3. MAC 地址上报 - 客户端注册时发送 MAC，服务端存储
4. 统计上传 - 客户端本地计数，心跳时上传累加到服务端
5. 下载功能 - 返回搜索链接（国家标准全文公开系统、工标网等4个源）
6. DOCX/DOC 页码支持 - 自动转PDF后提取，记录页码
7. CSV导出页码修复 - 正确显示标准号所在页码
8. 并行任务执行 - 提取、查询、下载可同时运行
9. 动态进度显示 - 显示当前处理的标准号和搜索源
10. 查询结果详细显示 - 成功/失败都显示完整信息和建议
11. 自动下载ChromeDriver - 首次使用自动安装，无需手动配置
12. 实际PDF下载功能 - 使用Playwright自动化下载真实PDF文件
13. 添加去重功能 - 提取结果按标准号去重，保留所有出现页码
14. OCR支持 - 添加rapidocr-onnxruntime支持扫描件PDF识别
15. 下载超时优化 - 添加120秒超时，避免卡住
16. OCR自动应用 - 自动检测扫描件，无需用户选择
17. 文件格式限制 - 仅支持PDF/Word/Excel/TXT/CSV/图片格式
18. 压缩包自动解压 - 自动处理ZIP文件
19. Excel支持 - 添加openpyxl、xlrd依赖
20. 图片OCR支持 - PNG/JPG自动OCR识别
21. 无标准号提示优化 - 显示识别内容摘要
22. 任务状态隔离 - 避免多任务显示混乱
23. 查询结果显示完整信息 - 显示所有字段包括标准编号、英文名、废止日期等
24. 查询结果CSV导出 - 支持导出查询结果
25. 任务终止功能 - 支持取消正在运行的任务
26. 文件提取+确认流程 - 查询/下载上传文件时自动提取标准号，弹出确认框
27. 界面布局重构 - 左侧导航栏+右侧操作区域
28. CSV格式验证 - 添加标识行检测，非Lab导出的CSV提示用户
29. 下载结果优化 - 显示文件路径，添加"打开文件夹"按钮
30. 查询源自动切换 - 当一个源无法连接时自动切换到下一个可用源
31. 新增查询平台 - 添加聊城、六安、厦门、深圳平台，共7个查询平台
32. 屏蔽代理 - 查询时添加`--proxy-server=direct://`参数避免代理问题
33. 文本直接查询支持 - 对于未提取到标准号的输入，直接使用输入关键词查询
34. 平台切换限制 - 连续3个平台查询无结果，就不再换平台
35. CSV文件提取支持修复 - 修复extractor.py中extract_from_file方法缺少CSV文件处理的问题
36. 食品伙伴网下载实现 - 作为首选下载源，直接下载PDF
37. GBT标准网下载实现 - 返回夸克网盘在线观看链接
38. 标准号解析修复 - 保留原始前缀(如GB/T)，避免丢失/T标识
39. 国家标准在线查看 - 返回国家标准全文公开系统详情页链接
40. 下载模块重构 - 先提取标准号再查询，结果以表格显示各平台状态和操作按钮
41. 国家标准在线阅读链接修复 - 访问详情页实时获取真正的在线阅读链接

### v2.1.0 新功能（2026-03-26）

42. **加密配置机制** - 配置本地是乱码，需密钥解密
43. **心跳续期系统** - 每30秒续期密钥，5分钟过期
44. **实时封禁控制** - 停止心跳续期即可封禁，5分钟后自动失效
45. **移除许可证系统** - 简化架构，配置即许可
46. **传输优化** - 80KB代码包 → 5KB加密配置
47. **业务代码内置** - 不再动态分发，客户端直接import
48. **广播消息修复** - 修复后台心跳线程消费消息导致前端收不到广播的问题
    - 后台心跳添加 `no_messages: True` 参数，只续期密钥不获取消息
    - 前端轮询添加 `no_messages: False` 参数，确保能获取到广播和管理员回复
49. **清理版本控制代码** - 移除客户端和管理界面的版本检查/强制升级功能
    - 删除 `upgrade_status` 全局变量、`check_upgrade()` 函数、`require_upgrade_check()` 装饰器
    - 删除 `/api/extract` 和 `do_download()` 中的版本检查调用
    - 管理界面移除版本统计卡片、强制升级控制面板、版本相关CSS样式和JavaScript函数
50. **管理界面新增配置管理模块** - 添加配置管理页面
    - 显示配置文件位置和工作模式说明
    - 实时统计卡片：查询平台数、下载平台数、提取规则数、最后更新时间
    - 标签形式显示所有启用的查询平台
    - 固定高度320px+滚动条，保持三栏布局整齐
    - 后端新增 `/api/admin/config/summary` API 接口

---

## 依赖安装

```bash
# 系统工具（DOC文件支持）
sudo apt-get install -y antiword catdoc libreoffice

# Python 依赖
pip install flask flask-cors waitress pdfplumber python-docx PyMuPDF beautifulsoup4 lxml playwright rapidocr-onnxruntime openpyxl xlrd
```

---

## 注意事项

- 客户端使用动态端口，不固定
- **客户端必须持续联网**，断网5分钟后自动失效
- 查询功能需要 Selenium + ChromeDriver
- DOCX/DOC 转 PDF 需要 LibreOffice (soffice)
- 当前代码版本: **无版本号机制**，客户端业务代码内置
- **加密配置**实时从服务端获取
- 配置文件位置: `backend/distributed_code/config_template.json`
- 最后更新: 2026-03-27
- 最新备份: `lab_20260327_124547.tar.gz`
- 最新修改: 
  - 移除版本号和许可证系统
  - 实施加密配置+临时密钥机制
  - 业务代码内置客户端
  - 封禁后5分钟自动失效
  - 清理所有版本控制代码残留
  - 新增管理界面配置管理模块
  - 下载结果添加"实际标准号"列（各平台查询到的真实标准号）
  - 修复下载模块 `import re` 局部导入导致的错误
  - 优化标准号提取逻辑，优先从页面标题获取
  - 消息中心改造成微信聊天风格布局
  - 管理后台IP访问控制（仅本机可访问管理界面）
  - 心跳/注册接口添加User-Agent验证，只允许Jingxi客户端
  - 管理后台可修改密钥有效期
  - 客户端注册防重复机制（新客户端才能注册）
  - 修复CSV文件上传错误：`'ExtractedStandard' object has no attribute 'standard'`
  - 添加置信度显示功能：前端显示标准号提取置信度（0-1，颜色编码：绿/橙/红）
  - 更新文件格式支持描述：支持PDF、Word、Excel、TXT、CSV、图片
  - 验证Excel文件支持：`.xlsx`（openpyxl）和`.xls`（xlrd）均可正常工作
  - 修复前端文件上传控件`accept`属性和描述信息
  - **新增"开发中"页面**：左侧导航栏添加"开发中"菜单，显示5个即将上线的功能（标准下载源增强、QC图监控系统、检测计量招投标信息采集发布系统、CMA CNAS评审员题库及智能问答系统、不确定度评估系统）
  - 修复HTML结构问题：开发中section被错误嵌套在下载section内部，导致页面显示为空白

---

## 待完成

### 🚀 Lab 项目发布计划 (2026-03-27)

#### 阶段一：创建 GitHub 仓库
- [x] 初始化 Git 仓库
- [x] 创建 GitHub 仓库 (jxbaoxiaodong/lab-hub)
- [x] 首次推送代码

#### 阶段二：客户端打包
- [x] 参考 mumu 的 `build_client.sh` 创建 lab 的打包脚本
- [x] 创建 `.github/workflows/build-release.yml` 用于 GitHub Actions
- [x] GitHub Actions 成功构建 v2.1.1，3个平台全部成功

#### 阶段三：创建 Release
- [x] GitHub Release 创建成功
- [x] 下载安装包到本地 (`landing_page/download/`)：lab-linux (118M), lab-macos (96M), lab-windows.exe (90M)
- [x] 推送到 Gitee

#### 阶段四：发布 Landing 网页
- [x] 确认 Landing 网页可以访问
- [x] 配置 Cloudflare Tunnel（使用 combined 隧道，labmumu.ftir.fun 指向 7832 端口）

---

#### Landing 网页状态
- `/home/bob/projects/lab/landing_page/index.html` - 已完善
- 功能介绍、正在开发的功能、使用方法、系统要求
- 下载目录已包含安装包：lab-linux, lab-macos, lab-windows.exe

#### 关键配置
- GitHub 仓库: `jxbaoxiaodong/lab-hub`
- Gitee 仓库: `baoxiaodong1/lab-hub`
- Landing 域名: `labmumu.ftir.fun`
- Release版本: `v2.1.1`
- Release文件: lab-linux (118M), lab-macos (96M), lab-windows.exe (90M)

---

#### 虚拟环境合并
- 原架构：backend/venv + client/venv
- 新架构：根目录单个venv
- 修改文件：`start.sh` - 改为使用根目录venv
- 问题修复：根目录venv配置损坏，使用--break-system-packages安装依赖到用户目录

#### start.sh改进
- 增加更长的等待时间（5秒）获取客户端端口
- 改进正则表达式匹配，支持python和python3
- 增加检查*端口的逻辑
- 保留三种获取端口的方法作为备选

---

#### 上传文件清单（仅客户端）

**需要上传到 GitHub 的文件：**

| 文件/目录 | 说明 |
|-----------|------|
| `client/app.py` | 客户端主程序 |
| `client/extractor.py` | 标准号提取模块 |
| `client/query_service.py` | 标准查询服务模块 |
| `client/downloader.py` | 标准下载模块 |
| `client/requirements.txt` | Python 依赖 |
| `client/start.bat` | Windows 启动脚本 |
| `client/lab_client.spec` | PyInstaller 配置 |
| `client/static/` | 前端文件（css/js/html） |
| `README_client.md` | 客户端使用说明 |

**不上传（排除）的文件：**

| 文件/目录 | 原因 |
|-----------|------|
| `backend/` | 服务端代码，由管理员部署 |
| `landing_page/` | Landing页面，服务器专用 |
| `cloudflared_lab.yml` | 服务器 Tunnel 配置 |
| `landing_tunnel.yml` | Landing Tunnel 配置 |
| `start.sh` | 服务端启动脚本 |
| `backup.sh/backup.py` | 备份脚本 |
| `release.sh` | 发布脚本 |
| `AGENTS.md` | 代理工作文档 |
| `docs/` | 开发文档 |
| `tools/` | 开发工具 |
| `drivers/` | 驱动目录 |
| `frontend/` | 旧前端代码 |
| `test_*.py` | 测试文件 |
| `*.png` | 测试图片 |
| `client/venv/` | 虚拟环境 |
| `client/cache/` | 运行缓存 |
| `client/downloads/` | 下载目录 |
| `client/__pycache__/` | Python缓存 |
