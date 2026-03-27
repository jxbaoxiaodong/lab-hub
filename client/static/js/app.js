// Jingxi 检测工具箱 - 离线JavaScript

// 全局状态
let currentTask = null;
let progressInterval = null;
let queryMode = 'text'; // 'file' 或 'text'
let extractResults = []; // 存储提取结果用于导出
let queryResults = []; // 存储查询结果用于导出
let downloadResults = []; // 存储下载结果用于分页
let downloadPagination = { page: 1, perPage: 5, total: 0 }; // 下载分页状态
let broadcasts = []; // 广播消息列表
let broadcastIndex = 0; // 当前广播索引
const BROADCAST_STORAGE_KEY = 'broadcast_history';

// 模拟数据开关 - 设置为 true 启用模拟数据
const ENABLE_MOCK_DATA = false;

// 任务状态管理 - 简化版本
let taskStates = {
    extract: { status: 'idle', lastStatus: 'idle' },
    query: { status: 'idle', lastStatus: 'idle' },
    download: { status: 'idle', lastStatus: 'idle' }
};

// 从localStorage加载广播历史
function loadBroadcastHistory() {
    const stored = localStorage.getItem(BROADCAST_STORAGE_KEY);
    if (stored) {
        try {
            broadcasts = JSON.parse(stored);
            // 只保留最近24小时的广播
            const now = new Date().getTime();
            broadcasts = broadcasts.filter(b => {
                const msgTime = new Date(b.created_at || b.time || 0).getTime();
                return (now - msgTime) < 24 * 60 * 60 * 1000;
            });
            if (broadcasts.length > 0) {
                updateBroadcastDisplay();
            }
        } catch (e) {
            broadcasts = [];
        }
    }
}

function removeDeletedBroadcasts(deletedIds) {
    console.log('[调试] 处理已删除的广播:', deletedIds);
    
    const beforeCount = broadcasts.length;
    broadcasts = broadcasts.filter(b => {
        const bid = b.broadcast_id || b.id;
        return !deletedIds.includes(bid);
    });
    const afterCount = broadcasts.length;
    
    if (beforeCount !== afterCount) {
        console.log(`[调试] 移除了 ${beforeCount - afterCount} 条广播`);
        
        localStorage.setItem(BROADCAST_STORAGE_KEY, JSON.stringify(broadcasts));
        
        if (broadcasts.length === 0) {
            const textEl = document.getElementById('broadcastText');
            if (textEl) {
                textEl.textContent = '欢迎使用 Jingxi 检测工具箱';
            }
        } else {
            updateBroadcastDisplay();
        }
    }
}

// 保存广播到localStorage
function saveBroadcast(msg) {
    let stored = [];
    const data = localStorage.getItem(BROADCAST_STORAGE_KEY);
    if (data) {
        try {
            stored = JSON.parse(data);
        } catch (e) {}
    }
    const msgId = msg.broadcast_id || msg.id;
    if (!stored.find(b => (b.broadcast_id || b.id) === msgId)) {
        stored.push(msg);
        if (stored.length > 50) {
            stored = stored.slice(-50);
        }
        localStorage.setItem(BROADCAST_STORAGE_KEY, JSON.stringify(stored));
    }
}

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    loadStatus();
    loadBroadcastHistory(); // 加载历史广播
    loadChatHistory(); // 加载历史消息
    loadInitialMessages(); // 从服务器加载最新消息
    startMessagePolling();
    setupDragDrop();
    setupQueryFileUpload();
});

// 启动时从服务器加载消息
async function loadInitialMessages() {
    try {
        const res = await fetch('/api/messages');
        const data = await res.json();
        
        console.log('[调试] 获取到的消息:', data);
        
        if (data.success && data.data) {
            let hasNewBroadcast = false;
            data.data.forEach(msg => {
                console.log('[调试] 消息详情:', msg);
                if (msg.type === 'broadcast') {
                    // 广播消息
                    console.log('[调试] 发现广播消息:', msg.content);
                    if (!broadcasts.find(b => b.id === msg.id)) {
                        broadcasts.push(msg);
                        saveBroadcast(msg);
                        hasNewBroadcast = true;
                    }
                } else if (msg.from === 'server' || msg.from === 'system') {
                    // 点对点消息
                    addChatMessage('server', msg.content, msg.id);
                }
            });
            console.log('[调试] 广播列表:', broadcasts);
            if (hasNewBroadcast) {
                updateBroadcastDisplay();
            }
        }
        
        // 处理已删除的广播
        if (data.deleted_broadcasts && data.deleted_broadcasts.length > 0) {
            removeDeletedBroadcasts(data.deleted_broadcasts);
        }
    } catch (e) {
        console.error('加载初始消息失败:', e);
    }
}

// 设置查询文件上传
function setupQueryFileUpload() {
    const fileInput = document.getElementById('queryFile');
    const fileName = document.getElementById('queryFileName');
    
    if (!fileInput) return;
    
    fileInput.addEventListener('change', function(e) {
        if (e.target.files.length > 0) {
            fileName.textContent = `已选择: ${e.target.files[0].name}`;
            // 读取文件内容
            const reader = new FileReader();
            reader.onload = function(e) {
                document.getElementById('queryInput').value = e.target.result;
            };
            reader.readAsText(e.target.files[0]);
        }
    });
}

// 切换查询模式
// 切换显示区块
function showSection(section) {
    console.log('showSection called:', section);
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    const navItem = document.querySelector(`.nav-item[data-section="${section}"]`);
    if (navItem) navItem.classList.add('active');
    
    document.querySelectorAll('.section').forEach(sec => {
        sec.style.display = 'none';
    });
    const targetSection = document.getElementById(`section-${section}`);
    if (targetSection) {
        targetSection.style.display = 'block';
        console.log('Section shown:', section);
    } else {
        console.log('Section not found:', `section-${section}`);
    }
}

window.showSection = showSection;

function setQueryMode(mode) {
    queryMode = mode;
    document.getElementById('btnFileMode').classList.toggle('active', mode === 'file');
    document.getElementById('btnTextMode').classList.toggle('active', mode === 'text');
    
    const queryFileZone = document.getElementById('queryFileZone');
    const queryTextArea = document.getElementById('queryTextArea');
    
    if (queryFileZone) queryFileZone.style.display = mode === 'file' ? 'block' : 'none';
    if (queryTextArea) queryTextArea.style.display = mode === 'text' ? 'block' : 'none';
}

// 拖拽上传设置
function setupDragDrop() {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('extractFile');

    if (!fileInput || !dropZone) {
        console.error('文件上传组件未找到');
        return;
    }

    // 移除旧的事件监听器（如果存在）
    const newFileInput = fileInput.cloneNode(true);
    fileInput.parentNode.replaceChild(newFileInput, fileInput);

    newFileInput.addEventListener('change', function(e) {
        if (e.target.files.length > 0) {
            const selectedFile = e.target.files[0];
            // 显示文件名但不移除文件输入元素
            dropZone.innerHTML = `
                <input type="file" id="extractFile" accept=".pdf,.doc,.docx,.txt,.csv,.xlsx,.xls,.jpeg,.jpg,.png" style="display: none;">
                <div class="file-upload-icon">✓</div>
                <div style="font-size: 0.875rem; color: var(--success);">${selectedFile.name}</div>
                <div style="font-size: 0.75rem; color: var(--text-secondary);">${(selectedFile.size/1024).toFixed(1)} KB</div>
                <button class="file-reselect-btn" onclick="clearFileSelection()">重新选择</button>
            `;
            // 将文件对象保存在全局变量中
            window.selectedFileForExtract = selectedFile;
        }
    });
}

// 清除文件选择
function clearFileSelection() {
    const dropZone = document.getElementById('dropZone');
    if (dropZone) {
        dropZone.innerHTML = `
            <input type="file" id="extractFile" accept=".pdf,.doc,.docx,.txt,.csv,.xlsx,.xls,.jpeg,.jpg,.png">
            <div class="file-upload-icon">📎</div>
            <div style="font-size: 0.875rem;">点击或拖拽文件到此处</div>
            <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">支持 PDF、Word、Excel、TXT、CSV、图片</div>
        `;
        setupDragDrop();
        window.selectedFileForExtract = null;
    }
}

