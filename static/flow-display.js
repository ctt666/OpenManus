// Flow 层级展示管理器
class FlowDisplayManager {
    constructor() {
        this.flowData = {
            plan: null,
            steps: [],
            summary: null,
            currentStepIndex: -1
        };
        this.containers = [];           // 存储所有容器
        this.currentContainer = null;   // 当前活跃容器
        this.executionPhase = 0;       // 执行阶段计数
    }

    // 初始化展示容器
    initContainer() {
        // 创建主容器
        this.container = document.createElement('div');
        this.container.className = 'flow-display-container';
        this.container.innerHTML = `
            <div class="flow-plan-section" style="display: none;">
                <div class="flow-plan-header">
                    <h4>📋 执行计划</h4>
                </div>
                <div class="flow-plan-content"></div>
            </div>
            <div class="flow-steps-section" style="display: none;">
                <div class="flow-steps-header">
                    <h4>🔄 执行步骤</h4>
                </div>
                <div class="flow-steps-content"></div>
            </div>
            <div class="flow-summary-section" style="display: none;">
                <div class="flow-summary-header">
                    <h4>📝 执行总结</h4>
                </div>
                <div class="flow-summary-content"></div>
            </div>
        `;

        // 设置执行阶段标题
        this.executionPhase++;
        const phaseHeader = document.createElement('div');
        phaseHeader.className = 'execution-phase-header';
        phaseHeader.innerHTML = `<h5 class="execution-phase-title">执行阶段 ${this.executionPhase}</h5>`;
        this.container.insertBefore(phaseHeader, this.container.firstChild);

        // 添加到容器列表
        this.containers.push(this.container);
        this.currentContainer = this.container;

        return this.container;
    }

    // 新增：创建新的执行阶段容器
    createNewExecutionPhase() {
        // 将当前容器标记为已完成
        if (this.currentContainer) {
            this.currentContainer.classList.add('execution-completed');
        }

        // 创建新的执行阶段容器
        const newContainer = document.createElement('div');
        newContainer.className = 'flow-display-container execution-phase-new';

        // 设置执行阶段标题
        this.executionPhase++;
        const phaseHeader = document.createElement('div');
        phaseHeader.className = 'execution-phase-header';
        phaseHeader.innerHTML = `<h5 class="execution-phase-title">执行阶段 ${this.executionPhase} (继续)</h5>`;
        newContainer.appendChild(phaseHeader);

        newContainer.innerHTML += `
            <div class="flow-plan-section" style="display: none;">
                <div class="flow-plan-header">
                    <h4>📋 执行计划</h4>
                </div>
                <div class="flow-plan-content"></div>
            </div>
            <div class="flow-steps-section" style="display: none;">
                <div class="flow-steps-header">
                    <h4>🔄 执行步骤</h4>
                </div>
                <div class="flow-steps-content"></div>
            </div>
            <div class="flow-summary-section" style="display: none;">
                <div class="flow-summary-header">
                    <h4>📝 执行总结</h4>
                </div>
                <div class="flow-summary-content"></div>
            </div>
        `;

        // 添加到容器列表并设置为当前容器
        this.containers.push(newContainer);
        this.currentContainer = newContainer;
        this.container = newContainer;  // 修复：确保this.container被正确设置

        return newContainer;
    }

    // 新增：只重置数据，不删除容器
    resetDataOnly() {
        this.flowData = {
            plan: null,
            steps: [],
            summary: null,
            currentStepIndex: -1
        };
        // 注意：不重置容器，保持历史显示
    }

    // 处理 plan 事件
    handlePlanEvent(content) {
        console.log('🔍 handlePlanEvent called with content:', content);
        console.log('🔍 Current container:', this.container);
        console.log('🔍 Current flowData:', this.flowData);

        this.flowData.plan = content;
        this.updatePlanDisplay();
    }

    // 处理 step 事件
    handleStepEvent(content, isStart = true) {
        if (isStart) {
            // 开始新步骤
            this.flowData.currentStepIndex++;
            this.flowData.steps[this.flowData.currentStepIndex] = {
                id: `step_${this.flowData.currentStepIndex + 1}`,
                title: content,
                status: 'running',
                content: content,
                details: []
            };
        } else {
            // 完成当前步骤
            if (this.flowData.currentStepIndex >= 0) {
                this.flowData.steps[this.flowData.currentStepIndex].status = 'completed';
            }
        }
        this.updateStepsDisplay();
    }

