import { useEffect, useState } from "react";
import type { ApprovalSummary, RendererApi } from "../../preload/api";

const statusLabels: Record<ApprovalSummary["status"], string> = {
  PENDING: "待处理",
  APPROVED: "已批准",
  DENIED: "已拒绝",
  EXPIRED: "已过期",
};

export function ApprovalsRoute({ api, safeMode, onPendingCountChange }: {
  api: RendererApi;
  safeMode: boolean;
  onPendingCountChange?(count: number): void;
}) {
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const next = await api.approvals.list();
      setApprovals(next);
      onPendingCountChange?.(next.filter((item) => item.status === "PENDING").length);
    }
    catch { setError("审批列表暂时无法加载"); }
    finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, []);

  const decide = async (approvalId: string, decision: "approve" | "deny") => {
    setBusyId(approvalId);
    setError("");
    try {
      const updated = await api.approvals.decide({ approvalId, decision });
      const next = approvals.map((item) => item.approval_id === approvalId ? updated : item);
      setApprovals(next);
      onPendingCountChange?.(next.filter((item) => item.status === "PENDING").length);
    } catch {
      setError("审批状态未能更新，请刷新后重试。");
    } finally {
      setBusyId("");
    }
  };

  return (
    <section className="panel approvals-panel">
      <div className="panel-heading">
        <div><span className="eyebrow">HUMAN REVIEW</span><h2>审批</h2></div>
        <span className="simulation-tag">演示审批</span>
      </div>
      <p className="muted">以下请求用于展示人工审批界面，不会触发真实 Tool 执行。</p>
      {error && <div className="inline-error" role="alert">{error}</div>}
      {loading ? <div className="loading-state">正在读取审批…</div> : approvals.length === 0 ? (
        <div className="empty-state"><span className="empty-state__mark" aria-hidden="true">✓</span><strong>当前没有待处理审批</strong><span>新的请求需要由 Policy Engine 判断后进入此处。</span></div>
      ) : (
        <div className="approval-list">
          {approvals.map((approval) => (
            <article className="approval-card" key={approval.approval_id}>
              <div className="approval-card__main">
                <span className="approval-mark" aria-hidden="true">!</span>
                <div><span className="eyebrow">{approval.approval_id}</span><h3>{approval.action}</h3><p>操作风险 <b>{approval.risk_level}</b> · 需要人工决定</p></div>
              </div>
              <div className="approval-card__actions">
                <span className={`pill pill--${approval.status.toLowerCase()}`}>{statusLabels[approval.status]}</span>
                {approval.status === "PENDING" && (
                  <>
                    <button className="button button--secondary" onClick={() => void decide(approval.approval_id, "deny")} disabled={safeMode || busyId === approval.approval_id}>拒绝</button>
                    <button className="button button--primary" onClick={() => void decide(approval.approval_id, "approve")} disabled={safeMode || busyId === approval.approval_id}>批准</button>
                  </>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
      {safeMode && <p className="inline-warning">Safe Mode 下暂停审批决策。</p>}
    </section>
  );
}