// 加载状态
async function loadStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        if (data.success) {
            const clientIdEl = document.getElementById('clientId');
            const codeVersionEl = document.getElementById('codeVersion');
            const homeUrlEl = document.getElementById('homeUrl');
            
            if (clientIdEl) clientIdEl.textContent = data.data.client_id.substring(0, 12) + '...';
            if (codeVersionEl) codeVersionEl.textContent = 'linhb112233';
            if (homeUrlEl) homeUrlEl.textContent = 'labmumu.ftir.fun';
            // 不再显示服务端地址，服务端只用于心跳
        }
    } catch (e) {
        console.error('加载状态失败:', e);
    }
}

// 开始提取
async function startExtract() {
    // 优先使用全局变量中的文件对象
    let file = window.selectedFileForExtract;
    let fileInput = null;
    
    // 如果没有全局文件对象，尝试从DOM获取
    if (!file) {
        fileInput = document.getElementById('extractFile');
        if (!fileInput) {
            alert('文件上传组件未初始化，请刷新页面');
            return;
        }
        file = fileInput.files[0];
    }
    
    if (!file) {
        alert('请先选择文件');
        return;
    }

    const formData = new FormData();
    formData.append('file', file);
    
    // OCR自动启用
    formData.append('enable_ocr', 'true');

    resetResult('extract');
    document.getElementById('extractProgress').classList.add('active');
    const extractBtn = document.getElementById('extractBtn');
    extractBtn.disabled = true;
    extractBtn.textContent = '提取中...';
    document.getElementById('extractCancelBtn').style.display = 'inline-block';

    currentTask = 'extract';
    startProgressTracking();

    try {
        const res = await fetch('/api/extract', { method: 'POST', body: formData });
        const data = await res.json();
        if (!data.success) {
            if (checkUpgradeResponse(data)) {
                resetButton('extract');
                return;
            }
            showError('extract', data.message);
        }
    } catch (e) {
        showError('extract', '启动失败: ' + e.message);
    }
}

// 导出CSV
function exportCSV() {
    if (extractResults.length === 0) {
        alert('没有可导出的数据');
        return;
    }

    // 构建CSV内容 - 添加标识行
    let csv = '# LAB_EXTRACT_CSV\n';
    csv += '标准号,来源页码,置信度\n';
    extractResults.forEach(item => {
        const pages = item.pages ? item.pages.join(';') : '-';
        const confidence = item.confidence || 'high';
        csv += `${item.standard},"${pages}",${confidence}\n`;
    });

    // 获取源文件名
    let exportName = '标准号提取';
    const sourceFile = window.selectedFileForExtract;
    if (sourceFile && sourceFile.name) {
        // 移除源文件后缀，添加_extract.csv
        const baseName = sourceFile.name.replace(/\.[^.]+$/, '');
        const ext = sourceFile.name.split('.').pop();
        exportName = `${baseName}_${ext}`;
    }

    // 下载
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${exportName}.csv`;
    link.click();
}

// 验证CSV是否为Lab提取模块导出的文件
function validateLabCSV(text) {
    const lines = text.split('\n');
    let firstLine = lines[0].trim();
    // 移除BOM字符
    if (firstLine.charCodeAt(0) === 0xFEFF) {
        firstLine = firstLine.slice(1);
    }
    
    // 检查标识行
    if (firstLine !== '# LAB_EXTRACT_CSV') {
        return { 
            valid: false, 
            message: '请上传经过"提取标准号"模块处理后输出的CSV文件' 
        };
    }
    
    // 检查列名（第二行）
    if (lines.length < 2) {
        return { 
            valid: false, 
            message: 'CSV文件格式错误' 
        };
    }
    let headerLine = lines[1].trim();
    if (headerLine.charCodeAt(0) === 0xFEFF) {
        headerLine = headerLine.slice(1);
    }
    // 检查3列：标准号、页码、置信度
    if (!headerLine.includes('标准号') || !headerLine.includes('页码') || !headerLine.includes('置信度')) {
        return { 
            valid: false, 
            message: 'CSV文件缺少置信度列，请重新从提取模块导出' 
        };
    }
    
    return { valid: true };
}

// 导出查询CSV
function exportQueryCSV() {
    if (queryResults.length === 0) {
        alert('没有可导出的数据');
        return;
    }

    // 构建CSV内容 - 新格式：输入关键词,提取标准号,标准编号,中文名,英文名,状态,发布日期,实施日期,废止日期,采用标准,引用标准,被替代标准,替代标准,参考依据,标准摘要,来源,查询状态,错误信息
    let csv = '输入关键词,提取标准号,标准编号,中文名,英文名,状态,发布日期,实施日期,废止日期,采用标准,引用标准,被替代标准,替代标准,参考依据,标准摘要,来源,查询状态,错误信息\n';
    
    queryResults.forEach(item => {
        const inputKeyword = item.input_keyword || item.standard || '';
        const extractedStandard = item.extracted_standard || '';
        const status = item.status || '';
        const errorMsg = item.error || '';
        
        if (item.status === 'success' && item.data) {
            const d = item.data;
            csv += `"${inputKeyword.replace(/"/g, '""')}","${extractedStandard.replace(/"/g, '""')}","${d.standard_number || ''}","${(d.chinese_name || '').replace(/"/g, '""')}","${(d.english_name || '').replace(/"/g, '""')}","${d.standard_status || ''}","${d.release_date || ''}","${d.implementation_date || ''}","${d.cancellation_date || ''}","${(d.adopt_standard || '').replace(/"/g, '""')}","${(d.reference_standard || '').replace(/"/g, '""')}","${(d.replaced_standard || '').replace(/"/g, '""')}","${(d.replacing_standard || '').replace(/"/g, '""')}","${(d.reference_basis || '').replace(/"/g, '""')}","${(d.standard_summary || '').replace(/"/g, '""')}","${d.resource || ''}","${status}","${errorMsg.replace(/"/g, '""')}"\n`;
        } else if (item.status === 'skipped') {
            // 未提取到标准号的情况
            csv += `"${inputKeyword.replace(/"/g, '""')}","","","","","","","","","","","","","","","","${status}","${errorMsg.replace(/"/g, '""')}"\n`;
        } else if (item.status === 'error') {
            // 查询失败的情况
            csv += `"${inputKeyword.replace(/"/g, '""')}","${extractedStandard.replace(/"/g, '""')}","","","","","","","","","","","","","","","${status}","${errorMsg.replace(/"/g, '""')}"\n`;
        }
    });

    // 下载
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `标准查询_${new Date().toLocaleDateString()}.csv`;
    link.click();
}

// 清空结果
function clearResults() {
    extractResults = [];
    const extractResultEl = document.getElementById('extractResult');
    const extractToolbarEl = document.getElementById('extractToolbar');
    
    if (extractResultEl) extractResultEl.innerHTML = '';
    if (extractToolbarEl) extractToolbarEl.style.display = 'none';
    
    clearFileSelection();
}

// 开始查询
async function startQuery() {
    const input = document.getElementById('queryInput');
    if (!input) {
        alert('查询输入框未找到');
        return;
    }
    
    let standardList = [];
    
    // 检查是文件模式还是文本模式
    if (queryMode === 'file') {
        const fileInput = document.getElementById('queryFile');
        if (!fileInput || !fileInput.files[0]) {
            alert('请上传CSV文件');
            return;
        }
        
        // 读取CSV文件
        const file = fileInput.files[0];
        try {
            const text = await file.text();
            
            // 验证CSV格式
            const validation = validateLabCSV(text);
            if (!validation.valid) {
                alert(validation.message);
                return;
            }
            
            // 解析CSV，提取标准号（跳过表头和非标准号行）
            const lines = text.split('\n');
            for (let i = 0; i < lines.length; i++) {
                const line = lines[i].trim();
                if (!line) continue;
                // 跳过标识行和表头行
                if (line.startsWith('#')) continue;
                if (line.includes('标准号') && line.includes('页码') && line.includes('置信度')) continue;
                // CSV格式: 标准号,页码,置信度 (3列)
                const parts = line.split(',');
                if (parts.length >= 2) {
                    const std = parts[0].trim().replace(/^"|"$/g, '');
                    if (std && !std.includes('标准号')) {
                        standardList.push(std);
                    }
                } else if (line.length > 3) {
                    // 直接一行一个标准号
                    const std = line.replace(/^"|"$/g, '').trim();
                    if (std && !std.includes('标准号')) {
                        standardList.push(std);
                    }
                }
            }
        } catch (e) {
            alert('读取文件失败: ' + e.message);
            return;
        }
    } else {
        // 文本模式
        const standards = input.value.trim();
        if (!standards) {
            alert('请输入标准号');
            return;
        }
        standardList = standards.split('\n').map(s => s.trim()).filter(s => s);
    }

    if (standardList.length === 0) {
        alert('未找到有效的标准号');
        return;
    }

    resetResult('query');
    document.getElementById('queryProgress').classList.add('active');
    const queryBtn = document.getElementById('queryBtn');
    queryBtn.disabled = true;
    queryBtn.textContent = '查询中...';
    document.getElementById('queryCancelBtn').style.display = 'inline-block';

    currentTask = 'query';
    startProgressTracking();

    try {
        const res = await fetch('/api/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ standards: standardList })
        });
        const data = await res.json();
        if (!data.success) {
            if (checkUpgradeResponse(data)) {
                resetButton('query');
                return;
            }
            showError('query', data.message);
        }
    } catch (e) {
        showError('query', '启动失败: ' + e.message);
    }
}