    // 处理 step 事件（根据消息内容判断开始或结束）
    handleStepEventByContent(content) {
        console.log('🔍 Processing step event content:', content);

        // 根据用户确认的需求，精确检测开始和结束
        const isStepStart = content.includes('Start executing step:');
        const isStepFinish = content.includes('Finish executing step:');

        console.log('🔍 Step detection:', { isStepStart, isStepFinish });

        if (isStepStart) {
            // 提取步骤信息：从 "Start executing step: [step_info]" 中提取 step_info
            const startMatch = content.match(/Start executing step:\s*(.+)/);
            if (startMatch) {
                const stepInfo = startMatch[1].trim();
                console.log('🚀 Starting new step:', stepInfo);
                this.handleStepEvent(stepInfo, true);
            } else {
                console.log('⚠️ Could not extract step info from:', content);
            }
        } else if (isStepFinish) {
            // 完成当前步骤
            console.log('✅ Completing current step');
            this.handleStepEvent('', false); // 不需要提取信息，只是标记完成
        } else {
            console.log('⚠️ Step event not recognized:', content);
        }
    }

    // 处理 act/think 事件
    handleDetailEvent(type, content) {
        console.log(`🔧 Adding ${type} detail to step ${this.flowData.currentStepIndex + 1}:`, content);

        // 确保有当前步骤
        if (this.flowData.currentStepIndex >= 0) {
            // 检查当前步骤是否已完成
            const currentStep = this.flowData.steps[this.flowData.currentStepIndex];

            if (currentStep && currentStep.status === 'completed') {
                // 如果当前步骤已完成，创建新步骤来容纳新的详情
                console.log('📝 Current step completed, creating new step for details');
                this.flowData.currentStepIndex++;
                this.flowData.steps[this.flowData.currentStepIndex] = {
                    id: `step_${this.flowData.currentStepIndex + 1}`,
                    title: `Step ${this.flowData.currentStepIndex + 1}`,
                    status: 'running',
                    content: `Step ${this.flowData.currentStepIndex + 1}`,
                    details: []
                };
            }

            // 添加详情到当前步骤
            this.flowData.steps[this.flowData.currentStepIndex].details.push({
                type: type,
                content: content,
                timestamp: new Date().toISOString()
            });

            // 确保步骤区域可见
            this.showStepsSection();
            this.updateStepsDisplay();
        } else {
            console.log('⚠️ No current step available for detail event');
        }
    }

    // 新增：显示步骤区域
    showStepsSection() {
        if (!this.container) return;

        const stepsSection = this.container.querySelector('.flow-steps-section');
        if (stepsSection) {
            stepsSection.style.display = 'block';
        }
    }

    // 处理 summary 事件
    handleSummaryEvent(content) {
        this.flowData.summary = content;
        this.updateSummaryDisplay();
    }

    // 新增：处理交互事件（ask_human）
    handleInteractionEvent(content) {
        console.log('🤝 Processing interaction event:', content);

        // 提取询问内容
        let inquire = content;
        if (content.includes('INTERACTION_REQUIRED:')) {
            inquire = content.split('INTERACTION_REQUIRED:')[1].trim();
        }

        // 创建交互显示区域（如果不存在）
        this.updateInteractionDisplay(inquire);
    }

    // 新增：添加轻量级交互标记（用于ask_human）
    addInteractionMarker(inquire) {
        if (!this.container) return;

        // 在Flow容器中添加一个简单的交互状态指示器
        let interactionIndicator = this.container.querySelector('.interaction-indicator');
        if (!interactionIndicator) {
            interactionIndicator = document.createElement('div');
            interactionIndicator.className = 'interaction-indicator';
            interactionIndicator.innerHTML = `
                <div class="interaction-status">
                    <span class="status-icon">💬</span>
                    <span class="status-text">等待用户回答</span>
                </div>
            `;

            // 插入到Flow容器的顶部
            this.container.insertBefore(interactionIndicator, this.container.firstChild);
        }

        // 更新状态文本
        const statusText = interactionIndicator.querySelector('.status-text');
        if (statusText) {
            statusText.textContent = '等待用户回答';
        }
    }

