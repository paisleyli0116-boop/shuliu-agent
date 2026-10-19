import { useEffect, useState } from "react";
import type { RendererApi, TaskSummary } from "../../preload/api";
import { safeUiError } from "../api/client";

const statusLabels: Record<TaskSummary["status"], string> = {
  QUEUED: "排队中",
  RUNNING: "运行中",
  WAITING: "等待审批",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
};

export function TasksRoute({ api, safeMode }: { api: RendererApi; safeMode: boolean }) {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setTasks(await api.tasks.list());
    } catch {
      setError("任务暂时无法加载");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const createTask = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanTitle = title.trim();
    if (cleanTitle.length < 2 || safeMode) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.tasks.create({ title: cleanTitle });
      setTasks((current) => [created, ...current]);
      setTitle("");
    } catch (failure) {
      setError(safeUiError(failure, "任务创建失败，请稍后重试。"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-grid page-grid--tasks">
      <section className="panel task-list-panel">
        <div className="panel-heading">
          <div><span className="eyebrow">WORK QUEUE</span><h2>任务</h2></div>
          <button className="button button--quiet" onClick={() => void load()} disabled={loading}>刷新</button>
        </div>
        <div className="table-head"><span>任务名称</span><span>状态</span><span>创建时间</span></div>
        {loading ? <div className="loading-state">正在读取任务…</div> : tasks.length === 0 ? (
          <div className="empty-state">
            <span className="empty-state__mark" aria-hidden="true">＋</span>
            <strong>还没有任务</strong>
            <span>创建一个通用任务，体验 Foundation 工作流界面。</span>
          </div>
        ) : (
          <div className="task-list">
            {tasks.map((task) => (
              <article className="task-row" key={task.task_id}>
                <div className="task-row__title"><span className="task-glyph" aria-hidden="true">↗</span><strong>{task.title}</strong><small>{task.task_id.slice(0, 8)}</small></div>
                <span className={`pill pill--${task.status.toLowerCase()}`}>{statusLabels[task.status]}</span>
                <time>{formatDate(task.created_at)}</time>
              </article>
            ))}
          </div>
        )}
        {error && <div className="inline-error" role="alert">{error}<button className="text-button" onClick={() => void load()}>重新加载</button></div>}
      </section>

      <section className="panel create-task-panel">
        <span className="eyebrow">NEW RUN</span>
        <h2>创建任务</h2>
        <p className="muted">输入一个清晰的任务目标。当前工作台使用本地演示队列，不会调用 Agent Tool。</p>
        <form onSubmit={(event) => void createTask(event)}>
          <label className="field">
            <span>任务名称</span>
            <textarea value={title} onChange={(event) => setTitle(event.currentTarget.value)} maxLength={120} rows={3} placeholder="例如：总结本周运行状态" disabled={busy || safeMode} />
            <small>{title.length}/120</small>
          </label>
          {safeMode && <p className="inline-warning">Safe Mode 下暂停创建任务。</p>}
          <button className="button button--primary button--full" type="submit" disabled={busy || safeMode || title.trim().length < 2}>
            {busy ? "正在创建…" : "创建任务"}
          </button>
        </form>
      </section>
    </div>
  );
}

function formatDate(value: string): string {
  if (value === "刚刚") return value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}