// 开始下载
async function startDownload() {
    let standardList = [];
    
    // 检查是文件模式还是文本模式
    if (downloadMode === 'file') {
        const fileInput = document.getElementById('downloadFile');
        if (!fileInput || !fileInput.files[0]) {
            alert('请上传CSV文件');
            return;
        }
        
        const file = fileInput.files[0];
        try {
            const text = await file.text();
            
            // 验证CSV格式
            const validation = validateLabCSV(text);
            if (!validation.valid) {
                alert(validation.message);
                return;
            }
            
            const lines = text.split('\n');
            for (let i = 0; i < lines.length; i++) {
                const line = lines[i].trim();
                if (!line) continue;
                // 跳过标识行和表头行
                if (line.startsWith('#')) continue;
                if (line.includes('标准号') && line.includes('页码') && line.includes('置信度')) continue;
                const parts = line.split(',');
                if (parts.length >= 2) {
                    const std = parts[0].trim().replace(/^"|"$/g, '');
                    if (std && !std.includes('标准号')) {
                        standardList.push(std);
                    }
                } else if (line.length > 3) {
                    const std = line.replace(/^"|"$/g, '').trim();
                    if (std && !std.includes('标准号')) {
                        standardList.push(std);
                    }
                }
            }
        } catch (e) {
            alert('读取文件失败: ' + e.message);
            return;
        }
    } else {
        const input = document.getElementById('downloadInput');
        if (!input) {
            alert('下载输入框未找到');
            return;
        }
        
        const standards = input.value.trim();
        if (!standards) {
            alert('请输入标准号');
            return;
        }
        standardList = standards.split('\n').map(s => s.trim()).filter(s => s);
    }

    if (standardList.length === 0) {
        alert('未找到有效的标准号');
        return;
    }

    resetResult('download');
    document.getElementById('downloadProgress').classList.add('active');
    const downloadBtn = document.getElementById('downloadBtn');
    downloadBtn.disabled = true;
    downloadBtn.textContent = '下载中...';
    document.getElementById('downloadCancelBtn').style.display = 'inline-block';

    currentTask = 'download';
    startProgressTracking();

    // 模拟数据模式 - 用于测试分页效果
    if (ENABLE_MOCK_DATA) {
        console.log('[模拟数据] 生成测试数据...');
        setTimeout(() => {
            const mockData = generateMockDownloadData(25); // 生成25条模拟数据
            downloadResults = mockData;
            downloadPagination.total = mockData.length;
            downloadPagination.page = 1;
            renderDownloadResults();
            resetButton('download');
        }, 1000);
        return;
    }
    
    try {
        const res = await fetch('/api/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ standards: standardList })
        });
        const data = await res.json();
        if (!data.success) {
            if (checkUpgradeResponse(data)) {
                resetButton('download');
                return;
            }
            showError('download', data.message);
        }
    } catch (e) {
        showError('download', '启动失败: ' + e.message);
    }
}

// 切换下载模式
let downloadMode = 'text';
function setDownloadMode(mode) {
    downloadMode = mode;
    document.getElementById('btnDownloadFileMode').classList.toggle('active', mode === 'file');
    document.getElementById('btnDownloadTextMode').classList.toggle('active', mode === 'text');
    
    document.getElementById('downloadFileZone').style.display = mode === 'file' ? 'block' : 'none';
    document.getElementById('downloadTextZone').style.display = mode === 'text' ? 'block' : 'none';
}

window.setDownloadMode = setDownloadMode;

// 进度跟踪 - 简化版本
function startProgressTracking() {
    if (progressInterval) clearInterval(progressInterval);
    
    progressInterval = setInterval(async () => {
        try {
            // 获取进度信息
            const res = await fetch('/api/progress');
            const data = await res.json();
            
            if (data.success && data.data.message) {
                updateProgress(data.data);
            }
            
            // 获取任务状态
            const statusRes = await fetch('/api/status');
            const statusData = await statusRes.json();
            
            if (statusData.success && statusData.data.current_tasks) {
                const tasks = statusData.data.current_tasks;
                const runningTypes = [];
                
                // 处理每个任务类型
                ['extract', 'query', 'download'].forEach(type => {
                    const task = tasks[type];
                    const currentState = taskStates[type];
                    
                    if (!task) return;
                    
                    // 记录当前状态
                    currentState.status = task.status || 'idle';
                    
                    // 如果状态发生变化
                    if (currentState.status !== currentState.lastStatus) {
                        console.log(`[状态变化] ${type}: ${currentState.lastStatus} -> ${currentState.status}`);
                        
                        // 根据新状态更新UI
                        switch(currentState.status) {
                            case 'running':
                                runningTypes.push(type);
                                // 显示取消按钮
                                const cancelBtn = document.getElementById(type + 'CancelBtn');
                                if (cancelBtn) cancelBtn.style.display = 'inline-block';
                                // 禁用开始按钮
                                const startBtn = document.getElementById(type + 'Btn');
                                if (startBtn) startBtn.disabled = true;
                                // 显示进度条
                                const progressEl = document.getElementById(type + 'Progress');
                                if (progressEl) progressEl.classList.add('active');
                                break;
                                
                            case 'completed':
                                // 显示结果
                                showResult(type, task.result, task.message);
                                // 重置按钮状态
                                resetButton(type);
                                break;
                                
                            case 'error':
                                // 显示错误
                                showError(type, task.message || task.error || '未知错误');
                                // 重置按钮状态
                                resetButton(type);
                                break;
                                
                            case 'cancelled':
                                // 显示取消信息
                                showError(type, '任务已终止');
                                // 重置按钮状态
                                resetButton(type);
                                break;
                                
                            case 'idle':
                                // 重置按钮状态
                                resetButton(type);
                                break;
                        }
                        
                        // 更新最后状态
                        currentState.lastStatus = currentState.status;
                    }
                });
                
                // 更新任务状态显示
                const taskStatusEl = document.getElementById('taskStatus');
                if (taskStatusEl) {
                    taskStatusEl.textContent = runningTypes.length > 0 ? `${runningTypes.length}个任务运行中` : '就绪';
                }
            }
        } catch (e) {
            console.error('进度跟踪失败:', e);
        }
    }, 800);
}

// 更新进度
function updateProgress(data) {
    const type = data.task_type;
    const percent = data.percentage || 0;
    
    const messageEl = document.getElementById(type + 'Message');
    const percentEl = document.getElementById(type + 'Percent');
    const barEl = document.getElementById(type + 'Bar');
    const detailEl = document.getElementById(type + 'Detail');
    
    if (type === 'extract') {
        if (messageEl) messageEl.textContent = data.message || '处理中...';
        if (percentEl) percentEl.textContent = percent + '%';
        if (barEl) barEl.style.width = percent + '%';
        
        let detail = '';
        if (data.details?.page && data.details?.total_pages) {
            detail = `第 ${data.details.page} / ${data.details.total_pages} 页`;
        }
        if (detailEl) {
            detailEl.textContent = detail;
            detailEl.style.cssText = 'font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.5rem; font-family: monospace;';
        }
    } else {
        let statusText = '正在处理中，请稍候，当前动作：';
        let action = data.message || '处理中...';
        
        if (data.details?.standard) {
            action = `处理标准: ${data.details.standard}`;
        }
        if (data.details?.platform) {
            const platformNames = {
                'hunan': '湖南省标准平台',
                'shenzhen': '深圳市标准平台',
                'shanxi': '陕西省标准平台',
                'jiangxi': '江西省标准平台'
            };
            const platformName = platformNames[data.details.platform] || data.details.platform;
            if (data.details?.next_platform) {
                const nextName = platformNames[data.details.next_platform] || data.details.next_platform;
                action = `${platformName}连接失败，切换到${nextName}`;
            } else {
                action = `使用${platformName}查询: ${data.details.standard || ''}`;
            }
        }
        if (data.details?.source) {
            action = `尝试下载源: ${data.details.source}`;
            if (data.details?.standard) action += ` (${data.details.standard})`;
        }
        if (data.details?.current && data.details?.total) {
            action = `[${data.details.current}/${data.details.total}] ${data.details.standard || ''}`;
        }
        
        if (detailEl) {
            detailEl.textContent = statusText + action;
            detailEl.style.cssText = 'font-size: 0.9rem; color: var(--text-primary); margin-top: 0;';
        }
    }
}

