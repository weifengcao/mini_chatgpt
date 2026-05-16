import React from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, Bot, Calculator, LogIn, MessageSquare, Plus, Send, Shield } from 'lucide-react';
import './styles.css';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

type User = {
  id: string;
  email: string;
  role: string;
  company_id: string;
  status: string;
};

type Agent = {
  id: string;
  name: string;
  description: string;
  model_provider: string;
  model_name: string;
  tool_allowlist_json: string[];
  status: string;
};

type ChatSession = {
  id: string;
  agent_id: string;
  title: string;
  status: string;
};

type Message = {
  id: string;
  user_id: string | null;
  role: string;
  content: string;
  status: string;
  sequence_number: number;
};

type AgentRun = {
  id: string;
  status: string;
  error_code: string | null;
  created_at: string;
};

type SseEvent = {
  event: string;
  data: Record<string, unknown>;
};

function parseSse(buffer: string): { events: SseEvent[]; rest: string } {
  const chunks = buffer.split('\n\n');
  const rest = chunks.pop() || '';
  const events = chunks
    .map((chunk) => {
      const eventLine = chunk.split('\n').find((line) => line.startsWith('event:'));
      const dataLine = chunk.split('\n').find((line) => line.startsWith('data:'));
      if (!eventLine || !dataLine) return null;
      try {
        return {
          event: eventLine.replace('event:', '').trim(),
          data: JSON.parse(dataLine.replace('data:', '').trim())
        };
      } catch {
        return null;
      }
    })
    .filter(Boolean) as SseEvent[];
  return { events, rest };
}

