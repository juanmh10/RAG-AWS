/**
 * Lógica do Frontend para o Chat RAG Híbrido
 */

const app = {
    API_URL: '',
    isReplying: false,

    init() {
        this.cacheDOMElements();
        this.bindEvents();
        this.applyInitialTheme();
        this.addMessage('Olá! Converse com o índice de documentos padrão ou carregue um PDF para uma sessão temporária.', 'ai');
        // Verifica status inicial da sessão para habilitar/desabilitar o chat
        this.checkStatusOnce();
    },

    cacheDOMElements() {
        this.dom = {
            uploadButton: document.getElementById('upload-button'),
            fileInput: document.getElementById('file-input'),
            newChatButton: document.getElementById('new-chat-button'),
            themeToggle: document.getElementById('theme-toggle'),
            messageList: document.getElementById('message-list'),
            messageListContainer: document.getElementById('message-list-container'),
            chatForm: document.getElementById('chat-form'),
            questionInput: document.getElementById('question-input'),
            sendButton: document.getElementById('send-button'),
            themeIconLight: document.getElementById('theme-icon-light'),
            themeIconDark: document.getElementById('theme-icon-dark'),
            charCounter: document.getElementById('char-counter'),
            charCounterContainer: document.getElementById('char-counter-container'),
        };
    },

    bindEvents() {
        this.dom.uploadButton.addEventListener('click', () => this.dom.fileInput.click());
        this.dom.fileInput.addEventListener('change', (e) => this.handleFileUpload(e));
        this.dom.newChatButton.addEventListener('click', () => this.handleNewChat());
        this.dom.themeToggle.addEventListener('click', () => this.toggleTheme());
        this.dom.chatForm.addEventListener('submit', (e) => this.handleSendMessage(e));
        this.dom.questionInput.addEventListener('keydown', (e) => this.handleInputKeydown(e));
        this.dom.questionInput.addEventListener('input', () => {
            this.adjustInputHeight();
            this.updateCharCounter();
        });
    },

    addMessage(text, type) {
        const messageHTML = this.createMessageHTML(text, type);
        this.dom.messageList.insertAdjacentHTML('beforeend', messageHTML);
        this.scrollToBottom();
    },

    async typeMessage(fullText) {
        const messageId = `msg-${Date.now()}`;
        const messageHTML = this.createMessageHTML('<span class="typing-cursor"></span>', 'ai', messageId);
        this.dom.messageList.insertAdjacentHTML('beforeend', messageHTML);
        this.scrollToBottom();

        const contentElement = document.getElementById(messageId).querySelector('.message-content');
        const words = fullText.split(' ');
        let currentText = '';

        for (let i = 0; i < words.length; i++) {
            currentText += (i > 0 ? ' ' : '') + words[i];
            contentElement.innerHTML = `${currentText.replace(/\n/g, '<br>')}<span class="typing-cursor"></span>`;
            this.scrollToBottom();
            await new Promise(resolve => setTimeout(resolve, 50));
        }
        contentElement.innerHTML = fullText.replace(/\n/g, '<br>');
    },

    createMessageHTML(text, type, id = null) {
        const messageId = id ? `id="${id}"` : '';
        const avatarUser = `<div class="avatar"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a5 5 0 015 5v2a5 5 0 01-10 0V7a5 5 0 015-5zm0 12c-3.314 0-6 2.686-6 6v2h12v-2c0-3.314-2.686-6-6-6z"></path></svg></div>`;
        const avatarAi = `<div class="avatar"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><path d="M12 22C6.477 22 2 17.523 2 12S6.477 2 12 2s10 4.477 10 10-4.477 10-10 10zm-3.5-8v-2h7v2h-7zm0-4v-2h7v2h-7z"></path></svg></div>`;
        const messageClass = type === 'user' ? 'user-message' : 'ai-message';
        return `
            <div class="message ${messageClass}" ${messageId}>
                ${type === 'user' ? avatarUser : avatarAi}
                <div class="message-content">${text}</div>
            </div>`;
    },

    setFormState(isLoading) {
        this.isReplying = isLoading;
        this.dom.questionInput.disabled = isLoading;
        this.dom.sendButton.disabled = isLoading;
        this.dom.uploadButton.disabled = isLoading;
        this.dom.newChatButton.disabled = isLoading;
    },

    setUploadState(isUploading) {
        this.dom.uploadButton.classList.toggle('loading', isUploading);
        this.setFormState(isUploading);
    },

    scrollToBottom() {
        this.dom.messageListContainer.scrollTop = this.dom.messageListContainer.scrollHeight;
    },

    adjustInputHeight() {
        this.dom.questionInput.style.height = 'auto';
        this.dom.questionInput.style.height = (this.dom.questionInput.scrollHeight) + 'px';
    },

    updateCharCounter() {
        const currentLength = this.dom.questionInput.value.length;
        this.dom.charCounter.textContent = currentLength;
        this.dom.charCounterContainer.style.color = currentLength > 220 ? '#f97316' : '';
    },

    async handleFileUpload(e) {
        const file = e.target.files[0];
        if (!file) return;

        this.setUploadState(true);
        this.addMessage(`Enviando e processando "${file.name}"...`, 'ai');

        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch(`${this.API_URL}/upload`, { method: 'POST', body: formData, credentials: 'include' });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Falha no upload.');
            this.addMessage(`Documento "${file.name}" carregado. A conversa agora usará este arquivo.`, 'ai');
            this.showToast(`Sessão atualizada com ${file.name}`, 'info');
            // Após upload bem-sucedido, inicia polling de status até ficar ready
            // e armazena session_id retornado pelo servidor se presente
            if (result.session_id) {
                this._sessionId = result.session_id;
            }
            this.startStatusPolling();
        } catch (error) {
            this.showToast(`Erro no upload: ${error.message}`, 'error');
        } finally {
            this.setUploadState(false);
            this.dom.fileInput.value = '';
        }
    },

    async handleNewChat() {
        this.setFormState(true);
        try {
            const response = await fetch(`${this.API_URL}/new-chat`, { method: 'POST', credentials: 'include' });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Falha ao reiniciar a conversa.');
            this.dom.messageList.innerHTML = '';
            this.addMessage('Sessão reiniciada. Converse com o índice de documentos padrão ou carregue um novo PDF.', 'ai');
            this.showToast('Sessão reiniciada.', 'info');
            // Após reiniciar, checa status (provavelmente no state padrão pronto)
            this.checkStatusOnce();
        } catch (error) {
            this.showToast(`Erro: ${error.message}`, 'error');
        } finally {
            this.setFormState(false);
        }
    },

    async handleSendMessage(e) {
        if (e) e.preventDefault();
        if (this.isReplying) return;

        const question = this.dom.questionInput.value.trim();
        if (!question) return;

        this.addMessage(question, 'user');
        this.dom.questionInput.value = '';
        this.adjustInputHeight();
        this.updateCharCounter();
        this.setFormState(true);

        try {
            const headers = { 'Content-Type': 'application/json' };
            if (this._sessionId) headers['X-Session-Id'] = this._sessionId;
            const response = await fetch(`${this.API_URL}/chat`, {
                method: 'POST',
                credentials: 'include',
                headers,
                body: JSON.stringify({ question }),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Não foi possível obter uma resposta.');
            await this.typeMessage(result.answer);
        } catch (error) {
            this.showToast(`Erro: ${error.message}`, 'error');
            this.addMessage('Desculpe, não consegui processar sua pergunta. Tente novamente.', 'ai');
        } finally {
            this.setFormState(false);
            this.dom.questionInput.focus();
        }
    },

    // --- Status polling / controle de UI ---
    async checkStatusOnce() {
        try {
            const resp = await fetch(`${this.API_URL}/status`, { method: 'GET', credentials: 'include' });
            const js = await resp.json();
            const status = js.status || js.state || 'uploaded';
            this.updateChatEnabled(status === 'ready');
            this.updateStatusBadge(status);
            if (status && status !== 'ready' && status !== 'no_session') {
                this.showToast(`Status: ${status}. Aguardando indexação...`, 'info');
            }
        } catch (e) {
            console.warn('Falha ao consultar /status:', e);
        }
    },

    startStatusPolling(intervalMs = 2000, timeoutMs = 5 * 60 * 1000) {
        // Para evitar múltiplos polls simultâneos
        if (this._statusPollHandle) return;
        const startedAt = Date.now();
        this.updateChatEnabled(false);
        this._statusPollHandle = setInterval(async () => {
            try {
                const resp = await fetch(`${this.API_URL}/status`, { method: 'GET', credentials: 'include' });
                const js = await resp.json();
                const status = js.status || 'uploaded';
                if (status === 'ready') {
                    this.showToast('Índice pronto. Chat habilitado.', 'info');
                    this.updateChatEnabled(true);
                    this.updateStatusBadge(status);
                    clearInterval(this._statusPollHandle);
                    this._statusPollHandle = null;
                    return;
                }
                if (status === 'error') {
                    this.showToast('Erro na indexação. Tente reenviar o documento.', 'error');
                    clearInterval(this._statusPollHandle);
                    this._statusPollHandle = null;
                    this.updateStatusBadge(status);
                    return;
                }
            } catch (e) {
                console.warn('Erro no polling de status:', e);
            }
            if (Date.now() - startedAt > timeoutMs) {
                this.showToast('Timeout de indexação. Tente novamente mais tarde.', 'error');
                clearInterval(this._statusPollHandle);
                this._statusPollHandle = null;
            }
        }, intervalMs);
    },

    updateChatEnabled(enabled) {
        this.dom.questionInput.disabled = !enabled;
        this.dom.sendButton.disabled = !enabled;
    },

    updateStatusBadge(status) {
        const el = document.getElementById('session-status');
        if (!el) return;
        if (status === 'ready') {
            el.textContent = 'Índice pronto';
            el.style.background = '#e6ffed';
            el.style.color = '#065f46';
        } else if (status === 'indexing') {
            el.textContent = 'Indexando...';
            el.style.background = '#fff7ed';
            el.style.color = '#92400e';
        } else if (status === 'uploaded') {
            el.textContent = 'Carregado';
            el.style.background = '#e6f4ff';
            el.style.color = '#0369a1';
        } else if (status === 'error') {
            el.textContent = 'Erro na indexação';
            el.style.background = '#ffe6e6';
            el.style.color = '#9b1c1c';
        } else {
            el.textContent = 'Índice padrão';
            el.style.background = '';
            el.style.color = '';
        }
    },

    handleInputKeydown(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            this.handleSendMessage();
        }
    },

    toggleTheme() {
        const isDarkMode = document.body.classList.toggle('dark-mode');
        localStorage.setItem('theme', isDarkMode ? 'dark' : 'light');
        this.updateThemeIcons(isDarkMode);
    },

    applyInitialTheme() {
        const savedTheme = localStorage.getItem('theme') || 'light';
        const isDarkMode = savedTheme === 'dark';
        if (isDarkMode) document.body.classList.add('dark-mode');
        this.updateThemeIcons(isDarkMode);
    },

    updateThemeIcons(isDarkMode) {
        this.dom.themeIconLight.style.display = isDarkMode ? 'none' : 'block';
        this.dom.themeIconDark.style.display = isDarkMode ? 'block' : 'none';
    },

    showToast(message, type = 'info') {
        const toast = document.getElementById('toast');
        toast.textContent = message;
        toast.className = 'show';
        if (type === 'error') toast.classList.add('error');
        setTimeout(() => { toast.className = toast.className.replace('show', ''); }, 3000);
    }
};

document.addEventListener('DOMContentLoaded', () => app.init());