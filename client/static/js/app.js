// Jingxi 检测工具箱 - 离线JavaScript

// 全局状态
let currentTask = null;
let progressInterval = null;
let queryMode = 'text'; // 'file' 或 'text'
let extractResults = []; // 存储提取结果用于导出
let queryResults = []; // 存储查询结果用于导出
let queryExportRows = []; // 查询导出累计结果
let queryReferenceEnabled = false;
let queryPendingReferenceRound = null;
let queryRoundIndex = 0;
let queryReferenceRoundUsed = false;
let queryCurrentBatchMeta = { label: '待测标准', isReferenceRound: false };
let queryRoundProgressState = { main: null, reference: null };
let queryCurrentStandards = [];
let queryKnownStandards = new Set();
let downloadResults = []; // 存储下载结果用于分页
let downloadPagination = { page: 1, perPage: 5, total: 0 }; // 下载分页状态
let broadcasts = []; // 广播消息列表
let broadcastIndex = 0; // 当前广播索引
let browserWarmupNoticeShown = false;
const BROADCAST_STORAGE_KEY = 'broadcast_history';
const LOCAL_API_HEADER = 'X-Lab-Local-Api-Token';
const nativeFetch = window.fetch.bind(window);
let localApiToken = '';
let localApiTokenPromise = null;

// 模拟数据开关 - 设置为 true 启用模拟数据
const ENABLE_MOCK_DATA = false;

function isApiRequestTarget(input) {
    try {
        const url = input instanceof Request ? new URL(input.url) : new URL(String(input), window.location.href);
        return url.origin === window.location.origin && url.pathname.startsWith('/api/');
    } catch (e) {
        return false;
    }
}

function isBootstrapRequestTarget(input) {
    try {
        const url = input instanceof Request ? new URL(input.url) : new URL(String(input), window.location.href);
        return url.origin === window.location.origin && url.pathname === '/api/bootstrap';
    } catch (e) {
        return false;
    }
}

async function ensureLocalApiToken() {
    if (localApiToken) return localApiToken;
    if (!localApiTokenPromise) {
        localApiTokenPromise = nativeFetch('/api/bootstrap', {
            credentials: 'same-origin'
        })
            .then(async (res) => {
                const payload = await res.json().catch(() => ({}));
                if (!res.ok || !payload.success || !payload.data?.local_api_token) {
                    throw new Error(payload.message || '本地接口初始化失败');
                }
                localApiToken = payload.data.local_api_token;
                return localApiToken;
            })
            .catch((err) => {
                localApiTokenPromise = null;
                throw err;
            });
    }
    return localApiTokenPromise;
}

window.fetch = async function(input, init = undefined) {
    if (!isApiRequestTarget(input) || isBootstrapRequestTarget(input)) {
        return nativeFetch(input, init);
    }

    const token = await ensureLocalApiToken();
    const headers = new Headers(
        init?.headers || (input instanceof Request ? input.headers : undefined) || {}
    );
    headers.set(LOCAL_API_HEADER, token);

    if (input instanceof Request) {
        return nativeFetch(
            new Request(input, {
                ...init,
                headers,
                credentials: init?.credentials || input.credentials || 'same-origin'
            })
        );
    }

    return nativeFetch(input, {
        ...init,
        headers,
        credentials: init?.credentials || 'same-origin'
    });
};

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
    setInterval(loadStatus, 5000);
    loadBroadcastHistory(); // 加载历史广播
    loadChatHistory(); // 加载历史消息
    loadInitialMessages(); // 从服务器加载最新消息
    startMessagePolling();
    setInterval(() => {
        if (downloadSubTab === 'tech' && techTaskListExpanded) {
            loadTechFileTasks();
        }
    }, 5000);
    setupDragDrop();
    setupQueryFileUpload();
    initSmartDropdowns();
});

function bindSmartDropdown(selectEl) {
    if (!selectEl || selectEl.dataset.smartBound === '1') return;

    const expand = () => {
        const optionCount = selectEl.options ? selectEl.options.length : 0;
        const visibleRows = Math.min(8, Math.max(2, optionCount || 2));
        selectEl.size = visibleRows;
        selectEl.style.maxHeight = '260px';
        selectEl.style.overflowY = 'auto';
    };

    const collapse = () => {
        selectEl.size = 1;
        selectEl.style.maxHeight = '';
        selectEl.style.overflowY = '';
    };

    selectEl.addEventListener('focus', expand);
    // 选中后控件可能仍保持焦点，第二次点击不会再触发 focus，这里强制再展开
    selectEl.addEventListener('mousedown', (e) => {
        if (selectEl.size === 1) {
            e.preventDefault();
            expand();
        }
    });

    selectEl.addEventListener('blur', collapse);
    selectEl.addEventListener('change', collapse);
    selectEl.dataset.smartBound = '1';
}

function initSmartDropdowns() {
    document.querySelectorAll('select[data-smart-dropdown="1"]').forEach(bindSmartDropdown);
}

function showToast(message, type = 'info', durationMs = 2200) {
    if (!message) return;
    const colorMap = {
        success: '#10b981',
        error: '#ef4444',
        info: '#3b82f6'
    };
    const bg = colorMap[type] || colorMap.info;

    try {
        let container = document.getElementById('globalToastContainer');
        if (!container) {
            container = document.createElement('div');
            container.id = 'globalToastContainer';
            container.style.cssText = 'position: fixed; right: 16px; bottom: 80px; z-index: 9999; display: flex; flex-direction: column; gap: 8px; max-width: 420px;';
            document.body.appendChild(container);
        }

        const toast = document.createElement('div');
        toast.style.cssText = `padding: 10px 12px; border-radius: 8px; color: #fff; background: ${bg}; box-shadow: 0 6px 20px rgba(0,0,0,0.2); font-size: 13px; line-height: 1.4;`;
        toast.textContent = String(message);
        container.appendChild(toast);

        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transition = 'opacity 0.25s';
            setTimeout(() => toast.remove(), 260);
        }, durationMs);
    } catch (e) {
        alert(String(message));
    }
}

// 启动时从服务器加载消息
async function loadInitialMessages() {
    try {
        const res = await fetch('/api/messages');
        const data = await res.json();
        
        console.log('[调试] 获取到的消息:', data);
        
        if (data.success && data.data) {
            let hasNewBroadcast = false;
            let newServerChatCount = 0;
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
                    if (addChatMessage('server', msg.content, msg.id)) {
                        newServerChatCount++;
                    }
                }
            });
            console.log('[调试] 广播列表:', broadcasts);
            if (hasNewBroadcast) {
                updateBroadcastDisplay();
            }

            // 启动时如果拉到了未读回复，且当前消息中心处于收起状态，则展示红点数量
            if (newServerChatCount > 0) {
                const widget = document.getElementById('chatWidget');
                const isCollapsed = widget ? widget.classList.contains('collapsed') : true;
                if (isCollapsed) {
                    incrementChatBadge(newServerChatCount);
                } else {
                    clearChatBadge();
                }
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
        if (section === 'questionbank') {
            loadQuestionBanks();
        }
    } else {
        console.log('Section not found:', `section-${section}`);
    }
}

window.showSection = showSection;

// ============ 在线题库 ============
let questionBankMode = 'quiz'; // view | quiz 默认做题模式
let questionBankList = [];
let questionBankCurrent = null;
let questionBankCurrentIndex = 0;
let quizResponses = {};

function setQuestionBankMode(mode) {
    questionBankMode = mode;
    document.getElementById('btnQBViewMode')?.classList.toggle('active', mode === 'view');
    document.getElementById('btnQBQuizMode')?.classList.toggle('active', mode === 'quiz');
    if (questionBankCurrent) {
        renderQuestionBank();
    }
}
window.setQuestionBankMode = setQuestionBankMode;

function bankLabelText(value, fallback = '') {
    if (value === null || value === undefined) return fallback;
    if (typeof value === 'string') {
        const text = value.trim();
        return text || fallback;
    }
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    try {
        return JSON.stringify(value);
    } catch (e) {
        return fallback;
    }
}

async function loadQuestionBanks() {
    try {
        const selectEl = document.getElementById('questionBankSelect');
        if (!selectEl) return;
        bindSmartDropdown(selectEl);
        if (questionBankList.length > 0 && selectEl.options.length > 0) return;

        selectEl.innerHTML = '<option value="">加载中...</option>';
        const res = await fetch('/api/question_banks');
        const data = await res.json();
        if (!data.success) {
            selectEl.innerHTML = '<option value="">题库加载失败</option>';
            showToast(data.message || '题库加载失败', 'error');
            return;
        }

        questionBankList = data.data || [];
        selectEl.innerHTML = questionBankList.map(b => {
            const title = bankLabelText(b.title, bankLabelText(b.id, '未命名题库'));
            const label = `${title}（${b.count || 0}）`;
            return `<option value="${escapeAttr(String(b.id || ''))}">${escapeHtml(label)}</option>`;
        }).join('');

        selectEl.onchange = async () => {
            renderQuestionBankMeta();
            await loadSelectedQuestionBank();
        };
        renderQuestionBankMeta();
        if (selectEl.value) {
            await loadSelectedQuestionBank();
        }
    } catch (e) {
        console.error('loadQuestionBanks failed:', e);
        showToast('题库加载失败: ' + e.message, 'error');
    }
}

function renderQuestionBankMeta() {
    const selectEl = document.getElementById('questionBankSelect');
    const metaEl = document.getElementById('questionBankMeta');
    if (!selectEl || !metaEl) return;
    const bankId = selectEl.value;
    const bank = questionBankList.find(b => b.id === bankId);
    if (!bank) {
        metaEl.textContent = '';
        return;
    }
    const desc = bank.description ? ` | ${bank.description}` : '';
    metaEl.textContent = `题数: ${bank.count || 0}${desc}`;
}