function App() {
  const [token, setToken] = React.useState(localStorage.getItem('mini_chatgpt_token') || '');
  const [user, setUser] = React.useState<User | null>(null);
  const [agents, setAgents] = React.useState<Agent[]>([]);
  const [sessions, setSessions] = React.useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = React.useState<ChatSession | null>(null);
  const [messages, setMessages] = React.useState<Message[]>([]);
  const [runs, setRuns] = React.useState<AgentRun[]>([]);
  const [metrics, setMetrics] = React.useState<Record<string, unknown>>({});
  const [email, setEmail] = React.useState('admin@mini.local');
  const [password, setPassword] = React.useState('password');
  const [draft, setDraft] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');

  const authHeaders = React.useMemo(() => ({ Authorization: `Bearer ${token}` }), [token]);

  async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? authHeaders : {}),
        ...(options.headers || {})
      }
    });
    if (!response.ok) {
      const body = await response.text();
      throw new Error(body || response.statusText);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  async function loadAll() {
    const [me, agentList, sessionList, runList] = await Promise.all([
      api<User>('/api/auth/me'),
      api<Agent[]>('/api/agents'),
      api<ChatSession[]>('/api/chat-sessions'),
      api<AgentRun[]>('/api/agent-runs')
    ]);
    setUser(me);
    setAgents(agentList);
    setSessions(sessionList);
    setRuns(runList);
    if (me.role === 'SUPER_ADMIN') {
      api<{ data: Record<string, unknown> }>('/api/metrics/company').then((result) => setMetrics(result.data)).catch(() => {});
    }
    const firstSession = sessionList[0] || null;
    setActiveSession((current) => current || firstSession);
    if (firstSession) {
      setMessages(await api<Message[]>(`/api/chat-sessions/${firstSession.id}/messages`));
    }
  }

  React.useEffect(() => {
    if (!token) return;
    loadAll().catch((err) => {
      setError(err.message);
      setToken('');
      localStorage.removeItem('mini_chatgpt_token');
    });
  }, [token]);

  async function login(event: React.FormEvent) {
    event.preventDefault();
    setError('');
    const result = await api<{ access_token: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password })
    });
    localStorage.setItem('mini_chatgpt_token', result.access_token);
    setToken(result.access_token);
  }

  async function createSession(agent: Agent) {
    const session = await api<ChatSession>('/api/chat-sessions', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agent.id, title: `Chat with ${agent.name}` })
    });
    setSessions((items) => [session, ...items]);
    setActiveSession(session);
    setMessages([]);
  }

  async function selectSession(session: ChatSession) {
    setActiveSession(session);
    setMessages(await api<Message[]>(`/api/chat-sessions/${session.id}/messages`));
  }

  async function sendMessage(event: React.FormEvent) {
    event.preventDefault();
    if (!activeSession || !draft.trim() || busy) return;
    setBusy(true);
    setError('');
    const content = draft.trim();
    setDraft('');
    const optimisticId = `local-${crypto.randomUUID()}`;
    const optimisticAssistantId = `assistant-${optimisticId}`;
    const rollbackOptimistic = () => {
      setMessages((items) => items.filter((message) => message.id !== optimisticId && message.id !== optimisticAssistantId));
    };
    setMessages((items) => [
      ...items,
      { id: optimisticId, user_id: user?.id || null, role: 'user', content, status: 'complete', sequence_number: items.length + 1 },
      { id: optimisticAssistantId, user_id: null, role: 'assistant', content: '', status: 'streaming', sequence_number: items.length + 2 }
    ]);

    let sawServerMessage = false;
    try {
      const response = await fetch(`${API_BASE}/api/chat-sessions/${activeSession.id}/messages/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
          'Idempotency-Key': crypto.randomUUID()
        },
        body: JSON.stringify({ content })
      });
      if (!response.ok || !response.body) {
        throw new Error(await response.text() || response.statusText);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let rest = '';
      let assistantId = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const parsed = parseSse(rest + decoder.decode(value, { stream: true }));
        rest = parsed.rest;
        for (const item of parsed.events) {
          if (item.event === 'message_started') {
            sawServerMessage = true;
            assistantId = String(item.data.message_id || '');
          }
          if (item.event === 'token') {
            const text = String(item.data.text || '');
            setMessages((items) => {
              const copy = [...items];
              const targetIndex = copy.findIndex((message) => message.id === optimisticAssistantId);
              if (targetIndex >= 0) {
                copy[targetIndex] = {
                  ...copy[targetIndex],
                  id: assistantId || copy[targetIndex].id,
                  content: copy[targetIndex].content + text
                };
              }
              return copy;
            });
          }
          if (item.event === 'error') {
            setError(String(item.data.message || item.data.code || 'Request failed'));
          }
        }
      }

      await Promise.all([
        api<Message[]>(`/api/chat-sessions/${activeSession.id}/messages`).then(setMessages),
        api<AgentRun[]>('/api/agent-runs').then(setRuns)
      ]);
    } catch (err) {
      if (sawServerMessage) {
        await Promise.allSettled([
          api<Message[]>(`/api/chat-sessions/${activeSession.id}/messages`).then(setMessages),
          api<AgentRun[]>('/api/agent-runs').then(setRuns)
        ]);
      } else {
        rollbackOptimistic();
      }
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <main className="login-shell">
        <form className="login-panel" onSubmit={login}>
          <div className="brand-row">
            <Bot size={28} />
            <h1>Mini ChatGPT</h1>
          </div>
          <label>
            Email
            <input value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          <button type="submit">
            <LogIn size={18} />
            Sign in
          </button>
          {error && <p className="error">{error}</p>}
        </form>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-row compact">
          <Bot size={22} />
          <strong>Mini ChatGPT</strong>
        </div>
        <section>
          <h2><Shield size={16} /> Agents</h2>
          {agents.map((agent) => (
            <button key={agent.id} className="list-button" onClick={() => createSession(agent)}>
              <Plus size={15} />
              <span>{agent.name}</span>
            </button>
          ))}
        </section>
        <section>
          <h2><MessageSquare size={16} /> Sessions</h2>
          {sessions.map((session) => (
            <button
              key={session.id}
              className={`list-button ${activeSession?.id === session.id ? 'active' : ''}`}
              onClick={() => selectSession(session)}
            >
              <span>{session.title}</span>
            </button>
          ))}
        </section>
      </aside>

      <section className="chat-pane">
        <header>
          <div>
            <h1>{activeSession?.title || 'Start a chat'}</h1>
            <p>{user?.email}</p>
          </div>
          <button className="secondary" onClick={() => { localStorage.removeItem('mini_chatgpt_token'); setToken(''); }}>
            Sign out
          </button>
        </header>

        <div className="messages">
          {messages.map((message) => (
            <article key={message.id} className={`message ${message.role}`}>
              <span>{message.role}</span>
              <p>{message.content || (message.status === 'streaming' ? '...' : '')}</p>
            </article>
          ))}
        </div>

        {error && <div className="error-bar">{error}</div>}

        <form className="composer" onSubmit={sendMessage}>
          <input
            disabled={!activeSession || busy}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={busy ? 'Assistant is responding...' : 'Message the agent, or type /calc 2 + 2'}
          />
          <button disabled={!activeSession || busy || !draft.trim()} type="submit">
            <Send size={18} />
          </button>
        </form>
      </section>

      <aside className="inspector">
        <section>
          <h2><Activity size={16} /> Metrics</h2>
          {Object.entries(metrics).map(([key, value]) => (
            <div className="metric" key={key}>
              <span>{key}</span>
              <strong>{String(value)}</strong>
            </div>
          ))}
        </section>
        <section>
          <h2><Calculator size={16} /> Agent Runs</h2>
          {runs.slice(0, 8).map((run) => (
            <div key={run.id} className="run-row">
              <span>{run.status}</span>
              <code>{run.id.slice(0, 12)}</code>
            </div>
          ))}
        </section>
      </aside>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
