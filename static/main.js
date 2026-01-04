let currentEventSource = null;
let aiMessageDiv = null;
let finalAnswer = null;
let thoughtQuote = null;
let chatMessages = null;
let chat_state = 'none';
// 添加聊天历史记录变量
let chatHistory = [];
let currentSessionId = null;
// 新增：当前模式（chat | flow），默认 chat
let currentMode = 'chat';
// 修改：简化交互相关变量，只保留必要的
let _currentInteractionTaskId = null;

// 新增：初始化交互提示元素
function initializeInteractionElements() {
    // 这个函数在原始代码中存在，用于初始化交互元素
    console.log('交互提示元素初始化函数已定义');
}

// 新增：显示交互提示
function showInteractionPrompt(prompt, taskId) {
    // 这个函数在原始代码中存在，用于显示交互提示
    console.log('显示交互提示:', prompt, '任务ID:', taskId);
}

// 新增：全局ask_human状态管理，防止重复处理
let globalAskHumanProcessed = false;
let globalProcessedInquire = null;
let globalProcessedTaskId = null;

// Ask Human 相关常量
const ASK_HUMAN_CONSTANTS = {
    TOOL_COMPLETED_MARKER: 'Tool \'ask_human\' completed its mission!',
    INTERACTION_REQUIRED_MARKER: 'INTERACTION_REQUIRED:',
    TOOL_NAME: 'ask_human',
    HUMAN_INTERACTION_REQUIRED: 'Human interaction required:',
    WAITING_FOR_RESPONSE: 'Waiting for human response'
};

// 新增：记录前一条消息的类型，用于决定think容器的创建策略
let lastMessageType = null; // 'user', 'ai', 'system', null

/**
 * 通用的 ask_human 检测器
 * @param {Object} data - 事件数据
 * @param {string} type - 事件类型
 * @param {boolean} isFlow - 是否为 flow 模式
 * @param {string} taskId - 任务/流程 ID
 * @returns {Object|null} 检测结果 {inquire: string, detected: boolean} 或 null
 */
function detectAskHuman(data, type, isFlow, taskId) {
    if (!data.result || typeof data.result !== 'string' || globalAskHumanProcessed) {
        return null;
    }

    let inquire = null;
    let detected = false;

    // 优先级1：检测ask_human工具执行完成的情况（最高优先级）
    if (data.result.includes(ASK_HUMAN_CONSTANTS.TOOL_COMPLETED_MARKER)) {
        console.log(`Detected ${isFlow ? 'flow' : 'task'} ask_human tool completion`);
        const interactionMatch = data.result.match(/INTERACTION_REQUIRED:\s*(.+)/);
        if (interactionMatch) {
            inquire = interactionMatch[1].trim();
            detected = true;
        }
    }
    // 优先级2：检测直接的INTERACTION_REQUIRED标记
    else if (data.result.includes(ASK_HUMAN_CONSTANTS.INTERACTION_REQUIRED_MARKER)) {
        console.log(`Detected ${isFlow ? 'flow' : 'task'} INTERACTION_REQUIRED marker`);
        inquire = data.result.split(ASK_HUMAN_CONSTANTS.INTERACTION_REQUIRED_MARKER).pop().trim();
        detected = true;
    }
    // 优先级3：检测ask_human工具的使用（仅在tool类型事件中）
    else if (type === 'tool' && data.result.includes(ASK_HUMAN_CONSTANTS.TOOL_NAME)) {
        console.log(`Detected ${isFlow ? 'flow' : 'task'} ask_human tool usage`);

        // 从JSON格式的tool arguments中提取
        const toolArgsMatch = data.result.match(/Tool arguments: ({[^}]+})/);
        if (toolArgsMatch) {
            try {
                const toolArgs = JSON.parse(toolArgsMatch[1]);
                if (toolArgs.inquire) {
                    inquire = toolArgs.inquire;
                    detected = true;
                }
            } catch (jsonError) {
                console.log(`JSON parsing failed for ${isFlow ? 'flow' : 'task'} tool args`);
            }
        }

        // 如果JSON解析失败，尝试其他提取方法
        if (!inquire) {
            const inquireMatch = data.result.match(/inquire["\s]*:["\s]*([^,\n}]+)/);
            if (inquireMatch) {
                inquire = inquireMatch[1].trim().replace(/["']/g, '');
                detected = true;
            }
        }
    }
    // 优先级4：检测waiting状态
    else if (type === 'waiting' && data.result.includes(ASK_HUMAN_CONSTANTS.WAITING_FOR_RESPONSE)) {
        const inquireMatch = data.result.match(/Waiting for human response to: (.+)/);
        if (inquireMatch) {
            inquire = inquireMatch[1].trim();
            detected = true;
        }
    }
    // 优先级5：检测interaction事件类型
    else if (type === 'interaction' && data.result.includes(ASK_HUMAN_CONSTANTS.HUMAN_INTERACTION_REQUIRED)) {
        inquire = data.result.replace(ASK_HUMAN_CONSTANTS.HUMAN_INTERACTION_REQUIRED, '').trim();
        detected = true;
    }

    if (detected && inquire) {
        return { inquire, detected };
    }

    return null;
}

/**
 * 处理 ask_human 交互
 * @param {string} inquire - 询问内容
 * @param {string} taskId - 任务/流程 ID
 * @param {boolean} isFlow - 是否为 flow 模式
 */
function processAskHuman(inquire, taskId, isFlow = false) {
    // 检查是否已经处理过相同的询问内容
    if (globalAskHumanProcessed && globalProcessedInquire === inquire && globalProcessedTaskId === taskId) {
        console.log(`Duplicate ${isFlow ? 'flow' : 'task'} ask_human detected, skipping:`, inquire);
        return;
    }

    console.log(`Processing ${isFlow ? 'flow' : 'task'} ask_human interaction:`, inquire);

    // 统一处理：无论是Chat还是Flow模式，都使用addMessage显示ask_human
    // 这样可以让ask_human以单独一条AI消息的形式显示，与new_chat流程保持一致
    addMessage(inquire, 'ai');

    // 如果是Flow模式，可以选择性地在Flow展示容器中添加一个轻量级的交互提示
    if (isFlow && window.flowDisplayManager) {
        // 可选：在Flow容器中添加一个简单的交互标记，但不影响主要显示
        window.flowDisplayManager.addInteractionMarker(inquire);
    }

    window.currentInteractionTaskId = taskId;
    // 设置交互类型标识
    window.currentInteractionType = isFlow ? 'flow' : 'task';
    console.log('Set interaction type:', window.currentInteractionType);

    toggle_chat_state('none');
    globalAskHumanProcessed = true;
    globalProcessedInquire = inquire; // 记录已处理的内容
    globalProcessedTaskId = taskId; // 记录已处理的任务ID
}