// 显示结果
function showResult(type, result, message) {
    const container = document.getElementById(type + 'Result');
    if (!container) return;
    
    container.innerHTML = '';
    
    if (type === 'extract' && Array.isArray(result)) {
        // 存储结果用于导出
        extractResults = result;
        
        // 去重并显示
        const seen = new Map();
        result.forEach(item => {
            const std = item.standard || item.text;
            if (!seen.has(std)) {
                seen.set(std, { ...item, pages: [item.page || '-'] });
            } else {
                seen.get(std).pages.push(item.page || '-');
            }
        });
        
        const uniqueResults = Array.from(seen.values());
        
        if (uniqueResults.length === 0) {
            // 获取文件内容摘要
            fetch('/api/extract/text')
                .then(r => r.json())
                .then(data => {
                    let contentPreview = '';
                    if (data.success && data.text && data.text.length > 0) {
                        contentPreview = data.text.substring(0, 30).replace(/\s+/g, ' ').trim();
                    }
                    if (contentPreview) {
                        container.innerHTML = `<div class="empty-state">提取到: "${contentPreview}..."<br>未提取到任何标准号</div>`;
                    } else {
                        container.innerHTML = '<div class="empty-state">未提取到任何标准号</div>';
                    }
                })
                .catch(() => {
                    container.innerHTML = '<div class="empty-state">未提取到任何标准号</div>';
                });
        } else {
            uniqueResults.forEach(item => {
                const div = document.createElement('div');
                div.className = 'result-item';
                
                // 获取置信度
                const confidence = item.confidence || 1.0;
                let confidenceDisplay = '';
                let confidenceColor = '';
                
                if (confidence >= 0.9) {
                    confidenceDisplay = '高';
                    confidenceColor = '#10b981'; // 绿色
                } else if (confidence >= 0.7) {
                    confidenceDisplay = '中';
                    confidenceColor = '#f59e0b'; // 橙色
                } else {
                    confidenceDisplay = '低';
                    confidenceColor = '#ef4444'; // 红色
                }
                
                // 显示标准号和置信度
                div.innerHTML = `
                    <div style="display: flex; align-items: center; justify-content: space-between; width: 100%;">
                        <span>${item.standard || item.text}</span>
                        <span style="font-size: 0.85rem; padding: 2px 6px; border-radius: 12px; background-color: ${confidenceColor}10; color: ${confidenceColor}; border: 1px solid ${confidenceColor}30;">
                            置信度: ${confidenceDisplay} (${(confidence * 100).toFixed(0)}%)
                        </span>
                    </div>
                `;
                container.appendChild(div);
            });
            
            const extractToolbarEl = document.getElementById('extractToolbar');
            if (extractToolbarEl) extractToolbarEl.style.display = 'flex';
        }
    } else if (Array.isArray(result)) {
        // 存储查询结果用于导出
        if (type === 'query') {
            queryResults = result;
            const queryToolbarEl = document.getElementById('queryToolbar');
            if (queryToolbarEl) queryToolbarEl.style.display = 'flex';
        }
        
        // 下载结果使用分页显示
        if (type === 'download') {
            downloadResults = result;
            downloadPagination.total = result.length;
            downloadPagination.page = 1;
            renderDownloadResults();
            return;
        }
        
        // 查询结果 - 显示详细信息
        result.forEach(item => {
            // 获取输入关键词和提取的标准号
            const inputKeyword = item.input_keyword || item.standard || '';
            const extractedStandard = item.extracted_standard || '';
            const standard = extractedStandard || inputKeyword;
            
            const isSuccess = item.status === 'success';
            const isSkipped = item.status === 'skipped';
            
            const div = document.createElement('div');
            div.className = 'result-item';
            // 根据状态设置颜色
            let borderColor = '#ef4444'; // 默认红色
            let bgColor = 'rgba(239, 68, 68, 0.05)';
            let statusText = '✗ 失败';
            let statusColor = '#ef4444';
            
            if (isSuccess) {
                borderColor = '#10b981';
                bgColor = 'rgba(16, 185, 129, 0.05)';
                statusText = '✓ 成功';
                statusColor = '#10b981';
            } else if (isSkipped) {
                borderColor = '#f59e0b';
                bgColor = 'rgba(245, 158, 11, 0.05)';
                statusText = '⚠ 跳过';
                statusColor = '#f59e0b';
            }
            
            div.style.cssText = `
                display: flex;
                flex-direction: column;
                padding: 1rem;
                margin-bottom: 0.75rem;
                border-radius: 8px;
                border-left: 4px solid ${borderColor};
                background: ${bgColor};
            `;
            
            // 标题行：标准号 + 状态
            const titleRow = document.createElement('div');
            titleRow.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;';
            
            let titleText = standard;
            if (inputKeyword !== extractedStandard && extractedStandard) {
                titleText = `${inputKeyword} → ${extractedStandard}`;
            } else if (isSkipped) {
                titleText = `${inputKeyword} (未提取到标准号)`;
            }
            
            titleRow.innerHTML = `
                <span style="font-weight: 700; font-size: 1.1rem;">${titleText}</span>
                <span style="color: ${statusColor}; font-weight: 700; font-size: 1.1rem;">
                    ${statusText}
                </span>
            `;
            div.appendChild(titleRow);
            
            // 详情行
            const detailsDiv = document.createElement('div');
            detailsDiv.style.cssText = 'font-size: 0.95rem; color: var(--text-secondary); line-height: 1.6;';
            
            if (isSuccess) {
                // 成功时显示详细信息
                if (type === 'query' && item.data) {
                    const data = item.data;
                    let detailsHtml = '';
                    
                    // 标准编号
                    if (data.standard_number) detailsHtml += `<div><strong>标准编号：</strong>${data.standard_number}</div>`;
                    // 中文名
                    if (data.chinese_name) detailsHtml += `<div><strong>中文名：</strong>${data.chinese_name}</div>`;
                    // 英文名
                    if (data.english_name) detailsHtml += `<div><strong>英文名：</strong>${data.english_name}</div>`;
                    // 状态
                    if (data.standard_status) detailsHtml += `<div><strong>状态：</strong>${data.standard_status}</div>`;
                    // 发布日期
                    if (data.release_date) detailsHtml += `<div><strong>发布日期：</strong>${data.release_date}</div>`;
                    // 实施日期
                    if (data.implementation_date) detailsHtml += `<div><strong>实施日期：</strong>${data.implementation_date}</div>`;
                    // 废止日期
                    if (data.cancellation_date) detailsHtml += `<div><strong>废止日期：</strong>${data.cancellation_date}</div>`;
                    // 采用标准
                    if (data.adopt_standard) detailsHtml += `<div><strong>采用标准：</strong>${data.adopt_standard}</div>`;
                    // 引用标准
                    if (data.reference_standard) detailsHtml += `<div><strong>引用标准：</strong>${data.reference_standard}</div>`;
                    // 被替代标准
                    if (data.replaced_standard) detailsHtml += `<div><strong>被替代标准：</strong>${data.replaced_standard}</div>`;
                    // 替代标准
                    if (data.replacing_standard) detailsHtml += `<div><strong>替代标准：</strong>${data.replacing_standard}</div>`;
                    // 参考依据
                    if (data.reference_basis) detailsHtml += `<div><strong>参考依据：</strong>${data.reference_basis}</div>`;
                    // 标准摘要
                    if (data.standard_summary) detailsHtml += `<div><strong>标准摘要：</strong>${data.standard_summary}</div>`;
                    // 附修订
                    if (data.supplementary_revision) detailsHtml += `<div><strong>附修订：</strong>${data.supplementary_revision}</div>`;
                    // 来源
                    if (data.resource) detailsHtml += `<div style="margin-top: 0.5rem; color: #6b7280; font-size: 0.75rem;">来源: ${data.resource}</div>`;
                    
                    detailsDiv.innerHTML = detailsHtml || '<div style="color: #6b7280;">查询成功但未返回详细信息</div>';
                } else if (type === 'download') {
                    const platformResults = item.results || [];
                    
                    if (platformResults.length > 0) {
                        // 显示查询到的标准号
                        const extractedStandard = item.extracted_standard || '';
                        let standardInfoHtml = '';
                        if (extractedStandard) {
                            standardInfoHtml = `<div style="margin-bottom: 0.75rem; padding: 0.5rem; background: rgba(59, 130, 246, 0.1); border-radius: 4px; color: #3b82f6; font-size: 0.95rem; font-weight: 500;">
                                <strong>查询标准号：</strong>${extractedStandard}
                            </div>`;
                        }
                        
                        let tableHtml = standardInfoHtml + `
                            <table style="width: 100%; border-collapse: collapse; font-size: 1rem; margin-top: 0.75rem;">
                                <thead>
                                    <tr style="background: var(--bg-secondary);">
                                        <th style="padding: 0.75rem; text-align: left; border-bottom: 2px solid var(--border); font-size: 1.05rem;">平台</th>
                                        <th style="padding: 0.75rem; text-align: center; border-bottom: 2px solid var(--border); font-size: 1.05rem;">状态</th>
                                        <th style="padding: 0.75rem; text-align: left; border-bottom: 2px solid var(--border); font-size: 1.05rem;">实际标准号</th>
                                        <th style="padding: 0.75rem; text-align: left; border-bottom: 2px solid var(--border); font-size: 1.05rem;">文件名</th>
                                        <th style="padding: 0.75rem; text-align: center; border-bottom: 2px solid var(--border); font-size: 1.05rem;">操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                        `;
                        
                        platformResults.forEach(pr => {
                            const isSuccess = pr.status === 'success';
                            const statusText = isSuccess ? '✓ 可用' : '✗ 不可用';
                            const statusColor = isSuccess ? '#10b981' : '#ef4444';
                            
                            // 获取实际标准号
                            const actualStandard = pr.standard_number || '-';
                            
                            // 获取文件名
                            let fileName = '-';
                            let fileNameDisplay = '-';
                            if (pr.file_path) {
                                fileName = pr.file_path.split(/[\\/]/).pop();
                                fileNameDisplay = `<span style="font-size: 0.9rem; color: #374151; font-family: monospace;">${fileName}</span>`;
                            } else if (pr.view_url) {
                                fileNameDisplay = '<span style="color: #6b7280; font-size: 0.9rem;">在线链接</span>';
                            }
                            
                            let actionHtml = '-';
                            if (isSuccess) {
                                if (pr.file_path) {
                                    const filePath = pr.file_path.replace(/\\/g, '\\\\');
                                    actionHtml = `<button onclick="openFolder('${filePath}')" style="padding: 0.4rem 0.75rem; background: #10b981; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.95rem; font-weight: 500;">打开文件夹</button>`;
                                } else if (pr.view_url) {
                                    actionHtml = `<a href="${pr.view_url}" target="_blank" style="padding: 0.4rem 0.75rem; background: #3b82f6; color: white; border-radius: 6px; text-decoration: none; font-size: 0.95rem; font-weight: 500;">在线查看</a>`;
                                }
                            } else {
                                actionHtml = `<span style="color: #9ca3af; font-size: 0.9rem;">${pr.message || '无结果'}</span>`;
                            }
                            
                            tableHtml += `
                                <tr>
                                    <td style="padding: 0.75rem; border-bottom: 1px solid var(--border); font-size: 1rem;">${pr.platform}</td>
                                    <td style="padding: 0.75rem; text-align: center; border-bottom: 1px solid var(--border); color: ${statusColor}; font-size: 1rem; font-weight: 600;">${statusText}</td>
                                    <td style="padding: 0.75rem; border-bottom: 1px solid var(--border); font-size: 1rem; color: #3b82f6; font-weight: 500;">${actualStandard}</td>
                                    <td style="padding: 0.75rem; border-bottom: 1px solid var(--border);">${fileNameDisplay}</td>
                                    <td style="padding: 0.75rem; text-align: center; border-bottom: 1px solid var(--border);">${actionHtml}</td>
                                </tr>
                            `;
                        });
                        
                        tableHtml += '</tbody></table>';
                        detailsDiv.innerHTML = tableHtml;
                    } else {
                        detailsDiv.innerHTML = '<div style="color: #6b7280;">无下载结果</div>';
                    }
                }
            } else if (isSkipped) {
                // 跳过状态（未提取到标准号）
                const errorMsg = item.error || '未提取到标准号';
                let skipHtml = `<div style="color: #f59e0b;"><strong>原因：</strong>${errorMsg}</div>`;
                
                skipHtml += `<div style="margin-top: 0.5rem; padding: 0.5rem; background: rgba(245, 158, 11, 0.1); border-radius: 4px; color: #d97706; font-size: 0.75rem;">
                    <strong>提示：</strong>系统无法从输入文本中提取标准号。请检查输入格式，确保包含有效的标准编号（如GB/T 19001-2016、ISO 9001:2015等）
                </div>`;
                
                detailsDiv.innerHTML = skipHtml;
            } else {
                // 失败时显示详细错误
                const errorMsg = item.error || item.message || '未知错误';
                const platform = item.platform || '';
                
                let errorHtml = `<div style="color: #ef4444;"><strong>错误：</strong>${errorMsg}</div>`;
                if (platform) {
                    errorHtml += `<div style="margin-top: 0.25rem; color: #6b7280; font-size: 0.75rem;">平台: ${platform}</div>`;
                }
                
                // 添加重试建议
                if (errorMsg.includes('Selenium') || errorMsg.includes('ChromeDriver')) {
                    errorHtml += `<div style="margin-top: 0.5rem; padding: 0.5rem; background: rgba(245, 158, 11, 0.1); border-radius: 4px; color: #d97706; font-size: 0.75rem;">
                        <strong>提示：</strong>请检查Selenium和ChromeDriver是否已正确安装
                    </div>`;
                } else if (errorMsg.includes('超时') || errorMsg.includes('timeout')) {
                    errorHtml += `<div style="margin-top: 0.5rem; padding: 0.5rem; background: rgba(245, 158, 11, 0.1); border-radius: 4px; color: #d97706; font-size: 0.75rem;">
                        <strong>提示：</strong>查询超时，请稍后重试或尝试其他标准号
                    </div>`;
                } else if (errorMsg.includes('无法找到') || errorMsg.includes('not found')) {
                    errorHtml += `<div style="margin-top: 0.5rem; padding: 0.5rem; background: rgba(59, 130, 246, 0.1); border-radius: 4px; color: #2563eb; font-size: 0.75rem;">
                        <strong>提示：</strong>在该平台未找到此标准，请尝试其他平台或检查标准号是否正确
                    </div>`;
                }
                
                detailsDiv.innerHTML = errorHtml;
            }
            
            div.appendChild(detailsDiv);
            container.appendChild(div);
        });
        
        // 添加统计信息
        const successCount = result.filter(r => r.status === 'success').length;
        const failCount = result.length - successCount;
        const summaryDiv = document.createElement('div');
        summaryDiv.style.cssText = 'margin-top: 1rem; padding: 1rem; background: var(--bg-secondary); border-radius: 8px; text-align: center; font-size: 1.1rem; border: 1px solid var(--border);';
        summaryDiv.innerHTML = `
            <span style="color: #10b981; font-weight: 700;">成功: ${successCount}</span>
            <span style="margin: 0 1.5rem; color: var(--text-secondary);">|</span>
            <span style="color: #ef4444; font-weight: 700;">失败: ${failCount}</span>
            <span style="margin: 0 1.5rem; color: var(--text-secondary);">|</span>
            <span style="color: var(--text-primary); font-weight: 500;">总计: ${result.length}</span>
        `;
        container.appendChild(summaryDiv);
    }
}

