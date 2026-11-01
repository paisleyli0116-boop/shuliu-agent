import { useEffect, useState } from "react";
import type { ApprovalSummary, RendererApi } from "../../preload/api";
import { Button, DataNotice, EmptyState } from "../components/design-system";

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
      <DataNotice tone="warning" title="本地演示审批">列表来自 Electron 进程内演示服务；批准或拒绝只修改演示状态，不会调用 Policy、Approval Service 或 Tool。</DataNotice>
      {error && <div className="inline-error" role="alert">{error}</div>}
      {loading ? <div className="loading-state">正在读取审批…</div> : approvals.length === 0 ? (
        <EmptyState title="当前没有演示审批" description="真实审批服务与持久化运行记录尚未接入此桌面端。" icon="✓" />
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
                    <Button variant="secondary" onClick={() => void decide(approval.approval_id, "deny")} disabled={safeMode || busyId === approval.approval_id}>演示拒绝</Button>
                    <Button variant="primary" onClick={() => void decide(approval.approval_id, "approve")} disabled={safeMode || busyId === approval.approval_id}>演示批准</Button>
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