// 新增：聊天历史管理配置
const MAX_DIALOGS = 5; // 最大保留对话数量
const MAX_MESSAGES = MAX_DIALOGS * 2; // 最大消息数量（每次对话包含用户和AI各一条）

// 添加加载会话历史的函数
function loadSessionHistory(sessionId) {
    if (!sessionId) return;

    fetch(`/sessions/${sessionId}/history`)
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to load session history');
            }
            return response.json();
        })
        .then(history => {
            // 清空当前显示
            chatMessages.innerHTML = '';
            chatHistory = [];

            // 重建聊天历史，只保留最近10条对话
            const allMessages = [];
            history.forEach(task => {
                if (task.chat_history && task.chat_history.length > 0) {
                    task.chat_history.forEach(msg => {
                        allMessages.push(msg);
                    });
                }
            });

            // 只保留最近m轮对话
            const recentMessages = allMessages.slice(-MAX_MESSAGES);

            // 清空当前显示并重建
            chatMessages.innerHTML = '';
            chatHistory = [];

            recentMessages.forEach(msg => {
                addMessage(msg.content, msg.role);
            });

            console.log(`加载历史记录，保留最近${MAX_DIALOGS}条对话`);

            currentSessionId = sessionId;
        })
        .catch(error => {
            console.error('Failed to load session history:', error);
            showErrorToast('Failed to load session history');
        });
}

// 新增：处理ask_human的用户回答
async function handleAskHumanResponse(userResponse) {
    if (!window.currentInteractionTaskId) {
        return;
    }

    try {
        // 显示用户回答
        addMessage(userResponse, 'user');
        chatHistory.push({
            role: 'user',
            content: userResponse,
            timestamp: new Date().toISOString()
        });

        // 判断是 task 还是 flow，并选择相应的端点
        const isFlow = window.currentInteractionType === 'flow' ||
            window.currentInteractionTaskId.includes('flow') ||
            currentMode === 'flow';

        const endpoint = isFlow ?
            `/flows/${window.currentInteractionTaskId}/interact` :
            `/tasks/${window.currentInteractionTaskId}/interact`;

        console.log(`Sending interaction response to ${endpoint}:`, userResponse);

        // 发送用户回答到后端
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                response: userResponse
            })
        });

        if (!response.ok) {
            throw new Error('提交回答失败');
        }

        // 保存当前任务ID，然后清除交互状态
        const taskId = window.currentInteractionTaskId;
        window.currentInteractionTaskId = null;
        window.currentInteractionType = null;  // 修复：添加这行

        // 新增：重置ask_human状态，允许后续的ask_human交互
        globalAskHumanProcessed = false;
        globalProcessedInquire = null;
        globalProcessedTaskId = null;  // 修复：添加这行
        console.log('Ask_human state reset, ready for next interaction');

        // 修复：不要重新建立SSE连接，让现有的连接继续工作
        // 这样可以保持流程的连续性，避免重新创建流程实例
        console.log('Ask_human response sent, waiting for existing flow to continue...');

        // 注释掉重新建立SSE连接的代码，保持现有连接
        // const isLongThought = document.getElementById('longThoughtCheckbox').checked;
        // if (thoughtQuote && isLongThought) {
        //     thoughtQuote.classList.add('thinking-completed');
        //     thoughtQuote = null;
        //     aiMessageDiv = null;
        //     console.log('Marked existing thinking container as completed, ready for new thinking after ask_human');
        // }
        // if (isFlow) {
        //     setupFlowSSE(taskId, isLongThought);
        // } else {
        //     setupSSE(taskId, isLongThought);
        // }

    } catch (error) {
        console.error('提交ask_human回答失败:', error);
        showErrorToast('提交回答失败: ' + error.message);
    }
}

function getMarkedText(text) {
    return DOMPurify.sanitize(marked.parse(text))
}

function showErrorToast(message) {
    const toastEl = document.getElementById('errorToast');
    const toastBody = toastEl.querySelector('.toast-body');
    toastBody.textContent = message;

    const toast = new bootstrap.Toast(toastEl);
    toast.show();
}

function toggle_chat_state(state) {
    chat_state = state;
    const sendButton = document.getElementById('sendButton');
    const sendSpinner = document.getElementById('send-spinner');
    const sendIcon = sendButton.querySelector('.bi-send');
    const terminateIcon = sendButton.querySelector('.bi-stop-fill');

    if (state === 'working') {
        // 处理中状态：显示terminate按钮
        sendSpinner.style.display = 'inline-block';
        sendIcon.style.display = 'none';
        if (terminateIcon) {
            terminateIcon.style.display = 'inline-block';
        } else {
            // 如果没有terminate图标，创建一个
            const newTerminateIcon = document.createElement('i');
            newTerminateIcon.className = 'bi bi-stop-fill';
            newTerminateIcon.style.display = 'inline-block';
            sendButton.appendChild(newTerminateIcon);
        }
        sendButton.setAttribute('data-bs-title', 'Terminate');
        sendButton.onclick = terminateCurrentTask;
    } else {
        // 非处理中状态：显示send按钮
        sendSpinner.style.display = 'none';
        sendIcon.style.display = 'inline-block';
        if (terminateIcon) {
            terminateIcon.style.display = 'none';
        }
        sendButton.setAttribute('data-bs-title', 'Send to Manus');
        sendButton.onclick = sendMessage;
    }
}