// 渲染下载结果（带分页）
function renderDownloadResults() {
    const container = document.getElementById('downloadResult');
    if (!container) return;
    
    container.innerHTML = '';
    
    const { page, perPage, total } = downloadPagination;
    const start = (page - 1) * perPage;
    const end = Math.min(start + perPage, total);
    const pageData = downloadResults.slice(start, end);
    
    // 显示当前页数据
    pageData.forEach(item => {
        const inputKeyword = item.input_keyword || item.standard || '';
        const extractedStandard = item.extracted_standard || '';
        const standard = extractedStandard || inputKeyword;
        
        const isSuccess = item.status === 'success';
        const isSkipped = item.status === 'skipped';
        
        const div = document.createElement('div');
        div.className = 'result-item';
        
        let borderColor = '#ef4444';
        let bgColor = 'rgba(239, 68, 68, 0.05)';
        let statusText = '✗ 失败';
        let statusColor = '#ef4444';
        
        if (isSuccess) {
            borderColor = '#10b981';
            bgColor = 'rgba(16, 185, 129, 0.05)';
            statusText = '✓ 成功';
            statusColor = '#10b981';
        } else if (isSkipped) {
            borderColor = '#f59e0b';
            bgColor = 'rgba(245, 158, 11, 0.05)';
            statusText = '⚠ 跳过';
            statusColor = '#f59e0b';
        }
        
        div.style.cssText = `
            display: flex;
            flex-direction: column;
            padding: 1rem;
            margin-bottom: 0.75rem;
            border-radius: 8px;
            border-left: 4px solid ${borderColor};
            background: ${bgColor};
        `;
        
        // 标题行
        const titleRow = document.createElement('div');
        titleRow.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;';
        
        let titleText = standard;
        if (inputKeyword !== extractedStandard && extractedStandard) {
            titleText = `${inputKeyword} → ${extractedStandard}`;
        } else if (isSkipped) {
            titleText = `${inputKeyword} (未提取到标准号)`;
        }
        
        titleRow.innerHTML = `
            <span style="font-weight: 700; font-size: 1.1rem;">${titleText}</span>
            <span style="color: ${statusColor}; font-weight: 700; font-size: 1.1rem;">${statusText}</span>
        `;
        div.appendChild(titleRow);
        
        // 详情表格
        const detailsDiv = document.createElement('div');
        detailsDiv.style.cssText = 'font-size: 0.95rem; color: var(--text-secondary); line-height: 1.6;';
        
        if (isSuccess) {
            const platformResults = item.results || [];
            if (platformResults.length > 0) {
                // 显示查询到的标准号
                let standardInfoHtml = '';
                if (extractedStandard) {
                    standardInfoHtml = `<div style="margin-bottom: 0.75rem; padding: 0.5rem; background: rgba(59, 130, 246, 0.1); border-radius: 4px; color: #3b82f6; font-size: 0.95rem; font-weight: 500;">
                        <strong>查询标准号：</strong>${extractedStandard}
                    </div>`;
                }
                
                let tableHtml = standardInfoHtml + `
                    <table style="width: 100%; border-collapse: collapse; font-size: 1rem; margin-top: 0.75rem;">
                        <thead>
                            <tr style="background: var(--bg-secondary);">
                                <th style="padding: 0.75rem; text-align: left; border-bottom: 2px solid var(--border); font-size: 1.05rem;">平台</th>
                                <th style="padding: 0.75rem; text-align: center; border-bottom: 2px solid var(--border); font-size: 1.05rem;">状态</th>
                                <th style="padding: 0.75rem; text-align: left; border-bottom: 2px solid var(--border); font-size: 1.05rem;">实际标准号</th>
                                <th style="padding: 0.75rem; text-align: left; border-bottom: 2px solid var(--border); font-size: 1.05rem;">文件名</th>
                                <th style="padding: 0.75rem; text-align: center; border-bottom: 2px solid var(--border); font-size: 1.05rem;">操作</th>
                            </tr>
                        </thead>
                        <tbody>
                `;
                
                platformResults.forEach(pr => {
                    const prSuccess = pr.status === 'success';
                    const prStatusText = prSuccess ? '✓ 可用' : '✗ 不可用';
                    const prStatusColor = prSuccess ? '#10b981' : '#ef4444';
                    
                    // 获取实际标准号
                    const actualStandard = pr.standard_number || '-';
                    
                    // 获取文件名
                    let fileName = '-';
                    let fileNameDisplay = '-';
                    if (pr.file_path) {
                        fileName = pr.file_path.split(/[\\/]/).pop();
                        fileNameDisplay = `<span style="font-size: 0.9rem; color: #374151; font-family: monospace;">${fileName}</span>`;
                    } else if (pr.view_url) {
                        fileNameDisplay = '<span style="color: #6b7280; font-size: 0.9rem;">在线链接</span>';
                    }
                    
                    let actionHtml = '-';
                    if (prSuccess) {
                        if (pr.file_path) {
                            const filePath = pr.file_path.replace(/\\/g, '\\\\');
                            actionHtml = `<button onclick="openFolder('${filePath}')" style="padding: 0.4rem 0.75rem; background: #10b981; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.95rem; font-weight: 500;">打开文件夹</button>`;
                        } else if (pr.view_url) {
                            actionHtml = `<a href="${pr.view_url}" target="_blank" style="padding: 0.4rem 0.75rem; background: #3b82f6; color: white; border-radius: 6px; text-decoration: none; font-size: 0.95rem; font-weight: 500;">在线查看</a>`;
                        }
                    } else {
                        actionHtml = `<span style="color: #9ca3af; font-size: 0.9rem;">${pr.message || '无结果'}</span>`;
                    }
                    
                    tableHtml += `
                        <tr>
                            <td style="padding: 0.75rem; border-bottom: 1px solid var(--border); font-size: 1rem;">${pr.platform}</td>
                            <td style="padding: 0.75rem; text-align: center; border-bottom: 1px solid var(--border); color: ${prStatusColor}; font-size: 1rem; font-weight: 600;">${prStatusText}</td>
                            <td style="padding: 0.75rem; border-bottom: 1px solid var(--border); font-size: 1rem; color: #3b82f6; font-weight: 500;">${actualStandard}</td>
                            <td style="padding: 0.75rem; border-bottom: 1px solid var(--border);">${fileNameDisplay}</td>
                            <td style="padding: 0.75rem; text-align: center; border-bottom: 1px solid var(--border);">${actionHtml}</td>
                        </tr>
                    `;
                });
                
                tableHtml += '</tbody></table>';
                detailsDiv.innerHTML = tableHtml;
            } else {
                detailsDiv.innerHTML = '<div style="color: #6b7280;">无下载结果</div>';
            }
        } else if (isSkipped) {
            const errorMsg = item.error || '未提取到标准号';
            detailsDiv.innerHTML = `<div style="color: #f59e0b;"><strong>原因：</strong>${errorMsg}</div>`;
        } else {
            const errorMsg = item.error || item.message || '未知错误';
            detailsDiv.innerHTML = `<div style="color: #ef4444;"><strong>错误：</strong>${errorMsg}</div>`;
        }
        
        div.appendChild(detailsDiv);
        container.appendChild(div);
    });
    
    // 添加分页控件
    if (total > perPage) {
        const totalPages = Math.ceil(total / perPage);
        const paginationDiv = document.createElement('div');
        paginationDiv.style.cssText = `
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 0.5rem;
            margin-top: 1.5rem;
            padding: 1rem;
            background: var(--bg-secondary);
            border-radius: 8px;
            border: 1px solid var(--border);
        `;
        
        // 上一页按钮
        const prevBtn = document.createElement('button');
        prevBtn.textContent = '← 上一页';
        prevBtn.disabled = page <= 1;
        prevBtn.style.cssText = `
            padding: 0.5rem 1rem;
            background: ${page <= 1 ? '#475569' : 'var(--accent)'};
            color: white;
            border: none;
            border-radius: 6px;
            cursor: ${page <= 1 ? 'not-allowed' : 'pointer'};
            font-size: 0.9rem;
        `;
        prevBtn.onclick = () => {
            if (page > 1) {
                downloadPagination.page--;
                renderDownloadResults();
            }
        };
        paginationDiv.appendChild(prevBtn);
        
        // 页码信息
        const pageInfo = document.createElement('span');
        pageInfo.style.cssText = 'color: var(--text-secondary); font-size: 0.9rem; margin: 0 1rem;';
        pageInfo.innerHTML = `第 <strong style="color: var(--text-primary);">${page}</strong> / ${totalPages} 页 (共 ${total} 条)`;
        paginationDiv.appendChild(pageInfo);
        
        // 每页显示数量选择
        const perPageSelect = document.createElement('select');
        perPageSelect.style.cssText = `
            padding: 0.4rem 0.5rem;
            background: var(--bg-card);
            color: var(--text-primary);
            border: 1px solid var(--border);
            border-radius: 4px;
            font-size: 0.85rem;
            margin-left: 0.5rem;
        `;
        [5, 10, 20, 50].forEach(n => {
            const opt = document.createElement('option');
            opt.value = n;
            opt.textContent = `${n}条/页`;
            opt.selected = n === perPage;
            perPageSelect.appendChild(opt);
        });
        perPageSelect.onchange = (e) => {
            downloadPagination.perPage = parseInt(e.target.value);
            downloadPagination.page = 1;
            renderDownloadResults();
        };
        paginationDiv.appendChild(perPageSelect);
        
        // 下一页按钮
        const nextBtn = document.createElement('button');
        nextBtn.textContent = '下一页 →';
        nextBtn.disabled = page >= totalPages;
        nextBtn.style.cssText = `
            padding: 0.5rem 1rem;
            background: ${page >= totalPages ? '#475569' : 'var(--accent)'};
            color: white;
            border: none;
            border-radius: 6px;
            cursor: ${page >= totalPages ? 'not-allowed' : 'pointer'};
            font-size: 0.9rem;
        `;
        nextBtn.onclick = () => {
            if (page < totalPages) {
                downloadPagination.page++;
                renderDownloadResults();
            }
        };
        paginationDiv.appendChild(nextBtn);
        
        container.appendChild(paginationDiv);
    }
    
    // 添加统计信息
    const successCount = downloadResults.filter(r => r.status === 'success').length;
    const failCount = downloadResults.length - successCount;
    const summaryDiv = document.createElement('div');
    summaryDiv.style.cssText = 'margin-top: 1rem; padding: 1rem; background: var(--bg-secondary); border-radius: 8px; text-align: center; font-size: 1.1rem; border: 1px solid var(--border);';
    summaryDiv.innerHTML = `
        <span style="color: #10b981; font-weight: 700;">成功: ${successCount}</span>
        <span style="margin: 0 1.5rem; color: var(--text-secondary);">|</span>
        <span style="color: #ef4444; font-weight: 700;">失败: ${failCount}</span>
        <span style="margin: 0 1.5rem; color: var(--text-secondary);">|</span>
        <span style="color: var(--text-primary); font-weight: 500;">总计: ${downloadResults.length}</span>
    `;
    container.appendChild(summaryDiv);
}