async function loadSelectedQuestionBank() {
    const selectEl = document.getElementById('questionBankSelect');
    const container = document.getElementById('questionBankContainer');
    if (!selectEl || !container) return;
    const bankId = selectEl.value;
    if (!bankId) {
        showToast('请选择题库', 'error');
        return;
    }
    container.innerHTML = '<div class="empty-state">加载中...</div>';
    try {
        const res = await fetch(`/api/question_banks/${encodeURIComponent(bankId)}`);
        const data = await res.json();
        if (!data.success) {
            container.innerHTML = `<div class="empty-state">${escapeHtml(data.message || '加载失败')}</div>`;
            return;
        }
        questionBankCurrent = data.data;
        questionBankCurrentIndex = 0;
        quizResponses = {};
        renderQuestionBank();
    } catch (e) {
        container.innerHTML = `<div class="empty-state">加载失败: ${escapeHtml(e.message)}</div>`;
    }
}
window.loadSelectedQuestionBank = loadSelectedQuestionBank;

function renderQuestionBank() {
    const container = document.getElementById('questionBankContainer');
    if (!container) return;
    if (!questionBankCurrent || !Array.isArray(questionBankCurrent.questions)) {
        container.innerHTML = '<div class="empty-state">未加载题库</div>';
        return;
    }

    const title = questionBankCurrent.title || questionBankCurrent.id;
    const isQuizMode = questionBankMode === 'quiz';
    const total = questionBankCurrent.questions.length;
    if (total === 0) {
        container.innerHTML = '<div class="empty-state">题库为空</div>';
        return;
    }
    if (questionBankCurrentIndex < 0) questionBankCurrentIndex = 0;
    if (questionBankCurrentIndex >= total) questionBankCurrentIndex = total - 1;

    const currentIdx = questionBankCurrentIndex;
    const currentQuestion = questionBankCurrent.questions[currentIdx];
    container.innerHTML = `
        <div style="position: relative;">
            <div style="position: absolute; top: 0; right: 0; z-index: 10; font-size: 0.8rem; color: var(--text-secondary); background: rgba(0,0,0,0.05); padding: 0.25rem 0.6rem; border-radius: 4px;">
                ${currentIdx + 1} / ${total}
            </div>
            <div id="qbList" style="padding-top: 0.5rem;"></div>
        </div>
        <div style="display:flex; align-items:center; justify-content:space-between; gap:0.5rem; margin-top:0.75rem;">
            <button class="btn btn-secondary" onclick="prevQuestionBankQuestion()" ${currentIdx <= 0 ? 'disabled' : ''}>上一题</button>
            <button class="btn btn-primary" onclick="nextQuestionBankQuestion()" ${currentIdx >= total - 1 ? 'disabled' : ''}>下一题</button>
        </div>
    `;
    const listEl = document.getElementById('qbList');
    listEl.innerHTML = renderQuestionItem(currentQuestion, currentIdx, isQuizMode);
}

function prevQuestionBankQuestion() {
    if (!questionBankCurrent || !Array.isArray(questionBankCurrent.questions)) return;
    if (questionBankCurrentIndex <= 0) return;
    questionBankCurrentIndex -= 1;
    renderQuestionBank();
}
window.prevQuestionBankQuestion = prevQuestionBankQuestion;

function nextQuestionBankQuestion() {
    if (!questionBankCurrent || !Array.isArray(questionBankCurrent.questions)) return;
    if (questionBankCurrentIndex >= questionBankCurrent.questions.length - 1) return;
    questionBankCurrentIndex += 1;
    renderQuestionBank();
}
window.nextQuestionBankQuestion = nextQuestionBankQuestion;

function normalizeAnswerList(q) {
    return (q.answer || [])
        .flatMap(a => String(a).split(/[，,;；、\s]+/))
        .map(a => String(a).trim())
        .filter(Boolean);
}

function isJudgeQuestion(q) {
    const typeText = String(q.type || '').toLowerCase();
    const answers = normalizeAnswerList(q).map(v => v.toLowerCase());
    const judgeWords = ['判断', 'true', 'false', '正确', '错误', '对', '错', '√', '×'];
    return typeText.includes('判断')
        || (answers.length > 0 && answers.every(v => judgeWords.includes(v)));
}

function normalizeJudgeValue(value) {
    const v = String(value || '').trim().toLowerCase();
    if (['正确', '对', 'true', '√', '1'].includes(v)) return '正确';
    if (['错误', '错', 'false', '×', '0'].includes(v)) return '错误';
    return String(value || '').trim();
}

function stripOptionPrefix(text, key) {
    let value = String(text || '').trim();
    const keyText = String(key || '').trim();
    if (!value || !keyText) return value;
    const escapedKey = keyText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const patterns = [
        new RegExp(`^\\s*${escapedKey}\\s*[\\.．、:：\\)）-]\\s*`, 'i'),
        new RegExp(`^\\s*[（(]\\s*${escapedKey}\\s*[)）]\\s*`, 'i')
    ];
    patterns.forEach(p => {
        value = value.replace(p, '').trim();
    });
    return value;
}

function optionKeyMap(q) {
    const map = new Map();
    (q.options || []).forEach(o => {
        const key = String(o.key || '').trim();
        const text = String(o.text || '').trim();
        const cleanText = stripOptionPrefix(text, key);
        if (!key) return;
        const upperKey = key.toUpperCase();
        map.set(upperKey, upperKey);
        if (text) {
            map.set(text, upperKey);
            map.set(text.toLowerCase(), upperKey);
        }
        if (cleanText) {
            map.set(cleanText, upperKey);
            map.set(cleanText.toLowerCase(), upperKey);
        }
    });
    return map;
}

function normalizeOptionAnswerValue(value, keyMap) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const upper = raw.toUpperCase();
    if (keyMap.has(upper)) return keyMap.get(upper);
    if (keyMap.has(raw)) return keyMap.get(raw);
    if (keyMap.has(raw.toLowerCase())) return keyMap.get(raw.toLowerCase());
    return upper;
}

function renderQuestionItem(q, idx, isQuizMode) {
    const options = q.options || [];
    const answers = normalizeAnswerList(q);
    const answerText = answers.length ? answers.join(', ') : '-';
    const explanation = q.explanation ? `<div style="margin-top:0.4rem;color:var(--text-secondary);font-size:0.85rem;">解析：${escapeHtml(q.explanation)}</div>` : '';
    const key = String(idx);
    const feedback = quizResponses[key];

    let answerArea = `
        <div style="margin-top:0.5rem;color:#10b981;font-weight:700;">答案：${escapeHtml(answerText)}</div>
        ${explanation}
    `;

    if (isQuizMode) {
        let inputHtml = '';
        if (options.length > 0) {
            const multi = answers.length > 1;
            inputHtml = options.map(o => {
                const inputId = `qb_${idx}_${o.key}`;
                return `
                    <label for="${inputId}" style="display:flex; gap:0.5rem; padding:0.45rem; border:1px solid var(--border); border-radius:8px; margin-top:0.4rem; cursor:pointer;">
                        <input type="${multi ? 'checkbox' : 'radio'}" name="qb_${idx}" id="${inputId}" value="${escapeHtml(String(o.key))}">
                        <span style="font-family: monospace; font-weight:700;">${escapeHtml(String(o.key))}.</span>
                        <span>${escapeHtml(stripOptionPrefix(o.text, o.key))}</span>
                    </label>
                `;
            }).join('');
        } else if (isJudgeQuestion(q)) {
            inputHtml = ['正确', '错误'].map(v => {
                const inputId = `qb_${idx}_${v}`;
                return `
                    <label for="${inputId}" style="display:flex; gap:0.5rem; padding:0.45rem; border:1px solid var(--border); border-radius:8px; margin-top:0.4rem; cursor:pointer;">
                        <input type="radio" name="qb_${idx}" id="${inputId}" value="${v}">
                        <span>${v}</span>
                    </label>
                `;
            }).join('');
        } else {
            inputHtml = `
                <input type="text" id="qb_blank_${idx}" placeholder="请输入你的答案" style="margin-top:0.45rem;">
            `;
        }

        const feedbackHtml = feedback
            ? `<div style="margin-top:0.55rem; color:${feedback.ok ? '#10b981' : '#ef4444'}; font-weight:700;">结果：${escapeHtml(feedback.userDisplay || '-')} ${feedback.ok ? '正确' : '错误'}</div>`
            : '';
        answerArea = `
            <div style="margin-top:0.45rem;">${inputHtml}</div>
            <div style="display:flex; gap:0.5rem; margin-top:0.55rem;">
                <button class="btn btn-primary" onclick="submitQuestionAnswer(${idx})">提交</button>
                <button class="btn btn-secondary" onclick="showQuestionAnswer(${idx})">显示答案</button>
            </div>
            ${feedbackHtml}
            ${feedback || quizResponses[`show_${key}`] ? `<div style="margin-top:0.5rem;color:#10b981;font-weight:700;">答案：${escapeHtml(answerText)}</div>${explanation}` : ''}
        `;
    } else {
        let readonlyHtml = '';
        if (options.length > 0) {
            const multi = answers.length > 1;
            readonlyHtml = options.map(o => `
                <label style="display:flex; gap:0.5rem; padding:0.45rem; border:1px solid var(--border); border-radius:8px; margin-top:0.4rem; opacity:0.9;">
                    <input type="${multi ? 'checkbox' : 'radio'}" disabled>
                    <span style="font-family: monospace; font-weight:700;">${escapeHtml(String(o.key))}.</span>
                    <span>${escapeHtml(stripOptionPrefix(o.text, o.key))}</span>
                </label>
            `).join('');
        } else if (isJudgeQuestion(q)) {
            readonlyHtml = ['正确', '错误'].map(v => `
                <label style="display:flex; gap:0.5rem; padding:0.45rem; border:1px solid var(--border); border-radius:8px; margin-top:0.4rem; opacity:0.9;">
                    <input type="radio" disabled>
                    <span>${v}</span>
                </label>
            `).join('');
        } else {
            readonlyHtml = `<input type="text" disabled placeholder="请输入你的答案" style="margin-top:0.45rem;">`;
        }

        answerArea = `
            <div style="margin-top:0.45rem;">${readonlyHtml}</div>
            <div style="margin-top:0.5rem;color:#10b981;font-weight:700;">答案：${escapeHtml(answerText)}</div>
            ${explanation}
        `;
    }

    return `
        <div class="result-item" style="margin-bottom: 0.75rem;">
            <div style="font-weight:700;">${idx + 1}. ${escapeHtml(q.question)}</div>
            ${answerArea}
        </div>
    `;
}