// 新增：终止当前任务
async function terminateCurrentTask() {
    if (!currentEventSource) {
        console.log('没有正在运行的任务');
        return;
    }

    try {
        // 获取当前任务ID（从EventSource URL中提取）
        const eventSourceUrl = currentEventSource.url;
        const taskIdMatch = eventSourceUrl.match(/\/tasks\/([^\/]+)\/events/);
        if (!taskIdMatch) {
            console.error('无法从EventSource URL中提取任务ID');
            return;
        }
        const taskId = taskIdMatch[1];

        console.log(`正在终止任务: ${taskId}`);

        // 发送终止请求到后端
        const response = await fetch(`/tasks/${taskId}/terminate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) {
            throw new Error('终止任务失败');
        }

        // 关闭EventSource连接
        if (currentEventSource) {
            currentEventSource.close();
            currentEventSource = null;
        }

        // 重置状态
        toggle_chat_state('none');

        // 显示终止成功消息
        addMessage('任务已终止', 'system');
        showErrorToast('任务已成功终止');

    } catch (error) {
        console.error('终止任务失败:', error);
        showErrorToast('终止任务失败: ' + error.message);
    }
}

// 新增：初始化thinking容器
function initializeThinkingContainer() {
    const isLongThought = document.getElementById('longThoughtCheckbox').checked;
    if (isLongThought && !thoughtQuote) {
        createLongThought('正在思考...', 'initializing');
    }
}

// 新增：创建thinking容器
function createLongThought(prompt, type = 'normal') {
    console.log('Creating long thought with prompt:', prompt, 'type:', type);

    // 如果已经存在thinking容器，直接追加内容
    if (thoughtQuote) {
        // 更新现有的thinking内容
        const stepDiv = document.createElement('div');
        stepDiv.className = 'thinking-message';
        stepDiv.textContent = prompt;
        thoughtQuote.querySelector('.quote-content').appendChild(stepDiv);

        // 移除初始化状态
        thoughtQuote.classList.remove('thinking-initializing');
        return;
    }

    // 创建新的AI消息容器
    aiMessageDiv = document.createElement('div');
    aiMessageDiv.className = 'message ai-message';

    const iconDiv = document.createElement('div');
    iconDiv.className = 'message-icon';
    const icon = document.createElement('i');
    icon.className = 'bi bi-robot';
    iconDiv.appendChild(icon);

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    // 创建thinking容器
    thoughtQuote = document.createElement('div');
    thoughtQuote.className = 'thinking-container';

    // 添加初始化状态类
    if (type === 'initializing') {
        thoughtQuote.classList.add('thinking-initializing');
    }

    const headerDiv = document.createElement('div');
    headerDiv.className = 'quote-header';
    headerDiv.innerHTML = `
        <span>🤔 思考过程</span>
        <div class="toggle-controls">
            <button class="btn btn-sm btn-outline-secondary thinking-toggle" onclick="toggleThinkingContainer()">
                <i class="bi bi-chevron-up"></i>
            </button>
        </div>
    `;

    const contentArea = document.createElement('div');
    contentArea.className = 'quote-content';

    if (type === 'initializing') {
        contentArea.innerHTML = '<div class="thinking-initializing">正在思考...</div>';
    } else {
        const stepDiv = document.createElement('div');
        stepDiv.className = 'thinking-message';
        stepDiv.textContent = prompt;
        contentArea.appendChild(stepDiv);
    }

    thoughtQuote.appendChild(headerDiv);
    thoughtQuote.appendChild(contentArea);

    contentDiv.appendChild(thoughtQuote);
    aiMessageDiv.appendChild(iconDiv);
    aiMessageDiv.appendChild(contentDiv);

    chatMessages.appendChild(aiMessageDiv);
    scrollView();
}

// 新增：切换thinking容器的展开/收起
function toggleThinkingContainer() {
    if (thoughtQuote) {
        const contentArea = thoughtQuote.querySelector('.quote-content');
        const toggleBtn = thoughtQuote.querySelector('.thinking-toggle i');

        if (contentArea.style.display === 'none') {
            contentArea.style.display = 'block';
            toggleBtn.className = 'bi bi-chevron-up';
        } else {
            contentArea.style.display = 'none';
            toggleBtn.className = 'bi bi-chevron-down';
        }
    }
}

function createChat() {
    console.log('=== createChat 被调用 ===');
    const isLongThought = document.getElementById('longThoughtCheckbox').checked;
    const promptInput = document.getElementById('messageInput');
    const prompt = promptInput.value.trim();

    if (!prompt) {
        showErrorToast("Please enter a valid prompt");
        promptInput.focus();
        return;
    }

    if (currentEventSource) {
        currentEventSource.close();
        currentEventSource = null;
    }

    // 生成或使用现有会话ID
    if (!currentSessionId) {
        currentSessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    }

    // 构建请求数据，包含聊天历史
    const requestData = {
        prompt: prompt,
        session_id: currentSessionId,
        chat_history: chatHistory
    };

    console.log('发送 Chat 请求到 /tasks，数据:', requestData);
    fetch('/tasks', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(requestData)
    })
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => { throw new Error(err.detail || 'Request failed') });
            }
            return response.json();
        })
        .then(data => {
            console.log('Task created successfully:', data);
            if (!data.task_id) {
                throw new Error('Invalid task ID');
            }
            console.log('Task ID:', data.task_id);
            console.log('isLongThought:', isLongThought);
            addMessage(prompt, 'user');

            // 新增：在任务开始时初始化thinking容器（仅Chat模式）
            initializeThinkingContainer();

            // 设置处理中状态，显示terminate按钮
            toggle_chat_state('working');
            console.log('Calling setupSSE with taskId:', data.task_id, 'isLongThought:', isLongThought);
            setupSSE(data.task_id, isLongThought);
            promptInput.value = '';
        })
        .catch(error => {
            showErrorToast(error.message)
            console.error('Failed to create task:', error);
        });
}

function setupSSE(taskId, isLongThought) {
    console.log('=== setupSSE called ===');
    console.log('taskId:', taskId);
    console.log('isLongThought:', isLongThought);

    let retryCount = 0;
    const maxRetries = 3;
    const retryDelay = 2000;
    let lastResultContent = '';

    // 修复：使用全局状态管理，防止重复处理
    // 检查是否是新的任务ID，如果是则重置状态
    if (globalProcessedTaskId !== taskId) {
        globalAskHumanProcessed = false;
        globalProcessedInquire = null;
        globalProcessedTaskId = taskId;
        // 重置lastMessageType，确保新任务的thinking容器创建逻辑正确
        lastMessageType = null;
        console.log('New task detected, resetting ask_human state and lastMessageType');
    }

    function connectFunction() {
        console.log('=== connectFunction() called ===');
        console.log('Attempting to connect to SSE:', `/tasks/${taskId}/events`);
        const eventSource = new EventSource(`/tasks/${taskId}/events`);
        currentEventSource = eventSource;

        console.log('EventSource created:', eventSource);
        console.log('EventSource readyState:', eventSource.readyState);

        // 事件队列处理机制
        const eventQueue = [];
        let isProcessingEvents = false;

        async function processEventQueue() {
            if (isProcessingEvents || eventQueue.length === 0) return;

            isProcessingEvents = true;
            while (eventQueue.length > 0) {
                const eventData = eventQueue.shift();
                try {
                    await handleEvent(eventData.event, eventData.type);
                } catch (error) {
                    console.error('Error processing event:', error);
                }
            }
            isProcessingEvents = false;
        }

        // 添加连接状态监听
        eventSource.onopen = () => {
            console.log('SSE connection opened successfully');
        };

        eventSource.onerror = (err) => {
            console.error('SSE connection error:', err);
            console.log('EventSource readyState on error:', eventSource.readyState);
        };

        // 不再预先创建容器，只有 think 事件才会创建
        const handleEvent = async (event, type) => {
            try {
                const data = JSON.parse(event.data);

                // 新增：调试日志
                console.log(`Received1111 ${type} event:`, data);
                console.log('isLongThought:', isLongThought);
                console.log('globalAskHumanProcessed:', globalAskHumanProcessed);

                // 检测并处理ask_human交互 - 使用通用检测函数
                const askHumanResult = detectAskHuman(data, type, false, taskId);
                if (askHumanResult) {
                    processAskHuman(askHumanResult.inquire, taskId, false);
                    return; // 处理完成后直接返回，避免后续处理
                }

                // 如果已经处理了ask_human，跳过其他处理逻辑
                if (globalAskHumanProcessed && type == 'interaction') {
                    console.log('Ask_human already processed, skipping other logic');
                    return;
                }

                // 1. 只有 think 类型的事件会创建或更新 thinking 容器（仅在长思考模式下）
                if (type === 'think' && isLongThought) {
                    console.log('Processing think event in long thought mode');
                    console.log('Last message type:', lastMessageType);

                    // 修复：判断是否需要创建新的thinking容器
                    // 只有在没有容器 或者 是用户消息后的第一个think事件时才创建新容器
                    const isFirstThinkAfterUser = lastMessageType === 'user' && !thoughtQuote;
                    const shouldCreateNewContainer = !thoughtQuote || isFirstThinkAfterUser;

                    if (shouldCreateNewContainer) {
                        console.log('Creating new thought quote (reason: ' + (isFirstThinkAfterUser ? 'first think after user message' : 'no existing container') + ')');
                        // 创建新容器时，将之前的容器标记为完成状态，但不删除
                        if (thoughtQuote) {
                            thoughtQuote.classList.add('thinking-completed');
                            // 重置thoughtQuote引用，为新的容器做准备
                            thoughtQuote = null;
                            aiMessageDiv = null;
                        }
                        createLongThought(data.result, 'normal');
                    } else {
                        console.log('Updating existing thought quote (reason: continuing previous thinking)');
                        // 更新现有的思考内容
                        const stepDiv = document.createElement('div');
                        stepDiv.className = 'thinking-message';
                        stepDiv.textContent = data.result;
                        thoughtQuote.querySelector('.quote-content').appendChild(stepDiv);

                        // 移除初始化状态
                        thoughtQuote.classList.remove('thinking-initializing');
                    }
                } else if (type === 'think' && !isLongThought) {
                    console.log('Think event received but long thought mode is OFF');
                }
                // 2. 其他类型的事件（log, tool, act 等）不创建任何容器，只记录日志（仅在长思考模式下）
                else if ((type === 'log' || type === 'tool' || type === 'act') && isLongThought) {
                    // 只记录日志，不创建任何容器
                    console.log(`Received ${type} event:`, data.result);
                }
                // 3. 非长思考模式下的处理逻辑
                else if (!isLongThought) {
                    if (type === 'act') {
                        addMessage(data.result, 'ai');
                    }
                }

                // 4. 处理complete事件
                if (type === 'complete') {
                    lastResultContent = data.result || '';

                    // 新增：在任务完成时，如果有交互提示，发送用户回答
                    if (window.currentInteractionTaskId === taskId) {
                        // 自动发送一个默认回答或提示用户
                        console.log('任务完成，但仍有待处理的交互提示');
                    }

                    // 只有 complete 类型的事件会创建 AI 消息容器输出内容
                    if (lastResultContent) {
                        // 直接创建 AI 消息，不再依赖 isLongThought 模式
                        addMessage(lastResultContent, 'ai');
                        // 将最终答案添加到聊天历史
                        chatHistory.push({
                            role: 'ai',
                            content: lastResultContent,
                            timestamp: new Date().toISOString()
                        });
                    }

                    // 任务完成时，将当前的thinking容器标记为完成状态
                    if (thoughtQuote) {
                        thoughtQuote.classList.add('thinking-completed');
                        thoughtQuote = null;
                        aiMessageDiv = null;
                    }

                    scrollView();
                    eventSource.close();
                    currentEventSource = null;
                    toggle_chat_state('none');
                }

                // 5. 处理terminated事件
                if (type === 'terminated') {
                    console.log('任务被终止:', data.message);
                    addMessage('任务已终止', 'system');

                    // 任务终止时，将当前的thinking容器标记为完成状态
                    if (thoughtQuote) {
                        thoughtQuote.classList.add('thinking-completed');
                        thoughtQuote = null;
                        aiMessageDiv = null;
                    }

                    eventSource.close();
                    currentEventSource = null;
                    toggle_chat_state('none');
                }

                // 6. 处理error事件
                if (type === 'error') {
                    console.error(event);
                    showErrorToast(data.message);

                    // 任务出错时，将当前的thinking容器标记为完成状态
                    if (thoughtQuote) {
                        thoughtQuote.classList.add('thinking-completed');
                        thoughtQuote = null;
                        aiMessageDiv = null;
                    }

                    eventSource.close();
                    currentEventSource = null;
                    toggle_chat_state('none');
                }
            } catch (e) {
                console.error(`Error handling ${type} event:`, e);
            }
        };

        const eventTypes = ['think', 'tool', 'act', 'log', 'run', 'message', 'interaction'];
        console.log('Setting up event listeners for types:', eventTypes);
        eventTypes.forEach(type => {
            console.log(`Adding event listener for: ${type}`);
            eventSource.addEventListener(type, (event) => {
                console.log(`Event listener triggered for: ${type}`);
                // 将事件加入队列，使用后端提供的时间戳
                const data = JSON.parse(event.data);
                const timestamp = data.timestamp || Date.now(); // 如果没有时间戳，使用当前时间作为后备
                eventQueue.push({ event, type, timestamp });
                // 按时间戳排序
                eventQueue.sort((a, b) => a.timestamp - b.timestamp);
                // 处理队列
                processEventQueue();
            });
        });

        eventSource.addEventListener('complete', (event) => {
            // 将complete事件也加入队列处理
            const data = JSON.parse(event.data);
            const timestamp = data.timestamp || Date.now(); // 如果没有时间戳，使用当前时间作为后备
            eventQueue.push({ event, type: 'complete', timestamp });
            eventQueue.sort((a, b) => a.timestamp - b.timestamp);
            processEventQueue();
        });

        // 新增：处理任务终止事件
        eventSource.addEventListener('terminated', (event) => {
            // 将terminated事件也加入队列处理
            const data = JSON.parse(event.data);
            const timestamp = data.timestamp || Date.now(); // 如果没有时间戳，使用当前时间作为后备
            eventQueue.push({ event, type: 'terminated', timestamp });
            eventQueue.sort((a, b) => a.timestamp - b.timestamp);
            processEventQueue();
        });

        eventSource.addEventListener('error', (event) => {
            // 将error事件也加入队列处理
            const data = JSON.parse(event.data);
            const timestamp = data.timestamp || Date.now(); // 如果没有时间戳，使用当前时间作为后备
            eventQueue.push({ event, type: 'error', timestamp });
            eventQueue.sort((a, b) => a.timestamp - b.timestamp);
            processEventQueue();
        });

        eventSource.onerror = (err) => {
            if (eventSource.readyState === EventSource.CLOSED) return;

            console.error('SSE connection error:', err);
            eventSource.close();

            fetch(`/tasks/${taskId}`)
                .then(response => response.json())
                .then(task => {
                    if (task.status === 'completed' || task.status === 'failed' || task.status === 'terminated') {
                        if (task.status === 'completed') {
                            // TODO
                        } else if (task.status === 'terminated') {
                            console.log('任务已终止');
                            addMessage('任务已终止', 'system');
                        } else {
                            console.error(task)
                            showErrorToast(task.error)
                        }
                        toggle_chat_state('none');
                    } else if (retryCount < maxRetries) {
                        retryCount++;
                        showErrorToast(`Connection lost, retrying in ${retryDelay / 1000} seconds (${retryCount}/${maxRetries})`)
                        setTimeout(connectFunction, retryDelay);
                    } else {
                        showErrorToast('Connection lost, please try refreshing the page')
                        toggle_chat_state('none');
                    }
                })
                .catch(error => {
                    console.error('Task status check failed:', error);
                    if (retryCount < maxRetries) {
                        retryCount++;
                        setTimeout(connectFunction, retryDelay);
                    } else {
                        toggle_chat_state('none');
                    }
                });
        }
    }
    // 调用 connect 函数建立连接
    connectFunction();
}

// 新增：Flow 版本的 SSE 订阅
function setupFlowSSE(flowId, isLongThought) {
    let retryCount = 0;
    const maxRetries = 3;
    const retryDelay = 2000;

    // 修复：使用全局状态管理，防止重复处理
    // 检查是否是新的流程ID，如果是则重置状态
    if (globalProcessedTaskId !== flowId) {
        globalAskHumanProcessed = false;
        globalProcessedInquire = null;
        globalProcessedTaskId = flowId;
        // 重置lastMessageType，确保新Flow任务的thinking容器创建逻辑正确
        lastMessageType = null;
        console.log('New flow detected, resetting ask_human state and lastMessageType');
    } else {
        // 修复：如果是同一个流程ID，说明是ask_human后的继续，不要重置状态
        console.log('Continuing existing flow, maintaining current state');
    }

    // 初始化 Flow 展示管理器
    console.log('检查FlowDisplayManager:', {
        'window.FlowDisplayManager': typeof window.FlowDisplayManager,
        'window.flowDisplayManager': window.flowDisplayManager
    });

    if (!window.FlowDisplayManager) {
        console.error('FlowDisplayManager类未定义！');
        // 创建一个简单的备用管理器
        window.FlowDisplayManager = class SimpleFlowDisplayManager {
            constructor() {
                this.flowData = { plan: null, steps: [], summary: null, currentStepIndex: -1 };
                this.containers = [];
                this.currentContainer = null;
                this.executionPhase = 0;
                console.log('创建了备用FlowDisplayManager');
            }

            resetDataOnly() {
                this.flowData = { plan: null, steps: [], summary: null, currentStepIndex: -1 };
                console.log('备用管理器数据重置');
            }

            initContainer() {
                const container = document.createElement('div');
                container.className = 'flow-display-container';
                container.innerHTML = `
                    <div class="flow-plan-section" style="display: none;">
                        <div class="flow-plan-header"><h4>📋 执行计划</h4></div>
                        <div class="flow-plan-content"></div>
                    </div>
                    <div class="flow-steps-section" style="display: none;">
                        <div class="flow-steps-header"><h4>🔄 执行步骤</h4></div>
                        <div class="flow-steps-content"></div>
                    </div>
                    <div class="flow-summary-section" style="display: none;">
                        <div class="flow-summary-header"><h4>📝 执行总结</h4></div>
                        <div class="flow-summary-content"></div>
                    </div>
                `;

                this.executionPhase++;
                const phaseHeader = document.createElement('div');
                phaseHeader.className = 'execution-phase-header';
                phaseHeader.innerHTML = `<h5 class="execution-phase-title">执行阶段 ${this.executionPhase}</h5>`;
                container.insertBefore(phaseHeader, container.firstChild);

                this.containers.push(container);
                this.currentContainer = container;
                console.log('备用管理器容器创建成功');
                return container;
            }

            createNewExecutionPhase() {
                return this.initContainer();
            }

            handlePlanEvent(text) {
                console.log('备用管理器处理Plan事件:', text);
                const planSection = this.currentContainer.querySelector('.flow-plan-section');
                const planContent = planSection.querySelector('.flow-plan-content');
                planContent.innerHTML = `<pre>${text}</pre>`;
                planSection.style.display = 'block';
            }

            handleStepEventByContent(text) {
                console.log('备用管理器处理Step事件:', text);
                const stepsSection = this.currentContainer.querySelector('.flow-steps-section');
                const stepsContent = stepsSection.querySelector('.flow-steps-content');

                if (text.includes('开始执行步骤')) {
                    const stepDiv = document.createElement('div');
                    stepDiv.className = 'flow-step running';
                    stepDiv.innerHTML = `
                        <div class="step-header">
                            <div class="step-number">${this.flowData.steps.length + 1}</div>
                            <div class="step-title">${text.replace('开始执行步骤', '').trim()}</div>
                            <div class="step-status">🔄</div>
                        </div>
                        <div class="step-content">
                            <div class="step-description">正在执行...</div>
                        </div>
                    `;
                    stepsContent.appendChild(stepDiv);
                    stepsSection.style.display = 'block';

                    this.flowData.steps.push({ number: this.flowData.steps.length + 1, title: text, status: 'running' });
                }
            }

            handleDetailEvent(type, text) {
                console.log('备用管理器处理Detail事件:', type, text);
            }

            handleSummaryEvent(text) {
                console.log('备用管理器处理Summary事件:', text);
                const summarySection = this.currentContainer.querySelector('.flow-summary-section');
                const summaryContent = summarySection.querySelector('.flow-summary-content');
                summaryContent.innerHTML = `<pre>${text}</pre>`;
                summarySection.style.display = 'block';
            }

            addInteractionMarker(text) {
                console.log('备用管理器添加交互标记:', text);
            }
        };
    }

    if (!window.flowDisplayManager) {
        console.log('创建新的FlowDisplayManager实例');
        window.flowDisplayManager = new window.FlowDisplayManager();
    }

    // 智能容器管理：新流程创建新管理器，继续流程创建新执行阶段
    if (globalProcessedTaskId !== flowId) {
        // 全新流程：重置数据并创建第一个容器
        window.flowDisplayManager.resetDataOnly();
        const displayContainer = window.flowDisplayManager.initContainer();
        chatMessages.appendChild(displayContainer);
        console.log('新流程：创建第一个容器');
    } else {
        // 继续现有流程：创建新的执行阶段容器
        console.log('继续现有流程：创建新的执行阶段容器');
        const newContainer = window.flowDisplayManager.createNewExecutionPhase();
        // 修复：不要重置数据，保持现有数据状态
        // window.flowDisplayManager.resetDataOnly(); // 移除这行
        // 插入到聊天消息区域
        chatMessages.appendChild(newContainer);
        console.log('新执行阶段容器已添加，保持现有数据状态');
    }

    function connect() {
        const eventSource = new EventSource(`/flows/${flowId}/events`);
        currentEventSource = eventSource;
        // 不再预先创建容器，只有 think 事件才会创建
        const handleEvent = (event, type) => {
            try {
                const data = JSON.parse(event.data);
                const text = data.result || data.message || '';

                // 新增：调试日志
                console.log(`Received flow ${type} event:`, data);
                console.log('isLongThought:', isLongThought);
                console.log('globalAskHumanProcessed:', globalAskHumanProcessed);

                // 检测并处理ask_human交互 - 使用通用检测函数
                const askHumanResult = detectAskHuman(data, type, true, flowId);
                if (askHumanResult) {
                    processAskHuman(askHumanResult.inquire, flowId, true);
                    return; // 提前返回，避免继续处理其他逻辑
                }

                // 使用 Flow 展示管理器处理事件
                if (window.flowDisplayManager) {
                    switch (type) {
                        case 'plan':
                            window.flowDisplayManager.handlePlanEvent(text);
                            break;
                        case 'step':
                            // 使用新的方法根据消息内容判断开始或结束
                            window.flowDisplayManager.handleStepEventByContent(text);
                            break;
                        case 'think':
                        case 'act':
                            window.flowDisplayManager.handleDetailEvent(type, text);
                            break;
                        case 'summary':
                            window.flowDisplayManager.handleSummaryEvent(text);
                            break;
                        case 'interaction':
                            // 新增：处理交互事件（ask_human）
                            // 注意：主要的ask_human显示已经通过processAskHuman处理
                            // 这里主要用于状态管理和Flow容器的交互标记
                            if (window.flowDisplayManager) {
                                window.flowDisplayManager.addInteractionMarker(text);
                            }
                            break;
                        default:
                            console.log(`Unhandled flow event type: ${type}`, text);
                    }
                }

                // 保留原有的长思考模式逻辑作为备用
                if (isLongThought && type === 'think') {
                    console.log('Processing flow think event in long thought mode');
                    // Flow模式不需要创建thinking容器，有自己的展示逻辑
                    // 这里可以保留原有的 thinking 容器逻辑，但实际不会执行
                }
            } catch (e) {
                console.error(`Error handling flow ${type} event:`, e);
            }
        };

        const eventTypes = ['think', 'tool', 'act', 'log', 'run', 'step', 'message', 'plan', 'summary', 'interaction'];
        eventTypes.forEach(type => {
            eventSource.addEventListener(type, (event) => handleEvent(event, type));
        });

        eventSource.addEventListener('complete', (event) => {
            try {
                const data = JSON.parse(event.data);
                const content = data.result || '';
                if (content) {
                    // 2. 只有 complete 类型的事件会创建 AI 消息容器输出内容
                    addMessage(content, 'ai');
                    chatHistory.push({ role: 'ai', content: content, timestamp: new Date().toISOString() });
                }

                // 任务完成时，将当前的thinking容器标记为完成状态
                if (thoughtQuote) {
                    thoughtQuote.classList.add('thinking-completed');
                    thoughtQuote = null;
                    aiMessageDiv = null;
                }
            } catch (e) {
                console.error('Error handling flow complete:', e);
            }
            eventSource.close();
            currentEventSource = null;
            toggle_chat_state('none');
        });

        eventSource.addEventListener('error', (event) => {
            try {
                const data = JSON.parse(event.data);
                showErrorToast(data.message || 'Flow failed');
            } catch (e) {
                console.error('Error handling flow error:', e);
            }

            // 任务出错时，将当前的thinking容器标记为完成状态
            if (thoughtQuote) {
                thoughtQuote.classList.add('thinking-completed');
                thoughtQuote = null;
                aiMessageDiv = null;
            }

            eventSource.close();
            currentEventSource = null;
            toggle_chat_state('none');
        });

        eventSource.onerror = (err) => {
            if (eventSource.readyState === EventSource.CLOSED) return;
            console.error('Flow SSE connection error:', err);
            eventSource.close();
            fetch(`/flows/${flowId}`)
                .then(response => response.json())
                .then(task => {
                    if (task.status === 'completed' || task.status === 'failed') {
                        if (task.status === 'completed') {
                            // ignore
                        } else {
                            showErrorToast(task.error || 'Flow failed');
                        }
                    } else if (retryCount < maxRetries) {
                        retryCount++;
                        showErrorToast(`Connection lost, retrying in ${retryDelay / 1000} seconds (${retryCount}/${maxRetries})`)
                        setTimeout(connect, retryDelay);
                    } else {
                        showErrorToast('Connection lost, please try refreshing the page')
                    }
                })
                .catch(error => {
                    console.error('Flow status check failed:', error);
                    if (retryCount < maxRetries) {
                        retryCount++;
                        setTimeout(connect, retryDelay);
                    }
                });
        };
    }

    connect();
}

// 新增：创建 Flow
function createFlow() {
    console.log('=== createFlow 被调用 ===');
    const isLongThought = document.getElementById('longThoughtCheckbox').checked;
    const promptInput = document.getElementById('messageInput');
    const prompt = promptInput.value.trim();
    if (!prompt) {
        showErrorToast('Please enter a valid prompt');
        promptInput.focus();
        return;
    }
    if (currentEventSource) {
        currentEventSource.close();
        currentEventSource = null;
    }
    if (!currentSessionId) {
        currentSessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    }
    const requestData = { prompt: prompt, session_id: currentSessionId, chat_history: chatHistory };
    console.log('发送 Flow 请求到 /flows，数据:', requestData);
    fetch('/flows', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestData)
    }).then(response => {
        if (!response.ok) {
            return response.json().then(err => { throw new Error(err.detail || 'Request failed') });
        }
        return response.json();
    }).then(data => {
        console.log('Flow 创建成功，响应数据:', data);
        if (!data.task_id) throw new Error('Invalid flow ID');
        addMessage(prompt, 'user');

        // Flow模式不需要初始化thinking容器，有自己的Flow展示逻辑
        // initializeThinkingContainer();  // 移除这行

        // 设置处理中状态，显示terminate按钮
        toggle_chat_state('working');
        setupFlowSSE(data.task_id, isLongThought);
        promptInput.value = '';
    }).catch(error => {
        showErrorToast(error.message);
        console.error('Failed to create flow:', error);
    });
}

// 新增：设置模式的函数（互斥选择）
function setMode(mode) {
    console.log('setMode 被调用，模式:', mode);
    currentMode = mode;
    const btnChat = document.getElementById('btn-chat');
    const btnFlow = document.getElementById('btn-flow');

    console.log('按钮元素:', { btnChat, btnFlow });

    if (btnChat && btnFlow) {
        if (mode === 'chat') {
            btnChat.classList.add('active');
            btnFlow.classList.remove('active');
            console.log('Chat 模式设置完成');
        } else {
            btnFlow.classList.add('active');
            btnChat.classList.remove('active');
            console.log('Flow 模式设置完成');
        }
    } else {
        console.error('按钮元素未找到，无法设置模式');
    }

    // 修复：确保sendButton的onclick始终指向正确的函数
    const sendButton = document.getElementById('sendButton');
    if (sendButton) {
        // 检查当前状态，如果不是working状态，则重新绑定sendMessage
        if (chat_state !== 'working') {
            sendButton.onclick = sendMessage;
        }
    }
}

function loadHistory() {
    fetch('/tasks')
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`request failure: ${response.status} - ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(tasks => {
            applyHistory(tasks)
        })
        .catch(error => {
            console.error('Failed to load history records:', error);
            showErrorToast(error.message)
        });
}

function applyHistory(tasks) {
    if (!tasks) return;
    const historyModal = new bootstrap.Modal(document.getElementById('historyModal'));
    const historyList = document.getElementById('historyList');

    historyList.innerHTML = '';

    if (tasks.length === 0) {
        historyList.innerHTML = '<li class="list-group-item text-muted">Record not found!</li>';
    } else {
        tasks.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        tasks.forEach(item => {
            const title = item.prompt
            const li = document.createElement('li');
            li.className = 'list-group-item d-flex justify-content-between align-items-center';
            li.innerHTML = `
                <div class="fw-bold">${title}</div>
                <small class="text-muted">${new Date(item.created_at).toLocaleString()}</small>
            `;
            li.addEventListener('click', () => {
                historyModal.hide();
                loadSessionHistory(item.session_id);
            });
            historyList.appendChild(li);
        });
    }

    historyModal.show();
}

function addMessage(content, role) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}-message`;

    const iconDiv = document.createElement('div');
    iconDiv.className = 'message-icon';
    const icon = document.createElement('i');
    icon.className = role === 'user' ? 'bi bi-person' : 'bi bi-robot';
    iconDiv.appendChild(icon);

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    if (role === 'user') {
        contentDiv.textContent = content;
    } else {
        contentDiv.innerHTML = getMarkedText(content);
    }

    messageDiv.appendChild(iconDiv);
    messageDiv.appendChild(contentDiv);

    chatMessages.appendChild(messageDiv);
    scrollView();

    // 更新前一条消息类型
    lastMessageType = role;

    // 添加到聊天历史，只保留最近10条对话
    if (role !== 'system') {
        chatHistory.push({
            role: role,
            content: content,
            timestamp: new Date().toISOString()
        });

        // 只保留最近10条对话数据
        if (chatHistory.length > MAX_MESSAGES) {
            chatHistory.splice(0, chatHistory.length - MAX_MESSAGES);
            console.log(`聊天历史已截断，保留最近${MAX_DIALOGS}条对话`);
        }
    }
}

function scrollView() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function sendMessage() {
    console.log('sendMessage 被调用，当前模式:', currentMode);
    if (currentMode === 'chat') {
        console.log('进入 Chat 模式，调用 createChat()');
        createChat();
    } else {
        console.log('进入 Flow 模式，调用 createFlow()');
        createFlow();
    }
}

// 页面加载完成后的初始化
document.addEventListener('DOMContentLoaded', function () {
    chatMessages = document.getElementById('chatMessages');

    // 设置默认模式
    setMode('chat');

    // 绑定回车键发送消息
    const messageInput = document.getElementById('messageInput');
    if (messageInput) {
        messageInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
    }

    // 绑定模式切换按钮
    const btnChat = document.getElementById('btn-chat');
    const btnFlow = document.getElementById('btn-flow');

    if (btnChat) {
        btnChat.addEventListener('click', () => setMode('chat'));
    }
    if (btnFlow) {
        btnFlow.addEventListener('click', () => setMode('flow'));
    }

    // 绑定历史记录按钮
    const historyBtn = document.getElementById('historyBtn');
    if (historyBtn) {
        historyBtn.addEventListener('click', loadHistory);
    }
});

// 添加 Flow 展示样式
const flowStyles = `
<style>
.flow-display-container {
    margin: 20px 0;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    background: #fafafa;
    overflow: hidden;
}

.flow-plan-section,
.flow-steps-section,
.flow-summary-section {
    padding: 15px;
    border-bottom: 1px solid #e0e0e0;
}

.flow-plan-section:last-child,
.flow-steps-section:last-child,
.flow-summary-section:last-child {
    border-bottom: none;
}

.flow-plan-header,
.flow-steps-header,
.flow-summary-header {
    margin-bottom: 10px;
}

.flow-plan-header h4,
.flow-steps-header h4,
.flow-summary-header h4 {
    margin: 0;
    color: #333;
    font-size: 16px;
    font-weight: 600;
}

.plan-content pre,
.summary-content pre {
    background: #f8f9fa;
    border: 1px solid #e9ecef;
    border-radius: 4px;
    padding: 10px;
    margin: 0;
    white-space: pre-wrap;
    font-size: 14px;
    line-height: 1.4;
}

.flow-step {
    margin-bottom: 15px;
    border: 1px solid #ddd;
    border-radius: 6px;
    background: white;
    overflow: hidden;
}

.flow-step.running {
    border-left: 4px solid #007bff;
}

.flow-step.completed {
    border-left: 4px solid #28a745;
}

.flow-step.failed {
    border-left: 4px solid #dc3545;
}

.step-header {
    display: flex;
    align-items: center;
    padding: 10px 15px;
    background: #f8f9fa;
    border-bottom: 1px solid #e9ecef;
}

.step-number {
    background: #007bff;
    color: white;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: bold;
    margin-right: 10px;
}

.step-title {
    flex: 1;
    font-weight: 500;
    color: #333;
}

.step-status {
    font-size: 18px;
    margin-left: 10px;
}

.step-content {
    padding: 15px;
}

.step-description {
    margin-bottom: 10px;
    color: #666;
    font-size: 14px;
}

.step-details {
    border-top: 1px solid #e9ecef;
    padding-top: 10px;
}

.step-detail {
    display: flex;
    align-items: flex-start;
    margin-bottom: 8px;
    padding: 8px;
    background: #f8f9fa;
    border-radius: 4px;
}

.step-detail.think {
    background: #fff3cd;
    border-left: 3px solid #ffc107;
}

.step-detail.act {
    background: #d1ecf1;
    border-left: 3px solid #17a2b8;
}

.detail-type {
    margin-right: 8px;
    font-size: 16px;
}

.detail-content {
    flex: 1;
    font-size: 13px;
    line-height: 1.4;
    color: #333;
}
</style>
`;

// 将样式添加到页面
document.head.insertAdjacentHTML('beforeend', flowStyles);

// 用户发起新对话的主入口函数
async function handleUserInput(userInput) {
    if (!userInput.trim()) return;

    console.log('Handling new user input:', userInput);

    // 判断是task还是flow模式
    const isFlow = currentMode === 'flow';

    try {
        // 构建请求体
        const requestBody = {
            prompt: userInput,
            session_id: getCurrentSessionId(),
            chat_history: chatHistory
        };

        // 调用相应的端点
        let response;
        if (isFlow) {
            response = await fetch('/flows', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody)
            });
        } else {
            response = await fetch('/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody)
            });
        }

        if (!response.ok) {
            throw new Error('创建对话失败');
        }

        // 获取task_id并建立SSE连接
        const result = await response.json();
        const taskId = result.task_id;

        // 获取长思考模式设置
        const isLongThought = document.getElementById('longThoughtCheckbox')?.checked || false;

        if (isFlow) {
            setupFlowSSE(taskId, isLongThought);
        } else {
            setupSSE(taskId, isLongThought);
        }

        console.log(`${isFlow ? 'Flow' : 'Task'} created with ID: ${taskId}`);

    } catch (error) {
        console.error('Error creating new chat:', error);
        showErrorToast('创建对话失败: ' + error.message);
    }
}

// 发送消息的主函数
function sendMessage() {
    const messageInput = document.getElementById('messageInput');
    const userInput = messageInput.value.trim();

    if (!userInput) return;

    // 检查是否在ask_human交互模式
    if (window.currentInteractionTaskId) {
        // 在ask_human交互模式中，调用handleAskHumanResponse
        handleAskHumanResponse(userInput);
    } else {
        // 正常模式，发起新对话
        handleUserInput(userInput);
    }

    // 清空输入框
    messageInput.value = '';
}

// 获取当前会话ID
function getCurrentSessionId() {
    if (!window.currentSessionId) {
        window.currentSessionId = 'session_' + Date.now();
    }
    return window.currentSessionId;
}

// 新增：更新输入框占位符的函数
function updateInputPlaceholder() {
    if (window.currentInteractionTaskId) {
        const messageInput = document.getElementById('messageInput');
        if (messageInput) {
            messageInput.placeholder = '请输入您的回答...';
        }
    } else {
        const messageInput = document.getElementById('messageInput');
        if (messageInput) {
            messageInput.placeholder = 'Ask Manus...';
        }
    }
}

// 监听currentInteractionTaskId的变化
Object.defineProperty(window, 'currentInteractionTaskId', {
    get: function () {
        return _currentInteractionTaskId;
    },
    set: function (value) {
        _currentInteractionTaskId = value;
        updateInputPlaceholder();
    }
});

// 新增：调试函数，显示ask_human的当前状态
function debugAskHumanState() {
    console.log('=== Ask Human State Debug ===');
    console.log('Global Ask Human Processed:', globalAskHumanProcessed);
    console.log('Global Processed Inquire:', globalProcessedInquire);
    console.log('Global Processed Task ID:', globalProcessedTaskId);
    console.log('Current Interaction Task ID:', window.currentInteractionTaskId);
    console.log('Chat State:', chat_state);
    console.log('============================');
}

// 将调试函数暴露到全局作用域，方便在控制台调用
window.debugAskHumanState = debugAskHumanState;