// 生成模拟下载数据
function generateMockDownloadData(count = 25) {
    const platforms = ['食品伙伴网', 'GBT标准网', '国家标准全文公开系统', '工标网'];
    const standards = [
        'GB/T 19001-2016', 'GB/T 24001-2016', 'GB/T 45001-2020', 
        'GB 5009.3-2016', 'GB 5009.4-2016', 'GB 5009.5-2016',
        'GB/T 2828.1-2012', 'GB/T 2829-2002', 'GB/T 2918-2018',
        'ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018',
        'GB/T 1.1-2020', 'GB/T 1.2-2020', 'GB/T 20001.10-2014',
        'HJ 25.1-2019', 'HJ 25.2-2019', 'HJ 25.3-2019',
        'JJF 1001-2011', 'JJF 1059.1-2012', 'JJF 1059.2-2012',
        'GBZ 2.1-2019', 'GBZ 2.2-2007', 'GBZ 1-2010', 'GBZ 188-2014'
    ];
    
    const results = [];
    for (let i = 0; i < count; i++) {
        const standard = standards[i % standards.length];
        const isSuccess = Math.random() > 0.3;
        
        const platformResults = platforms.map(p => {
            const prSuccess = isSuccess && Math.random() > 0.5;
            return {
                platform: p,
                status: prSuccess ? 'success' : 'error',
                file_path: prSuccess && p === '食品伙伴网' ? `/home/user/downloads/${standard.replace(/\//g, '_')}.pdf` : null,
                view_url: prSuccess && p === '国家标准全文公开系统' ? `http://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=${Math.random().toString(36).substr(2, 10)}` : null,
                message: prSuccess ? null : '未找到该标准文件'
            };
        });
        
        results.push({
            input_keyword: standard,
            extracted_standard: standard,
            status: isSuccess ? 'success' : 'error',
            results: platformResults,
            message: isSuccess ? '下载成功' : '所有平台均未找到该标准'
        });
    }
    
    return results;
}