function collectUserAnswer(q, idx) {
    const options = q.options || [];
    if (options.length > 0) {
        const multi = normalizeAnswerList(q).length > 1;
        if (multi) {
            return Array.from(document.querySelectorAll(`input[name="qb_${idx}"]:checked`)).map(el => String(el.value).trim());
        }
        const checked = document.querySelector(`input[name="qb_${idx}"]:checked`);
        return checked ? [String(checked.value).trim()] : [];
    }
    if (isJudgeQuestion(q)) {
        const checked = document.querySelector(`input[name="qb_${idx}"]:checked`);
        return checked ? [normalizeJudgeValue(checked.value)] : [];
    }
    const text = document.getElementById(`qb_blank_${idx}`)?.value || '';
    return text.trim() ? [text.trim()] : [];
}

function normalizeForCompare(arr, q) {
    if (isJudgeQuestion(q)) {
        return arr.map(normalizeJudgeValue).sort();
    }
    if ((q.options || []).length > 0) {
        const keyMap = optionKeyMap(q);
        return arr
            .map(v => normalizeOptionAnswerValue(v, keyMap))
            .filter(Boolean)
            .sort();
    }
    return arr.map(v => String(v).trim().toLowerCase()).sort();
}

function formatAnswerForDisplay(arr, q) {
    if (isJudgeQuestion(q) || (q.options || []).length > 0) {
        return normalizeForCompare(arr, q).join(', ');
    }
    return (arr || []).map(v => String(v).trim()).filter(Boolean).join(', ');
}

function submitQuestionAnswer(idx) {
    const q = questionBankCurrent?.questions?.[idx];
    if (!q) return;
    const userAns = collectUserAnswer(q, idx);
    if (userAns.length === 0) {
        showToast('请先作答', 'error');
        return;
    }
    const correct = normalizeAnswerList(q);
    const left = normalizeForCompare(userAns, q);
    const right = normalizeForCompare(correct, q);
    const ok = right.length === 0 ? true : (left.length === right.length && left.every((v, i) => v === right[i]));
    quizResponses[String(idx)] = {
        ok: ok,
        userDisplay: formatAnswerForDisplay(userAns, q),
        correctDisplay: formatAnswerForDisplay(correct, q),
    };
    renderQuestionBank();
}
window.submitQuestionAnswer = submitQuestionAnswer;

function showQuestionAnswer(idx) {
    quizResponses[`show_${idx}`] = true;
    renderQuestionBank();
}
window.showQuestionAnswer = showQuestionAnswer;

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
                <div style="font-size: 0.875rem; color: var(--success);">${escapeHtml(selectedFile.name)}</div>
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

function buildServiceStatusText(statusData) {
    const hubState = statusData?.hub_state || {};
    const serviceReady = Boolean(statusData?.service_ready);
    const serviceMessage = statusData?.service_message || '';
    const serviceWaiting = Boolean(statusData?.service_waiting);
    const configValid = statusData?.config_valid !== false;
    const isDeviceIdConflict = hubState?.state === 'device_id_conflict' || serviceMessage.includes('设备ID异常');

    if (isDeviceIdConflict) {
        return {
            text: '设备ID异常',
            color: '#ef4444'
        };
    }

    if (hubState.state === 'connecting' || serviceWaiting) {
        return {
            text: serviceMessage || '服务正在加载中，请稍后再试',
            color: '#f59e0b'
        };
    }

    if (hubState.state === 'error' && !serviceReady) {
        return {
            text: serviceMessage || '服务正在重新连接，请稍后再试',
            color: '#f59e0b'
        };
    }

    if (!serviceReady || !configValid) {
        return {
            text: serviceMessage || '服务暂不可用，请联系管理员',
            color: '#ef4444'
        };
    }
    if (hubState.state === 'banned') {
        return {
            text: '服务暂不可用，请联系管理员',
            color: '#ef4444'
        };
    }
    return {
        text: '就绪',
        color: '#10b981'
    };
}