    // 更新计划显示
    updatePlanDisplay() {
        console.log('🔍 updatePlanDisplay called');
        console.log('🔍 Container:', this.container);
        console.log('🔍 Plan data:', this.flowData.plan);

        if (!this.container) {
            console.error('❌ Container is null, cannot update plan display');
            return;
        }

        const planSection = this.container.querySelector('.flow-plan-section');
        const planContent = this.container.querySelector('.flow-plan-content');

        console.log('🔍 Plan section found:', planSection);
        console.log('🔍 Plan content found:', planContent);

        if (this.flowData.plan) {
            if (planSection && planContent) {
                planSection.style.display = 'block';
                planContent.innerHTML = `
                    <div class="plan-content">
                        <pre>${this.flowData.plan}</pre>
                    </div>
                `;
                console.log('✅ Plan display updated successfully');
            } else {
                console.error('❌ Plan section or content elements not found');
            }
        } else {
            console.log('⚠️ No plan data to display');
        }
    }

    // 更新步骤显示
    updateStepsDisplay() {
        if (!this.container) return;

        const stepsContent = this.container.querySelector('.flow-steps-content');
        stepsContent.innerHTML = '';

        this.flowData.steps.forEach((step, index) => {
            const stepElement = document.createElement('div');
            stepElement.className = `flow-step ${step.status}`;

            // 根据步骤状态显示不同的内容
            const stepTitle = step.title || `Step ${index + 1}`;
            const stepDescription = step.content || `Step ${index + 1}`;

            stepElement.innerHTML = `
                <div class="step-header">
                    <span class="step-number">${index + 1}</span>
                    <span class="step-title">${stepTitle}</span>
                    <span class="step-status">
                        ${step.status === 'running' ? '🔄' :
                    step.status === 'completed' ? '✅' :
                        step.status === 'failed' ? '❌' : '⏳'}
                    </span>
                </div>
                <div class="step-content">
                    <div class="step-description">${stepDescription}</div>
                    ${step.details.length > 0 ? `
                        <div class="step-details">
                            ${step.details.map(detail => `
                                <div class="step-detail ${detail.type}">
                                    <span class="detail-type">${detail.type === 'think' ? '🤔' : '🔧'}</span>
                                    <span class="detail-content">${detail.content}</span>
                                </div>
                            `).join('')}
                        </div>
                    ` : ''}
                </div>
            `;
            stepsContent.appendChild(stepElement);
        });
    }

    // 更新总结显示
    updateSummaryDisplay() {
        if (!this.container) return;

        const summarySection = this.container.querySelector('.flow-summary-section');
        const summaryContent = this.container.querySelector('.flow-summary-content');

        if (this.flowData.summary) {
            summarySection.style.display = 'block';
            summaryContent.innerHTML = `
                <div class="summary-content">
                    <pre>${this.flowData.summary}</pre>
                </div>
            `;
        }
    }

    // 新增：更新交互显示
    updateInteractionDisplay(inquire) {
        if (!this.container) return;

        let interactionSection = this.container.querySelector('.interaction-section');
        if (!interactionSection) {
            // 创建交互区域
            interactionSection = document.createElement('div');
            interactionSection.className = 'interaction-section';
            interactionSection.innerHTML = `
                <div class="section-header">
                    <h4>🤝 需要用户交互</h4>
                </div>
                <div class="interaction-content">
                    <div class="interaction-message">
                        <div class="interaction-icon">🤖</div>
                        <div class="interaction-text">${inquire}</div>
                    </div>
                </div>
            `;

            // 插入到步骤区域之前
            const stepsSection = this.container.querySelector('.flow-steps-section');
            if (stepsSection) {
                stepsSection.parentNode.insertBefore(interactionSection, stepsSection);
            } else {
                this.container.appendChild(interactionSection);
            }
        } else {
            // 更新现有内容
            const contentDiv = interactionSection.querySelector('.interaction-text');
            if (contentDiv) {
                contentDiv.textContent = inquire;
            }
        }
    }

    // 重置数据
    reset() {
        this.flowData = {
            plan: null,
            steps: [],
            summary: null,
            currentStepIndex: -1
        };
        this.containers = [];
        this.currentContainer = null;
        this.executionPhase = 0;
    }
}

// 全局 Flow 展示管理器实例
let flowDisplayManager = null;

// 导出供其他文件使用
window.FlowDisplayManager = FlowDisplayManager;
window.flowDisplayManager = flowDisplayManager;