// 检查响应是否需要升级
function checkUpgradeResponse(data) {
    if (data && data.need_upgrade) {
        showUpgradeModal(data.message);
        return true;
    }
    return false;
}

// 显示升级弹窗
function showUpgradeModal(message) {
    // 如果已经存在弹窗，不再重复显示
    if (document.getElementById('upgradeModal')) return;
    
    const modal = document.createElement('div');
    modal.id = 'upgradeModal';
    modal.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.8); z-index: 10000;
        display: flex; align-items: center; justify-content: center;
    `;
    
    modal.innerHTML = `
        <div style="background: var(--bg-secondary); padding: 2rem; border-radius: 12px; max-width: 400px; text-align: center; border: 2px solid var(--warning);">
            <div style="font-size: 3rem; margin-bottom: 1rem;">⚠️</div>
            <h3 style="margin-bottom: 1rem; color: var(--warning);">需要升级</h3>
            <p style="margin-bottom: 1.5rem; color: var(--text-secondary); line-height: 1.6;">
                ${message || '您的客户端版本过低，需要升级后才能继续使用。'}
            </p>
            <p style="margin-bottom: 1.5rem; font-size: 0.875rem; color: var(--text-secondary);">
                请重启客户端或联系管理员获取最新版本。
            </p>
            <button onclick="location.reload()" style="padding: 0.75rem 2rem; background: var(--warning); color: #000; border: none; border-radius: 6px; font-weight: 600; cursor: pointer;">
                刷新页面重试
            </button>
        </div>
    `;
    
    document.body.appendChild(modal);
}

// 显示错误
function showError(type, message) {
    const container = document.getElementById(type + 'Result');
    if (!container) return;
    
    // 解析错误信息，添加更友好的显示
    let errorTitle = '操作失败';
    let errorDetails = message;
    let suggestions = [];
    
    if (message.includes('Selenium') || message.includes('ChromeDriver') || message.includes('Chrome')) {
        errorTitle = '浏览器驱动错误';
        errorDetails = '无法启动浏览器自动化工具';
        suggestions = [
            '1. 请确保已安装 Google Chrome 浏览器',
            '2. 下载并安装 ChromeDriver（版本需与Chrome匹配）',
            '3. 将 ChromeDriver 添加到系统 PATH',
            '4. 或者使用命令: pip install selenium'
        ];
    } else if (message.includes('timeout') || message.includes('超时')) {
        errorTitle = '请求超时';
        errorDetails = '查询请求超时，可能是网络问题或服务器响应慢';
        suggestions = [
            '1. 检查网络连接是否正常',
            '2. 稍后重试',
            '3. 尝试减少同时查询的标准号数量'
        ];
    } else if (message.includes('not found') || message.includes('无法找到') || message.includes('找不到')) {
        errorTitle = '未找到结果';
        errorDetails = '在查询平台上未找到该标准';
        suggestions = [
            '1. 检查标准号是否输入正确',
            '2. 尝试使用其他查询平台',
            '3. 该标准可能不在该平台上收录'
        ];
    } else if (message.includes('permission') || message.includes('许可') || message.includes('license')) {
        errorTitle = '许可证错误';
        errorDetails = '无法获取操作许可证';
        suggestions = [
            '1. 检查客户端是否已注册到服务端',
            '2. 联系管理员确认账户状态',
            '3. 重启客户端后重试'
        ];
    } else if (message.includes('version') || message.includes('版本')) {
        errorTitle = '版本不兼容';
        suggestions = [
            '1. 请升级客户端到最新版本',
            '2. 重启客户端',
            '3. 如果问题持续，联系管理员'
        ];
    }
    
    let suggestionsHtml = '';
    if (suggestions.length > 0) {
        suggestionsHtml = `
            <div style="margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid rgba(239, 68, 68, 0.2);">
                <div style="font-weight: 600; margin-bottom: 0.5rem; color: #dc2626;">解决建议：</div>
                ${suggestions.map(s => `<div style="margin: 0.25rem 0; padding-left: 0.5rem; border-left: 2px solid #fecaca;">${s}</div>`).join('')}
            </div>
        `;
    }
    
    container.innerHTML = `
        <div style="padding: 1rem; background: rgba(239, 68, 68, 0.08); border-radius: 8px; border: 1px solid rgba(239, 68, 68, 0.3);">
            <div style="display: flex; align-items: center; margin-bottom: 0.5rem;">
                <span style="font-size: 1.25rem; margin-right: 0.5rem;">✗</span>
                <span style="font-weight: 700; color: #dc2626; font-size: 1rem;">${errorTitle}</span>
            </div>
            <div style="color: #991b1b; margin-bottom: 0.5rem; font-size: 0.9rem;">${errorDetails}</div>
            ${message !== errorDetails ? `<div style="font-size: 0.8rem; color: #7f1d1d; background: rgba(239, 68, 68, 0.1); padding: 0.5rem; border-radius: 4px; font-family: monospace; margin-bottom: 0.5rem;">详细信息: ${message}</div>` : ''}
            ${suggestionsHtml}
        </div>
    `;
}

// 重置
function resetResult(type) {
    const resultEl = document.getElementById(type + 'Result');
    const progressEl = document.getElementById(type + 'Progress');
    
    if (resultEl) resultEl.innerHTML = '';
    if (progressEl) progressEl.classList.remove('active');
}

function resetButton(type) {
    const progressEl = document.getElementById(type + 'Progress');
    const btn = document.getElementById(type + 'Btn');
    const cancelBtn = document.getElementById(type + 'CancelBtn');
    
    if (progressEl) progressEl.classList.remove('active');
    if (btn) {
        btn.disabled = false;
        const labels = { extract: '开始提取', query: '开始查询', download: '开始下载' };
        btn.textContent = labels[type];
    }
    if (cancelBtn) cancelBtn.style.display = 'none';
    
    if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
    }
}

// 取消任务
async function cancelTask(taskType) {
    try {
        const res = await fetch('/api/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_type: taskType })
        });
        const data = await res.json();
        if (data.success) {
            const btn = document.getElementById(taskType + 'CancelBtn');
            if (btn) btn.style.display = 'none';
        }
    } catch (e) {
        console.error('取消失败:', e);
    }
}

window.cancelTask = cancelTask;

// 打开文件夹
async function openFolder(filePath) {
    try {
        const res = await fetch('/api/open-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: filePath })
        });
        const data = await res.json();
        if (!data.success) {
            alert(data.message || '打开文件夹失败');
        }
    } catch (e) {
        alert('打开文件夹失败: ' + e.message);
    }
}
window.openFolder = openFolder;

// 消息中心
function toggleChat() {
    const widget = document.getElementById('chatWidget');
    if (!widget) return;
    
    widget.classList.toggle('collapsed');
    const toggleBtn = document.getElementById('chatToggle');
    if (toggleBtn) {
        toggleBtn.textContent = widget.classList.contains('collapsed') ? '▲' : '▼';
    }
}

function sendChatMessage() {
    const input = document.getElementById('chatInput');
    if (!input) return;
    
    const content = input.value.trim();
    if (!content) return;

    addChatMessage('self', content);
    input.value = '';

    fetch('/api/messages', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content })
    }).catch(e => console.error('发送消息失败:', e));
}

// 从localStorage加载历史消息
function loadChatHistory() {
    const history = localStorage.getItem('chat_history');
    if (history) {
        const messages = JSON.parse(history);
        messages.forEach(m => {
            addChatMessage(m.type, m.content, m.id, false);
        });
    }
}

// 保存消息到localStorage
function saveChatMessage(type, content, msgId) {
    let history = [];
    const stored = localStorage.getItem('chat_history');
    if (stored) {
        history = JSON.parse(stored);
    }
    // 检查是否已存在
    if (!history.find(m => m.id === msgId)) {
        history.push({
            id: msgId || Date.now().toString(),
            type: type,
            content: content,
            time: new Date().toISOString()
        });
        // 只保留最近100条
        if (history.length > 100) {
            history = history.slice(-100);
        }
        localStorage.setItem('chat_history', JSON.stringify(history));
    }
}

function addChatMessage(type, content, msgId, save = true) {
    const container = document.getElementById('chatMessages');
    if (!container) return;
    
    // 如果有msgId，检查是否已存在
    if (msgId && container.querySelector(`[data-msg-id="${msgId}"]`)) {
        return;
    }
    
    if (container.querySelector('.empty-state')) {
        container.innerHTML = '';
    }
    
    const time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    const div = document.createElement('div');
    div.className = `chat-message ${type}`;
    if (msgId) {
        div.setAttribute('data-msg-id', msgId);
    }
    div.innerHTML = `
        <div class="chat-bubble">${escapeHtml(content)}</div>
        <div class="chat-time">${time}</div>
    `;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    
    // 保存到本地存储
    if (save) {
        saveChatMessage(type, content, msgId || Date.now().toString());
    }
}

function startMessagePolling() {
    setInterval(async () => {
        try {
            const res = await fetch('/api/messages');
            const data = await res.json();
            
            if (data.success && data.data) {
                let newChatCount = 0;
                let newBroadcastCount = 0;
                data.data.forEach(msg => {
                    // 真正的广播消息(type=broadcast)加入走马灯
                    if (msg.type === 'broadcast') {
                        if (!broadcasts.find(b => b.id === msg.id)) {
                            broadcasts.push(msg);
                            saveBroadcast(msg); // 保存到localStorage
                            newBroadcastCount++;
                        }
                    } else if (msg.from === 'server' || msg.from === 'system') {
                        // 管理员点对点回复显示在聊天窗口
                        addChatMessage('server', msg.content);
                        newChatCount++;
                    }
                });
                
                // 如果有新广播，更新显示
                if (newBroadcastCount > 0) {
                    updateBroadcastDisplay();
                }
                
                if (newChatCount > 0) {
                    const badge = document.getElementById('chatBadge');
                    if (badge) {
                        const current = parseInt(badge.textContent) || 0;
                        badge.textContent = current + newChatCount;
                        badge.classList.add('show');
                    }
                }
            }
            
            // 处理已删除的广播
            if (data.deleted_broadcasts && data.deleted_broadcasts.length > 0) {
                removeDeletedBroadcasts(data.deleted_broadcasts);
            }
        } catch (e) {
            console.error('消息轮询失败:', e);
        }
    }, 5000);
}

// 更新广播走马灯显示
function updateBroadcastDisplay() {
    console.log('[调试] updateBroadcastDisplay 被调用, broadcasts数量:', broadcasts.length);
    if (broadcasts.length === 0) {
        console.log('[调试] broadcasts 为空，不更新显示');
        return;
    }
    
    const textEl = document.getElementById('broadcastText');
    console.log('[调试] broadcastText 元素:', textEl);
    if (!textEl) {
        console.log('[调试] 未找到 broadcastText 元素');
        return;
    }
    
    const current = broadcasts[broadcastIndex % broadcasts.length];
    console.log('[调试] 当前广播内容:', current);
    textEl.textContent = current.content;
    
    // 重新触发动画
    textEl.style.animation = 'none';
    textEl.offsetHeight; // 触发重排
    textEl.style.animation = 'marquee 15s linear infinite';
    console.log('[调试] 走马灯动画已设置');
}

// 广播轮播切换
setInterval(() => {
    if (broadcasts.length > 1) {
        broadcastIndex = (broadcastIndex + 1) % broadcasts.length;
        updateBroadcastDisplay();
    }
}, 10000); // 10秒切换一条广播

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 确认弹窗相关

// 导出函数到全局作用域
window.startExtract = startExtract;
window.exportCSV = exportCSV;
window.exportQueryCSV = exportQueryCSV;
window.clearResults = clearResults;
window.startQuery = startQuery;
window.startDownload = startDownload;
window.setQueryMode = setQueryMode;
window.toggleChat = toggleChat;
window.sendChatMessage = sendChatMessage;
window.clearFileSelection = clearFileSelection;
window.cancelTask = cancelTask;
window.openFolder = openFolder;