function updateFooterStatus(statusData, runningCount = 0) {
    const taskStatusEl = document.getElementById('taskStatus');
    const statusDotEl = document.getElementById('serviceStatusDot');
    if (!taskStatusEl) return;

    const serviceStatus = buildServiceStatusText(statusData || {});
    taskStatusEl.textContent = runningCount > 0
        ? `${runningCount}个任务运行中 | ${serviceStatus.text}`
        : serviceStatus.text;
    if (statusDotEl) {
        statusDotEl.style.background = serviceStatus.color;
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
            if (String(data.data.browser_warmup_state || '') === 'completed') {
                document.getElementById('browserWarmupModal')?.remove();
            }
            if (
                !browserWarmupNoticeShown &&
                data.data.browser_warmup_first_run &&
                ['pending', 'running', 'error'].includes(String(data.data.browser_warmup_state || ''))
            ) {
                browserWarmupNoticeShown = true;
                showBrowserWarmupModal(
                    data.data.browser_warmup_message || '首次正在初始化浏览器环境，可能会短暂弹出空白 Chrome 窗口。如果弹出空白页，手动关闭即可。'
                );
            }
            // 不再显示服务端地址，服务端只用于心跳
            updateFooterStatus(data.data, 0);
        }
    } catch (e) {
        console.error('加载状态失败:', e);
        updateFooterStatus({ service_ready: false, config_valid: false }, 0);
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

function normalizeStandardKey(text) {
    return String(text || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}

function extractStandardCandidates(text) {
    if (!text) return [];
    const source = String(text)
        .replace(/[，；、\n\r]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();

    const patterns = [
        /[A-Z]{1,8}(?:\/[A-Z]{1,8})*\s*\d{2,6}(?:[-:/]\d{2,4})?/gi,
        /\b\d{3,6}(?:[-:/]\d{4})\b/g
    ];

    const found = [];
    patterns.forEach(pattern => {
        let match;
        while ((match = pattern.exec(source)) !== null) {
            const value = match[0].replace(/\s+/g, ' ').trim();
            if (value && !found.includes(value)) {
                found.push(value);
            }
        }
    });

    if (found.length > 0) {
        return found;
    }

    return source
        .split(/\s+/)
        .map(part => part.replace(/^[,;:：，。]+|[,;:：，。]+$/g, '').trim())
        .filter(part => /[0-9]/.test(part) && part.length >= 4);
}

function collectReferenceStandards(results, knownStandards) {
    const knownKeys = new Set((knownStandards || []).map(normalizeStandardKey).filter(Boolean));
    const collected = [];
    const excluded = [];
    const seen = new Set();

    (results || []).forEach(item => {
        if (!item || item.status !== 'success' || !item.data) return;
        const refs = extractStandardCandidates(item.data.reference_standard || '');
        refs.forEach(ref => {
            const key = normalizeStandardKey(ref);
            if (!key || seen.has(key)) return;
            if (knownKeys.has(key)) {
                excluded.push(ref);
                seen.add(key);
                return;
            }
            collected.push(ref);
            seen.add(key);
        });
    });

    return { collected, excluded };
}

function sanitizeQueryErrorMessage(rawMessage) {
    const text = String(rawMessage || '').toLowerCase();
    if (!text) return '';

    const formatKeywords = ['格式', 'invalid', '不合法', '格式错误', '标准号错误', '编号错误'];
    if (formatKeywords.some(k => text.includes(k))) {
        return '标准号格式不正确';
    }

    const noResultKeywords = ['无结果', '未找到', 'not found', 'no result', '查无', '不存在'];
    if (noResultKeywords.some(k => text.includes(k))) {
        return '查询无结果';
    }

    return '站点不可用';
}

function appendQueryExportRows(result, meta = {}) {
    const batchLabel = meta.label || '待测标准';
    const roundType = meta.isReferenceRound ? '引用标准' : '待测标准';
    const queryTime = new Date().toLocaleString('zh-CN', { hour12: false });

    (result || []).forEach(item => {
        const baseRow = {
            input_keyword: item.input_keyword || item.standard || '',
            extracted_standard: item.extracted_standard || '',
            standard_number: '',
            chinese_name: '',
            english_name: '',
            standard_status: '',
            release_date: '',
            implementation_date: '',
            cancellation_date: '',
            adopt_standard: '',
            reference_standard: '',
            replaced_standard: '',
            replacing_standard: '',
            reference_basis: '',
            standard_summary: '',
            resource: '',
            query_time: queryTime,
            status: item.status || '',
            error: sanitizeQueryErrorMessage(item.error || item.message || ''),
            note: batchLabel
        };

        if (item.status === 'success' && item.data) {
            const d = item.data;
            baseRow.standard_number = d.standard_number || '';
            baseRow.chinese_name = d.chinese_name || '';
            baseRow.english_name = d.english_name || '';
            baseRow.standard_status = d.standard_status || '';
            baseRow.release_date = d.release_date || '';
            baseRow.implementation_date = d.implementation_date || '';
            baseRow.cancellation_date = d.cancellation_date || '';
            baseRow.adopt_standard = d.adopt_standard || '';
            baseRow.reference_standard = d.reference_standard || '';
            baseRow.replaced_standard = d.replaced_standard || '';
            baseRow.replacing_standard = d.replacing_standard || '';
            baseRow.reference_basis = d.reference_basis || '';
            baseRow.standard_summary = d.standard_summary || '';
            baseRow.resource = d.resource || '';
            if (roundType) {
                baseRow.note = roundType;
            }
        }

        queryExportRows.push(baseRow);
    });
}

function renderReferenceSummary(summary) {
    const container = document.getElementById('queryReferenceSummary');
    if (!container) return;

    if (!summary || !summary.standards || summary.standards.length === 0) {
        container.style.display = 'none';
        container.innerHTML = '';
        return;
    }

    const visibleStandards = summary.standards.slice(0, 30);
    const moreCount = Math.max(0, summary.standards.length - visibleStandards.length);
    const excludedList = (summary.excluded || []).slice(0, 10);
    const excludedMore = Math.max(0, (summary.excluded || []).length - excludedList.length);

    container.innerHTML = `
        <div class="result-item" style="display: flex; flex-direction: column; gap: 0.75rem; padding: 1rem; border-left: 4px solid #f59e0b; background: rgba(245, 158, 11, 0.06);">
            <div style="display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; flex-wrap: wrap;">
                <div>
                    <div style="font-weight: 700; font-size: 1rem; color: var(--text-primary);">
                        发现 ${summary.standards.length} 条引用标准候选
                    </div>
                    <div style="margin-top: 0.25rem; font-size: 0.82rem; color: var(--text-secondary);">
                        已剔除 ${summary.excluded.length} 条与当前轮次已查询标准重复的引用项
                    </div>
                </div>
                <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
                    <button class="btn btn-success" onclick="continueReferenceQuery()">继续查询引用标准</button>
                    <button class="btn btn-secondary" onclick="dismissReferenceQuery()">暂不继续</button>
                </div>
            </div>
            <div style="font-size: 0.82rem; color: var(--text-secondary); line-height: 1.6;">
                <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 0.35rem;">引用标准列表：</div>
                <div style="display: flex; flex-wrap: wrap; gap: 0.4rem;">
                    ${visibleStandards.map(item => `<span style="display: inline-block; padding: 0.25rem 0.6rem; border-radius: 999px; background: rgba(245, 158, 11, 0.12); color: #92400e; border: 1px solid rgba(245, 158, 11, 0.25); font-family: monospace;">${escapeHtml(item)}</span>`).join('')}
                </div>
                ${moreCount > 0 ? `<div style="margin-top: 0.35rem;">还有 ${moreCount} 条未展示。</div>` : ''}
            </div>
            ${
                excludedList.length > 0 ? `
                <details style="font-size: 0.8rem; color: var(--text-secondary);">
                    <summary style="cursor: pointer; color: #b45309;">查看已排除项</summary>
                    <div style="margin-top: 0.5rem; display: flex; flex-wrap: wrap; gap: 0.4rem;">
                        ${excludedList.map(item => `<span style="display: inline-block; padding: 0.22rem 0.55rem; border-radius: 999px; background: rgba(148, 163, 184, 0.12); color: #475569; border: 1px solid rgba(148, 163, 184, 0.25); font-family: monospace;">${escapeHtml(item)}</span>`).join('')}
                    </div>
                    ${excludedMore > 0 ? `<div style="margin-top: 0.35rem;">还有 ${excludedMore} 条已排除项未展示。</div>` : ''}
                </details>
                ` : ''
            }
        </div>
    `;
    container.style.display = 'block';
}

function hideReferenceSummary() {
    const container = document.getElementById('queryReferenceSummary');
    if (!container) return;
    container.style.display = 'none';
    container.innerHTML = '';
}

function getCurrentQueryKnownStandards(results) {
    const known = [];
    (results || []).forEach(item => {
        if (item && item.extracted_standard) known.push(item.extracted_standard);
        if (item && item.input_keyword) known.push(item.input_keyword);
        if (item && item.data && item.data.standard_number) known.push(item.data.standard_number);
    });
    return known;
}

async function submitQueryBatch(standardList, options = {}) {
    const { isReferenceRound = false, roundLabel = '' } = options;
    if (!standardList || standardList.length === 0) {
        alert('未找到有效的标准号');
        return;
    }

    queryCurrentStandards = [...standardList];
    queryRoundIndex += 1;
    queryCurrentBatchMeta = {
        label: roundLabel || (isReferenceRound ? '引用标准' : '待测标准'),
        isReferenceRound
    };
    hideReferenceSummary();
    resetResult('query');
    document.getElementById('queryProgress').classList.add('active');
    const queryBtn = document.getElementById('queryBtn');
    queryBtn.disabled = true;
    queryBtn.textContent = isReferenceRound ? '引用查询中...' : '查询中...';
    document.getElementById('queryCancelBtn').style.display = 'inline-block';

    currentTask = 'query';
    startProgressTracking();

    try {
        const res = await fetch('/api/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                standards: standardList,
                include_reference_query: queryReferenceEnabled,
                query_round: queryRoundIndex,
                reference_round: isReferenceRound
            })
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

function dismissReferenceQuery() {
    queryPendingReferenceRound = null;
    hideReferenceSummary();
    queryReferenceRoundUsed = true;
}

async function continueReferenceQuery() {
    if (!queryPendingReferenceRound || !queryPendingReferenceRound.standards.length) {
        hideReferenceSummary();
        return;
    }

    const nextStandards = queryPendingReferenceRound.standards;
    queryPendingReferenceRound = null;
    queryReferenceRoundUsed = true;
    await submitQueryBatch(nextStandards, { isReferenceRound: true, roundLabel: '引用标准' });
}

// 导出查询CSV
function exportQueryCSV() {
    if (queryExportRows.length === 0) {
        alert('没有可导出的数据');
        return;
    }

    // 构建CSV内容 - 新格式：输入关键词,提取标准号,标准编号,中文名,英文名,状态,发布日期,实施日期,废止日期,采用标准,引用标准,被替代标准,替代标准,参考依据,标准摘要,来源,查询时间,查询状态,备注,错误信息
    let csv = '输入关键词,提取标准号,标准编号,中文名,英文名,状态,发布日期,实施日期,废止日期,采用标准,引用标准,被替代标准,替代标准,参考依据,标准摘要,来源,查询时间,查询状态,备注,错误信息\n';
    
    queryExportRows.forEach(item => {
        const inputKeyword = item.input_keyword || '';
        const extractedStandard = item.extracted_standard || '';
        const status = item.status || '';
        const errorMsg = item.error || '';
        const note = item.note || '';

        csv += `"${inputKeyword.replace(/"/g, '""')}","${extractedStandard.replace(/"/g, '""')}","${(item.standard_number || '').replace(/"/g, '""')}","${(item.chinese_name || '').replace(/"/g, '""')}","${(item.english_name || '').replace(/"/g, '""')}","${item.standard_status || ''}","${item.release_date || ''}","${item.implementation_date || ''}","${item.cancellation_date || ''}","${(item.adopt_standard || '').replace(/"/g, '""')}","${(item.reference_standard || '').replace(/"/g, '""')}","${(item.replaced_standard || '').replace(/"/g, '""')}","${(item.replacing_standard || '').replace(/"/g, '""')}","${(item.reference_basis || '').replace(/"/g, '""')}","${(item.standard_summary || '').replace(/"/g, '""')}","${item.resource || ''}","${(item.query_time || '').replace(/"/g, '""')}","${status}","${note.replace(/"/g, '""')}","${errorMsg.replace(/"/g, '""')}"\n`;
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

    queryReferenceEnabled = !!(document.getElementById('queryIncludeReferences')?.checked);
    queryPendingReferenceRound = null;
    queryRoundIndex = 0;
    queryReferenceRoundUsed = false;
    queryKnownStandards = new Set();
    queryExportRows = [];
    queryRoundProgressState = { main: null, reference: null };
    await submitQueryBatch(standardList, { isReferenceRound: false, roundLabel: '主标准' });
}

// 开始下载
async function startDownload() {
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
    const standardList = standards.split('\n').map(s => s.trim()).filter(s => s);

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
            body: JSON.stringify({
                standards: standardList
            })
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

let downloadSubTab = 'standard';
let techTaskListExpanded = false;
function setDownloadSubTab(tab) {
    downloadSubTab = tab;
    const standardBtn = document.getElementById('btnDownloadStandardTab');
    const techBtn = document.getElementById('btnDownloadTechTab');
    if (standardBtn) standardBtn.classList.toggle('active', tab === 'standard');
    if (techBtn) techBtn.classList.toggle('active', tab === 'tech');

    const standardPanel = document.getElementById('downloadStandardPanel');
    const techPanel = document.getElementById('downloadTechPanel');
    if (standardPanel) standardPanel.style.display = tab === 'standard' ? 'block' : 'none';
    if (techPanel) techPanel.style.display = tab === 'tech' ? 'block' : 'none';

    if (tab === 'tech') {
        if (techTaskListExpanded) {
            loadTechFileTasks();
        }
    }
}
window.setDownloadSubTab = setDownloadSubTab;

function toggleTechTaskList() {
    techTaskListExpanded = !techTaskListExpanded;
    const wrap = document.getElementById('techTaskListWrap');
    const arrow = document.getElementById('techTaskListArrow');
    if (wrap) {
        wrap.style.display = techTaskListExpanded ? 'block' : 'none';
    }
    if (arrow) {
        arrow.textContent = techTaskListExpanded ? '▲' : '▼';
    }
    if (techTaskListExpanded) {
        loadTechFileTasks();
    }
}
window.toggleTechTaskList = toggleTechTaskList;

async function createTechFileTasks() {
    const input = document.getElementById('techFileInput');
    const raw = (input?.value || '').trim();
    if (!raw) {
        alert('请输入技术文件关键词');
        return;
    }

    const keywords = raw.split('\n').map(s => s.trim()).filter(Boolean);
    if (keywords.length === 0) {
        alert('请输入技术文件关键词');
        return;
    }

    const btn = document.getElementById('techFileCreateBtn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '提交中...';
    }

    try {
        const res = await fetch('/api/tech-files/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keywords })
        });
        const data = await res.json();
        if (!data.success) {
            alert(data.message || '提交失败');
            return;
        }

        const created = data.data?.created || [];
        const suggestions = data.data?.suggestions || [];
        if (created.length > 0) {
            showToast(`已创建 ${created.length} 个后台处理任务`, 'success');
        }
        if (suggestions.length > 0) {
            const msg = suggestions.map(item => `${item.keyword} -> ${item.extracted_standard}`).join('\n');
            alert(`检测到可提取标准号，建议去“找标准”检索：\n${msg}`);
        }
        await loadTechFileTasks();
    } catch (e) {
        alert('提交失败: ' + e.message);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '提交后台处理';
        }
    }
}
window.createTechFileTasks = createTechFileTasks;

async function loadTechFileTasks() {
    const container = document.getElementById('techFileTaskList');
    if (!container) return;

    try {
        const res = await fetch('/api/tech-files/tasks');
        const data = await res.json();
        if (!data.success) {
            container.innerHTML = `<div class="empty-state">${escapeHtml(data.message || '加载任务失败')}</div>`;
            return;
        }
        const tasks = data.data || [];
        if (tasks.length === 0) {
            container.innerHTML = '<div class="empty-state">暂无任务</div>';
            return;
        }
        const header = `
            <div style="display:grid; grid-template-columns: 220px 1fr 120px 140px; gap: 10px; padding: 8px 10px; border-bottom: 1px solid var(--border); font-size: 12px; color: var(--text-secondary);">
                <div>创建时间</div>
                <div>关键词</div>
                <div>当前进度</div>
                <div>下载</div>
            </div>
        `;

        const rows = tasks.map(task => {
            const status = task.status === 'completed' ? '完成' : '处理中';
            const color = task.status === 'completed' ? '#10b981' : '#f59e0b';
            const createdAt = task.created_at ? new Date(task.created_at).toLocaleString('zh-CN') : '-';
            const keyword = escapeHtml(task.keyword || '');
            const progressText = escapeHtml(task.progress_text || status);
            const downloadUrl = sanitizeUrl(task.download_url || '');
            const action = downloadUrl !== '#'
                ? `<a href="${escapeAttr(downloadUrl)}" target="_blank" rel="noopener noreferrer" class="btn btn-success" style="padding: 0.3rem 0.6rem; text-decoration: none;">下载</a>`
                : '<span style="color: var(--text-secondary);">-</span>';
            return `
                <div style="display:grid; grid-template-columns: 220px 1fr 120px 140px; gap: 10px; padding: 10px; border-bottom: 1px solid var(--border); align-items: center;">
                    <div style="font-size: 12px; color: var(--text-secondary);">${createdAt}</div>
                    <div style="min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${keyword}">${keyword}</div>
                    <div style="font-weight: 600; color: ${color};">${progressText}</div>
                    <div>${action}</div>
                </div>
            `;
        }).join('');

        container.innerHTML = `
            <div class="result-item" style="padding: 0;">
                ${header}
                ${rows}
            </div>
        `;
    } catch (e) {
        container.innerHTML = `<div class="empty-state">加载任务失败: ${escapeHtml(e.message)}</div>`;
    }
}
window.loadTechFileTasks = loadTechFileTasks;

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
                    if (currentState.status === 'running') runningTypes.push(type);
                    if (type === 'query' && currentState.status === 'running' && Array.isArray(task.result)) {
                        renderQueryLiveProgressRows(task.result);
                    }
                    
                    // 如果状态发生变化
                    if (currentState.status !== currentState.lastStatus) {
                        console.log(`[状态变化] ${type}: ${currentState.lastStatus} -> ${currentState.status}`);
                        
                        // 根据新状态更新UI
                        switch(currentState.status) {
                            case 'running':
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
                                if (type === 'query' && Array.isArray(task.result) && task.result.length > 0) {
                                    showResult(type, task.result, task.message || task.error || '查询失败');
                                } else {
                                    showError(type, task.message || task.error || '未知错误');
                                }
                                // 重置按钮状态
                                resetButton(type);
                                break;
                                
                            case 'cancelled':
                                if (type === 'query' && Array.isArray(task.result) && task.result.length > 0) {
                                    showResult(type, task.result, '任务已终止，已保留已查询结果');
                                } else {
                                    showError(type, '任务已终止');
                                }
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
                updateFooterStatus(statusData.data, runningTypes.length);

                // 没有任务在运行时停止轮询，避免出现“状态变化错过”导致的卡住
                if (runningTypes.length === 0 && progressInterval) {
                    clearInterval(progressInterval);
                    progressInterval = null;
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
    } else if (type === 'query') {
        const details = data.details || {};
        const current = Number(details.current || 0);
        const total = Number(details.total || 0);
        const success = Number(details.success || 0);
        const skipped = Number(details.skipped || 0);
        const error = Number(details.error || 0);
        const etaSeconds = Math.max(0, Number(details.eta_seconds || 0));
        const latestStandard = details.latest_standard || details.extracted_standard || details.input_keyword || '';
        const latestStatusRaw = String(details.latest_status || '').toLowerCase();
        const stageMessage = details.stage_message || '';
        const hasRoundFlag = Object.prototype.hasOwnProperty.call(details, 'reference_round');
        const isReferenceRound = hasRoundFlag ? !!details.reference_round : !!queryCurrentBatchMeta?.isReferenceRound;
        const roundLabel = isReferenceRound ? '引用轮' : '主轮';
        const platformValue = String(details.platform || '').trim();
        const latestStatus = latestStatusRaw === 'success'
            ? '成功'
            : latestStatusRaw === 'error'
                ? '失败'
                : latestStatusRaw === 'skipped'
                    ? '跳过'
                    : latestStatusRaw === 'running'
                        ? '进行中'
                    : '';
        const etaMinute = Math.floor(etaSeconds / 60);
        const etaSecond = etaSeconds % 60;
        const etaFinish = new Date(Date.now() + etaSeconds * 1000);
        const etaClock = `${String(etaFinish.getHours()).padStart(2, '0')}:${String(etaFinish.getMinutes()).padStart(2, '0')}:${String(etaFinish.getSeconds()).padStart(2, '0')}`;
        const etaText = `预计剩余：${etaMinute}分${etaSecond}秒（约 ${etaClock} 完成）`;

        const line1 = total > 0
            ? `查询进度：${current}/${total}（${percent}%）`
            : `查询进度：${percent}%`;
        const line2 = `结果统计：成功 ${success}，失败 ${error}，跳过 ${skipped}`;
        const line3 = latestStandard
            ? `当前/最新：${latestStandard}${latestStatus ? `（${latestStatus}）` : ''}`
            : (data.message || '正在查询中...');
        const line4 = stageMessage ? `当前动作：${stageMessage}` : '';
        const currentRoundState = {
            line1, line2, line3, line4, etaText, percent, current, total
        };
        if (hasRoundFlag || (current > 0 || total > 0 || stageMessage)) {
            if (isReferenceRound) {
                queryRoundProgressState.reference = currentRoundState;
            } else {
                queryRoundProgressState.main = currentRoundState;
            }
        }

        const mainText = queryRoundProgressState.main
            ? `主轮：${queryRoundProgressState.main.line1} | ${queryRoundProgressState.main.line2} | ${queryRoundProgressState.main.etaText}`
            : '主轮：未开始';
        const refText = queryRoundProgressState.reference
            ? `引用轮：${queryRoundProgressState.reference.line1} | ${queryRoundProgressState.reference.line2} | ${queryRoundProgressState.reference.etaText}`
            : '引用轮：未开始';
        const focusState = isReferenceRound ? queryRoundProgressState.reference : queryRoundProgressState.main;
        const focusLine3 = focusState?.line3 || '';
        const focusLine4 = focusState?.line4 || '';
        const rollingParts = [];
        rollingParts.push(roundLabel);
        rollingParts.push(total > 0 ? `${current}/${total}（${percent}%）` : `${percent}%`);
        rollingParts.push(`成功 ${success}`);
        rollingParts.push(`失败 ${error}`);
        rollingParts.push(`跳过 ${skipped}`);
        if (latestStandard) {
            rollingParts.push(`当前 ${latestStandard}${latestStatus ? `（${latestStatus}）` : ''}`);
        }
        if (platformValue) {
            rollingParts.push(String(platformValue));
        }
        if (stageMessage) {
            rollingParts.push(stageMessage);
        } else if (data.message) {
            rollingParts.push(data.message);
        }
        if (etaSeconds > 0) {
            rollingParts.push(etaText);
        }
        const detailText = rollingParts.join(' | ');

        if (detailEl) {
            detailEl.textContent = detailText;
            detailEl.style.cssText = 'font-size: 0.9rem; color: var(--text-primary); margin-top: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.5;';
            detailEl.title = detailText;
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

function renderQueryLiveProgressRows(rows) {
    const container = document.getElementById('queryResult');
    if (!container || !Array.isArray(rows)) return;
    if (rows.length === 0) return;

    const latestRows = rows.slice(-12).reverse();
    const html = latestRows.map(item => {
        const status = item.status || '';
        const inputKeyword = escapeHtml(item.input_keyword || '-');
        const extractedStandard = escapeHtml(item.extracted_standard || item.input_keyword || '-');
        const matchedStandard = escapeHtml(item.data?.standard_number || '-');
        const validityStatus = escapeHtml(item.data?.standard_status || '-');
        const statusText = status === 'success' ? '成功' : status === 'error' ? '失败' : status === 'skipped' ? '跳过' : status;
        const statusColor = status === 'success' ? '#10b981' : status === 'error' ? '#ef4444' : '#f59e0b';
        const message = escapeHtml(item.error || item.message || '');

        const left = status === 'success'
            ? `
                <div style="display:grid; grid-template-columns:minmax(120px,1.2fr) minmax(140px,1.1fr) minmax(88px,0.8fr); gap:0.75rem; width:100%; min-width:0;">
                    <div style="min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${inputKeyword}">${inputKeyword}</div>
                    <div style="min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text-primary); font-family:monospace;" title="${matchedStandard}">${matchedStandard}</div>
                    <div style="min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:${validityStatus.includes('现') ? '#10b981' : validityStatus.includes('废') ? '#ef4444' : '#f59e0b'}; font-weight:700;" title="${validityStatus}">${validityStatus}</div>
                </div>
            `
            : `
                <div style="display:grid; grid-template-columns:minmax(120px,1.2fr) minmax(140px,1.1fr) minmax(88px,0.8fr); gap:0.75rem; width:100%; min-width:0;">
                    <div style="min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${inputKeyword}">${inputKeyword}</div>
                    <div style="min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text-secondary); font-family:monospace;" title="${extractedStandard}">${extractedStandard}</div>
                    <div style="min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:${statusColor}; font-weight:700;" title="${statusText}">${statusText}</div>
                </div>
            `;

        return `
            <div class="result-item" style="display:flex; justify-content:space-between; align-items:center; padding:0.55rem 0.75rem; margin-bottom:0.45rem; gap:0.75rem;">
                ${left}
                <div style="display:flex; align-items:center; gap:0.5rem; margin-left:0.5rem; flex-shrink:0;">
                    ${message ? `<span style="color:var(--text-secondary); font-size:0.8rem; max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${message}">${message}</span>` : ''}
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = `
        <div style="margin-bottom:0.6rem; color:var(--text-secondary); font-size:0.82rem;">
            已返回结果（最新 12 条，完整结果将在结束后展示）：
        </div>
        <div style="display:grid; grid-template-columns:minmax(120px,1.2fr) minmax(140px,1.1fr) minmax(88px,0.8fr); gap:0.75rem; padding:0 0.75rem 0.45rem; color:var(--text-secondary); font-size:0.8rem; font-weight:600;">
            <div>输入关键词</div>
            <div>查到的标准号</div>
            <div>有效状态</div>
        </div>
        ${html}
    `;
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
                        container.innerHTML = `<div class="empty-state">提取到: "${escapeHtml(contentPreview)}..."<br>未提取到任何标准号</div>`;
                    } else {
                        container.innerHTML = '<div class="empty-state">未提取到任何标准号</div>';
                    }
                })
                .catch(() => {
                    container.innerHTML = '<div class="empty-state">未提取到任何标准号</div>';
                });
        } else {
            // 添加统计信息
            const totalCount = uniqueResults.length;
            const summaryDiv = document.createElement('div');
            summaryDiv.style.cssText = 'margin-bottom: 1rem; padding: 1rem; background: var(--bg-secondary); border-radius: 8px; text-align: center; font-size: 1.1rem; border: 1px solid var(--border); position: relative;';
            summaryDiv.innerHTML = `
                <button onclick="this.parentElement.remove()" style="position: absolute; top: 8px; right: 8px; width: 24px; height: 24px; border: none; background: rgba(0,0,0,0.1); border-radius: 50%; cursor: pointer; font-size: 16px; line-height: 1; display: flex; align-items: center; justify-content: center; color: var(--text-secondary); padding: 0;">×</button>
                <span style="color: #10b981; font-weight: 700;">共提取到 ${totalCount} 个标准号候选</span>
            `;
            container.appendChild(summaryDiv);

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
                        <span>${escapeHtml(item.standard || item.text || '')}</span>
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
            appendQueryExportRows(result, queryCurrentBatchMeta);
            if (message) {
                const banner = document.createElement('div');
                banner.className = 'result-item';
                banner.style.cssText = 'padding: 0.75rem 0.9rem; margin-bottom: 0.75rem; border-left: 4px solid #f59e0b; background: rgba(245, 158, 11, 0.08); color: var(--text-primary);';
                banner.textContent = message;
                container.appendChild(banner);
            }
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
                position: relative;
                padding-right: 3rem;
            `;

            // 添加关闭按钮
            const closeBtn = document.createElement('button');
            closeBtn.innerHTML = '×';
            closeBtn.style.cssText = `
                position: absolute;
                top: 8px;
                right: 8px;
                width: 24px;
                height: 24px;
                border: none;
                background: rgba(0,0,0,0.1);
                border-radius: 50%;
                cursor: pointer;
                font-size: 18px;
                line-height: 1;
                display: flex;
                align-items: center;
                justify-content: center;
                color: var(--text-secondary);
                padding: 0;
            `;
            closeBtn.onclick = function() { div.remove(); };
            div.appendChild(closeBtn);

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
                <span style="font-weight: 700; font-size: 1.1rem;">${escapeHtml(titleText)}</span>
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
                    if (data.standard_number) detailsHtml += `<div><strong>标准编号：</strong>${escapeHtml(data.standard_number)}</div>`;
                    // 中文名
                    if (data.chinese_name) detailsHtml += `<div><strong>中文名：</strong>${escapeHtml(data.chinese_name)}</div>`;
                    // 英文名
                    if (data.english_name) detailsHtml += `<div><strong>英文名：</strong>${escapeHtml(data.english_name)}</div>`;
                    // 状态 - 高亮加粗显示
                    if (data.standard_status) {
                        const statusColor = data.standard_status.includes('现行') ? '#10b981' : 
                                          data.standard_status.includes('废止') ? '#ef4444' : '#f59e0b';
                        detailsHtml += `<div style="font-size: 1.1rem; margin: 0.5rem 0;"><strong style="color: ${statusColor}; font-size: 1.2rem;">★ 标准状态：${escapeHtml(data.standard_status)}</strong></div>`;
                    }
                    // 发布日期
                    if (data.release_date) detailsHtml += `<div><strong>发布日期：</strong>${escapeHtml(data.release_date)}</div>`;
                    // 实施日期
                    if (data.implementation_date) detailsHtml += `<div><strong>实施日期：</strong>${escapeHtml(data.implementation_date)}</div>`;
                    // 废止日期
                    if (data.cancellation_date) detailsHtml += `<div><strong>废止日期：</strong>${escapeHtml(data.cancellation_date)}</div>`;
                    // 采用标准
                    if (data.adopt_standard) detailsHtml += `<div><strong>采用标准：</strong>${escapeHtml(data.adopt_standard)}</div>`;
                    // 引用标准
                    if (data.reference_standard) detailsHtml += `<div><strong>引用标准：</strong>${escapeHtml(data.reference_standard)}</div>`;
                    // 被替代标准
                    if (data.replaced_standard) detailsHtml += `<div><strong>被替代标准：</strong>${escapeHtml(data.replaced_standard)}</div>`;
                    // 替代标准
                    if (data.replacing_standard) detailsHtml += `<div><strong>替代标准：</strong>${escapeHtml(data.replacing_standard)}</div>`;
                    // 参考依据
                    if (data.reference_basis) detailsHtml += `<div><strong>参考依据：</strong>${escapeHtml(data.reference_basis)}</div>`;
                    // 标准摘要
                    if (data.standard_summary) detailsHtml += `<div><strong>标准摘要：</strong>${escapeHtml(data.standard_summary)}</div>`;
                    // 附修订
                    if (data.supplementary_revision) detailsHtml += `<div><strong>附修订：</strong>${escapeHtml(data.supplementary_revision)}</div>`;
                    // 来源
                    if (data.resource) detailsHtml += `<div style="margin-top: 0.5rem; color: #6b7280; font-size: 0.75rem;">来源: ${escapeHtml(data.resource)}</div>`;
                    
                    detailsDiv.innerHTML = detailsHtml || '<div style="color: #6b7280;">查询成功但未返回详细信息</div>';
                } else if (type === 'download') {
                    const platformResults = item.results || [];
                    
                    if (platformResults.length > 0) {
                        // 显示查询到的标准号
                        const extractedStandard = item.extracted_standard || '';
                        let standardInfoHtml = '';
                        if (extractedStandard) {
                            standardInfoHtml = `<div style="margin-bottom: 0.75rem; padding: 0.5rem; background: rgba(59, 130, 246, 0.1); border-radius: 4px; color: #3b82f6; font-size: 0.95rem; font-weight: 500;">
                                <strong>查询标准号：</strong>${escapeHtml(extractedStandard)}
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
                            const actualStandard = escapeHtml(pr.standard_number || '-');
                            
                            // 获取文件名
                            let fileName = '-';
                            let fileNameDisplay = '-';
                            if (pr.file_path) {
                                fileName = pr.file_path.split(/[\\/]/).pop();
                                fileNameDisplay = `<span style="font-size: 0.9rem; color: var(--text-primary); font-family: monospace; font-weight: 500;">${escapeHtml(fileName)}</span>`;
                            } else if (pr.view_url) {
                                fileNameDisplay = '<span style="color: var(--text-primary); font-size: 0.9rem;">在线链接</span>';
                            }
                            
                            let actionHtml = '-';
                            if (isSuccess) {
                                if (pr.file_path) {
                                    const encodedFilePath = encodeURIComponent(pr.file_path || '');
                                    actionHtml = `<button onclick="openFolder(decodeURIComponent('${encodedFilePath}'))" style="padding: 0.4rem 0.75rem; background: #10b981; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.95rem; font-weight: 500;">打开文件夹</button>`;
                                } else if (pr.view_url) {
                                    const safeViewUrl = sanitizeUrl(pr.view_url);
                                    actionHtml = safeViewUrl === '#'
                                        ? '<span style="color: #9ca3af; font-size: 0.9rem;">链接无效</span>'
                                        : `<a href="${escapeAttr(safeViewUrl)}" target="_blank" rel="noopener noreferrer" style="padding: 0.4rem 0.75rem; background: #3b82f6; color: white; border-radius: 6px; text-decoration: none; font-size: 0.95rem; font-weight: 500;">在线查看</a>`;
                                }
                            } else {
                                actionHtml = `<span style="color: #9ca3af; font-size: 0.9rem;">${escapeHtml(pr.message || '无结果')}</span>`;
                            }
                            
                            tableHtml += `
                                <tr>
                                    <td style="padding: 0.75rem; border-bottom: 1px solid var(--border); font-size: 1rem;">${escapeHtml(pr.platform || '')}</td>
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
                let skipHtml = `<div style="color: #f59e0b;"><strong>原因：</strong>${escapeHtml(errorMsg)}</div>`;
                
                skipHtml += `<div style="margin-top: 0.5rem; padding: 0.5rem; background: rgba(245, 158, 11, 0.1); border-radius: 4px; color: #d97706; font-size: 0.75rem;">
                    <strong>提示：</strong>系统无法从输入文本中提取标准号。请检查输入格式，确保包含有效的标准编号（如GB/T 19001-2016、ISO 9001:2015等）
                </div>`;
                
                detailsDiv.innerHTML = skipHtml;
            } else {
                // 失败时显示详细错误
                const errorMsg = item.error || item.message || '未知错误';
                const platform = item.platform || '';
                
                let errorHtml = `<div style="color: #ef4444;"><strong>错误：</strong>${escapeHtml(errorMsg)}</div>`;
                if (platform) {
                    errorHtml += `<div style="margin-top: 0.25rem; color: #6b7280; font-size: 0.75rem;">平台: ${escapeHtml(platform)}</div>`;
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

        if (type === 'query') {
            const knownStandards = getCurrentQueryKnownStandards(result);
            queryKnownStandards = new Set(knownStandards);
            const refInfo = collectReferenceStandards(result, knownStandards);

            if (queryReferenceEnabled && !queryCurrentBatchMeta.isReferenceRound && !queryReferenceRoundUsed && refInfo.collected.length > 0) {
                queryPendingReferenceRound = {
                    standards: refInfo.collected,
                    excluded: refInfo.excluded,
                    sourceStandards: knownStandards,
                    round: queryRoundIndex
                };
                renderReferenceSummary(queryPendingReferenceRound);
            } else {
                queryPendingReferenceRound = null;
                hideReferenceSummary();
            }
        }
        
        // 添加统计信息
        const successCount = result.filter(r => r.status === 'success').length;
        const failCount = result.length - successCount;
        const summaryDiv = document.createElement('div');
        summaryDiv.style.cssText = 'margin-top: 1rem; padding: 1rem; background: var(--bg-secondary); border-radius: 8px; text-align: center; font-size: 1.1rem; border: 1px solid var(--border); position: relative;';
        summaryDiv.innerHTML = `
            <button onclick="this.parentElement.remove()" style="position: absolute; top: 8px; right: 8px; width: 24px; height: 24px; border: none; background: rgba(0,0,0,0.1); border-radius: 50%; cursor: pointer; font-size: 16px; line-height: 1; display: flex; align-items: center; justify-content: center; color: var(--text-secondary); padding: 0;">×</button>
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
            <span style="font-weight: 700; font-size: 1.1rem;">${escapeHtml(titleText)}</span>
            <span style="color: ${statusColor}; font-weight: 700; font-size: 1.1rem;">${statusText}</span>
        `;
        div.appendChild(titleRow);
        
        // 详情表格
        const detailsDiv = document.createElement('div');
        detailsDiv.style.cssText = 'font-size: 0.95rem; color: var(--text-secondary); line-height: 1.6;';
        
        if (isSuccess) {
            const platformResults = item.results || [];
            const hasUsableResult = platformResults.some(pr => pr && pr.status === 'success' && (pr.file_path || pr.view_url));
            if (platformResults.length > 0) {
                // 显示查询到的标准号
                let standardInfoHtml = '';
                if (extractedStandard) {
                    standardInfoHtml = `<div style="margin-bottom: 0.75rem; padding: 0.5rem; background: rgba(59, 130, 246, 0.1); border-radius: 4px; color: #3b82f6; font-size: 0.95rem; font-weight: 500;">
                        <strong>查询标准号：</strong>${escapeHtml(extractedStandard)}
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
                    const actualStandard = escapeHtml(pr.standard_number || '-');
                    
                    // 获取文件名
                    let fileName = '-';
                    let fileNameDisplay = '-';
                    if (pr.file_path) {
                        fileName = pr.file_path.split(/[\\/]/).pop();
                        fileNameDisplay = `<span style="font-size: 0.9rem; color: var(--text-primary); font-family: monospace; font-weight: 500;">${escapeHtml(fileName)}</span>`;
                    } else if (pr.view_url) {
                        fileNameDisplay = '<span style="color: var(--text-primary); font-size: 0.9rem;">在线链接</span>';
                    }
                    
                    let actionHtml = '-';
                    if (prSuccess) {
                        if (pr.file_path) {
                            const encodedFilePath = encodeURIComponent(pr.file_path || '');
                            actionHtml = `<button onclick="openFolder(decodeURIComponent('${encodedFilePath}'))" style="padding: 0.4rem 0.75rem; background: #10b981; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.95rem; font-weight: 500;">打开文件夹</button>`;
                        } else if (pr.view_url) {
                            const safeViewUrl = sanitizeUrl(pr.view_url);
                            actionHtml = safeViewUrl === '#'
                                ? '<span style="color: #9ca3af; font-size: 0.9rem;">链接无效</span>'
                                : `<a href="${escapeAttr(safeViewUrl)}" target="_blank" rel="noopener noreferrer" style="padding: 0.4rem 0.75rem; background: #3b82f6; color: white; border-radius: 6px; text-decoration: none; font-size: 0.95rem; font-weight: 500;">在线查看</a>`;
                        }
                    } else {
                        actionHtml = `<span style="color: #9ca3af; font-size: 0.9rem;">${escapeHtml(pr.message || '无结果')}</span>`;
                    }
                    
                    tableHtml += `
                        <tr>
                            <td style="padding: 0.75rem; border-bottom: 1px solid var(--border); font-size: 1rem;">${escapeHtml(pr.platform || '')}</td>
                            <td style="padding: 0.75rem; text-align: center; border-bottom: 1px solid var(--border); color: ${prStatusColor}; font-size: 1rem; font-weight: 600;">${prStatusText}</td>
                            <td style="padding: 0.75rem; border-bottom: 1px solid var(--border); font-size: 1rem; color: #3b82f6; font-weight: 500;">${actualStandard}</td>
                            <td style="padding: 0.75rem; border-bottom: 1px solid var(--border);">${fileNameDisplay}</td>
                            <td style="padding: 0.75rem; text-align: center; border-bottom: 1px solid var(--border);">${actionHtml}</td>
                        </tr>
                    `;
                });
                
                tableHtml += '</tbody></table>';
                if (!hasUsableResult) {
                    const payload = encodeURIComponent(JSON.stringify({
                        input_keyword: item.input_keyword || '',
                        extracted_standard: item.extracted_standard || '',
                        results: item.results || []
                    }));
                    tableHtml += `
                        <div style="margin-top: 0.75rem; display: flex; gap: 0.5rem; align-items: center;">
                            <button class="btn btn-primary" onclick="requestDownloadHelp('${payload}')">无可用结果，一键求助后台</button>
                            <span style="color: var(--text-secondary); font-size: 0.85rem;">一般排队需要数小时，注意查看右下角信息</span>
                        </div>
                    `;
                }
                detailsDiv.innerHTML = tableHtml;
            } else {
                const payload = encodeURIComponent(JSON.stringify({
                    input_keyword: item.input_keyword || '',
                    extracted_standard: item.extracted_standard || '',
                    results: item.results || []
                }));
                detailsDiv.innerHTML = `
                    <div style="color: #6b7280;">无下载结果</div>
                    <div style="margin-top: 0.75rem; display: flex; gap: 0.5rem; align-items: center;">
                        <button class="btn btn-primary" onclick="requestDownloadHelp('${payload}')">发送给后台求助</button>
                        <span style="color: var(--text-secondary); font-size: 0.85rem;">一般排队需要数小时，注意查看右下角信息</span>
                    </div>
                `;
            }
        } else if (isSkipped) {
            const errorMsg = item.error || '未提取到标准号';
            const payload = encodeURIComponent(JSON.stringify({
                input_keyword: item.input_keyword || '',
                extracted_standard: item.extracted_standard || '',
                results: item.results || []
            }));
            detailsDiv.innerHTML = `
                <div style="color: #f59e0b;"><strong>原因：</strong>${escapeHtml(errorMsg)}</div>
                <div style="margin-top: 0.75rem; display: flex; gap: 0.5rem; align-items: center;">
                    <button class="btn btn-primary" onclick="requestDownloadHelp('${payload}')">发送给后台求助</button>
                    <span style="color: var(--text-secondary); font-size: 0.85rem;">一般排队需要数小时，注意查看右下角信息</span>
                </div>
            `;
        } else {
            const errorMsg = item.error || item.message || '未知错误';
            const payload = encodeURIComponent(JSON.stringify({
                input_keyword: item.input_keyword || '',
                extracted_standard: item.extracted_standard || '',
                results: item.results || []
            }));
            detailsDiv.innerHTML = `
                <div style="color: #ef4444;"><strong>错误：</strong>${escapeHtml(errorMsg)}</div>
                <div style="margin-top: 0.75rem; display: flex; gap: 0.5rem; align-items: center;">
                    <button class="btn btn-primary" onclick="requestDownloadHelp('${payload}')">发送给后台求助</button>
                    <span style="color: var(--text-secondary); font-size: 0.85rem;">一般排队需要数小时，注意查看右下角信息</span>
                </div>
            `;
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
        perPageSelect.setAttribute('data-smart-dropdown', '1');
        bindSmartDropdown(perPageSelect);
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
    summaryDiv.style.cssText = 'margin-top: 1rem; padding: 1rem; background: var(--bg-secondary); border-radius: 8px; text-align: center; font-size: 1.1rem; border: 1px solid var(--border); position: relative;';
    summaryDiv.innerHTML = `
        <button onclick="this.parentElement.remove()" style="position: absolute; top: 8px; right: 8px; width: 24px; height: 24px; border: none; background: rgba(0,0,0,0.1); border-radius: 50%; cursor: pointer; font-size: 16px; line-height: 1; display: flex; align-items: center; justify-content: center; color: var(--text-secondary); padding: 0;">×</button>
        <span style="color: #10b981; font-weight: 700;">成功: ${successCount}</span>
        <span style="margin: 0 1.5rem; color: var(--text-secondary);">|</span>
        <span style="color: #ef4444; font-weight: 700;">失败: ${failCount}</span>
        <span style="margin: 0 1.5rem; color: var(--text-secondary);">|</span>
        <span style="color: var(--text-primary); font-weight: 500;">总计: ${downloadResults.length}</span>
    `;
    container.appendChild(summaryDiv);

    // 显示工具栏
    const toolbarEl = document.getElementById('downloadToolbar');
    if (toolbarEl) toolbarEl.style.display = 'flex';
}

// 生成模拟下载数据
function generateMockDownloadData(count = 25) {
    const platforms = ['食品伙伴网', '国家标准全文公开系统'];
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
    const safeMessage = escapeHtml(message || '您的客户端版本过低，需要升级后才能继续使用。');
    
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
                ${safeMessage}
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

function showBrowserWarmupModal(message) {
    const existing = document.getElementById('browserWarmupModal');
    if (existing) {
        const msgEl = existing.querySelector('[data-role="message"]');
        if (msgEl) msgEl.textContent = message || '首次正在初始化浏览器环境，可能会短暂弹出空白 Chrome 窗口。如果弹出空白页，手动关闭即可。';
        return;
    }

    const modal = document.createElement('div');
    modal.id = 'browserWarmupModal';
    modal.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.65); z-index: 9998;
        display: flex; align-items: center; justify-content: center;
        padding: 20px;
    `;
    modal.innerHTML = `
        <div style="background: var(--bg-secondary); padding: 1.4rem 1.5rem; border-radius: 12px; max-width: 460px; width: 100%; border: 1px solid rgba(59,130,246,0.25); box-shadow: 0 18px 50px rgba(0,0,0,0.28);">
            <div style="font-size: 1.05rem; font-weight: 700; color: var(--text-primary); margin-bottom: 0.75rem;">首次初始化浏览器环境</div>
            <div data-role="message" style="font-size: 0.92rem; color: var(--text-secondary); line-height: 1.65; margin-bottom: 1rem;">
                ${escapeHtml(message || '首次正在初始化浏览器环境，可能会短暂弹出空白 Chrome 窗口。如果弹出空白页，手动关闭即可。')}
            </div>
            <div style="font-size: 0.82rem; color: var(--text-secondary); line-height: 1.6; margin-bottom: 1rem;">
                如果弹出空白页，手动关闭即可。
            </div>
            <div style="display:flex; justify-content:flex-end;">
                <button id="browserWarmupModalClose" style="padding: 0.65rem 1rem; background: #3b82f6; color: #fff; border: none; border-radius: 8px; font-weight: 600; cursor: pointer;">
                    知道了
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    document.getElementById('browserWarmupModalClose')?.addEventListener('click', () => {
        modal.remove();
    });
}

// 显示错误
function showError(type, message) {
    const container = document.getElementById(type + 'Result');
    if (!container) return;

    if (type === 'query') {
        queryPendingReferenceRound = null;
        hideReferenceSummary();
    }
    
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

    const safeErrorTitle = escapeHtml(errorTitle);
    const safeErrorDetails = escapeHtml(errorDetails);
    const safeMessage = escapeHtml(message || '');
    
    let suggestionsHtml = '';
    if (suggestions.length > 0) {
        suggestionsHtml = `
            <div style="margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid rgba(239, 68, 68, 0.2);">
                <div style="font-weight: 600; margin-bottom: 0.5rem; color: #dc2626;">解决建议：</div>
                ${suggestions.map(s => `<div style="margin: 0.25rem 0; padding-left: 0.5rem; border-left: 2px solid #fecaca;">${escapeHtml(s)}</div>`).join('')}
            </div>
        `;
    }
    
    container.innerHTML = `
        <div style="padding: 1rem; background: rgba(239, 68, 68, 0.08); border-radius: 8px; border: 1px solid rgba(239, 68, 68, 0.3);">
            <div style="display: flex; align-items: center; margin-bottom: 0.5rem;">
                <span style="font-size: 1.25rem; margin-right: 0.5rem;">✗</span>
                <span style="font-weight: 700; color: #dc2626; font-size: 1rem;">${safeErrorTitle}</span>
            </div>
            <div style="color: #991b1b; margin-bottom: 0.5rem; font-size: 0.9rem;">${safeErrorDetails}</div>
            ${message !== errorDetails ? `<div style="font-size: 0.8rem; color: #7f1d1d; background: rgba(239, 68, 68, 0.1); padding: 0.5rem; border-radius: 4px; font-family: monospace; margin-bottom: 0.5rem;">详细信息: ${safeMessage}</div>` : ''}
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

// 清空结果（关闭所有结果框）
function clearResult(type) {
    const resultEl = document.getElementById(type + 'Result');
    const progressEl = document.getElementById(type + 'Progress');
    const toolbarEl = document.getElementById(type + 'Toolbar');

    if (resultEl) resultEl.innerHTML = '';
    if (progressEl) progressEl.classList.remove('active');
    if (toolbarEl) toolbarEl.style.display = 'none';

    // 重置对应全局变量
    if (type === 'query') {
        queryResults = [];
        queryExportRows = [];
    } else if (type === 'download') {
        downloadResults = [];
    }
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
            if (taskType === 'query') {
                const detailEl = document.getElementById('queryDetail');
                if (detailEl) {
                    detailEl.textContent = '主轮：已终止\n引用轮：已终止\n查询已终止';
                    detailEl.style.cssText = 'font-size: 0.9rem; color: #ef4444; margin-top: 0; white-space: pre-line; line-height: 1.5;';
                }
            }
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

// 下载求助
async function sendDownloadHelpPayload(payloadObj) {
    try {
        const res = await fetch('/api/download/help', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payloadObj || {})
        });
        const data = await res.json();
        if (data.success) {
            showToast('已发送给后台，可能需要排队数小时，请留意右下角信息', 'success');
        } else {
            showToast(data.message || '发送失败', 'error');
        }
    } catch (e) {
        console.error('sendDownloadHelpPayload failed:', e);
        showToast('发送失败: ' + e.message, 'error');
    }
}

async function requestDownloadHelp(encodedPayload) {
    try {
        const payload = JSON.parse(decodeURIComponent(encodedPayload || '{}'));
        await sendDownloadHelpPayload(payload);
    } catch (e) {
        console.error('requestDownloadHelp failed:', e);
        showToast('发送失败: ' + e.message, 'error');
    }
}
window.requestDownloadHelp = requestDownloadHelp;

// 消息中心
function toggleChat() {
    const widget = document.getElementById('chatWidget');
    if (!widget) return;
    
    widget.classList.toggle('collapsed');
    const toggleBtn = document.getElementById('chatToggle');
    if (toggleBtn) {
        toggleBtn.textContent = widget.classList.contains('collapsed') ? '▲' : '▼';
    }

    // 打开消息中心即视为已读，清空红点
    if (!widget.classList.contains('collapsed')) {
        clearChatBadge();
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

function clearChatBadge() {
    const badge = document.getElementById('chatBadge');
    if (!badge) return;
    badge.textContent = '0';
    badge.classList.remove('show');
}

function incrementChatBadge(delta) {
    const badge = document.getElementById('chatBadge');
    if (!badge) return;
    const current = parseInt(badge.textContent) || 0;
    const next = current + (parseInt(delta) || 0);
    badge.textContent = String(next);
    if (next > 0) {
        badge.classList.add('show');
    } else {
        badge.classList.remove('show');
    }
}

function addChatMessage(type, content, msgId, save = true) {
    const container = document.getElementById('chatMessages');
    if (!container) return false;
    
    // 如果有msgId，检查是否已存在
    if (msgId && container.querySelector(`[data-msg-id="${msgId}"]`)) {
        return false;
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

    return true;
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
                        if (addChatMessage('server', msg.content, msg.id)) {
                            newChatCount++;
                        }
                    }
                });
                
                // 如果有新广播，更新显示
                if (newBroadcastCount > 0) {
                    updateBroadcastDisplay();
                }
                
                if (newChatCount > 0) {
                    const widget = document.getElementById('chatWidget');
                    const isCollapsed = widget ? widget.classList.contains('collapsed') : true;
                    if (isCollapsed) {
                        incrementChatBadge(newChatCount);
                    } else {
                        clearChatBadge();
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

function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function sanitizeUrl(url) {
    try {
        const parsed = new URL(String(url || ''), window.location.origin);
        if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
            return parsed.href;
        }
    } catch (e) {
        return '#';
    }
    return '#';
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
