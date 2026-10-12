/**
 * Routing, and the gate in front of it.
 *
 * Without a resolved principal every route redirects to `/auth/login`: a
 * real session or nothing. The gate exists so the audit trail always names
 * somebody who actually signed in, never a principal nobody proved.
 */

import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Shell } from "./components/Shell";
import { RoleOnly } from "./components/RoleOnly";
import { PublicFrame } from "./components/PublicFrame";
import { useIdentity } from "./identity/IdentityContext";
import { Login } from "./identity/Login";
import { Home } from "./features/home/Home";
import { Datasets } from "./features/governance/Datasets";
import { Departments } from "./features/governance/Departments";
import { RegisterDataset, ResumeIngest } from "./features/ingest/RegisterDataset";
import { VersionDetail } from "./features/governance/VersionDetail";
import { AuditLog } from "./features/governance/AuditLog";
import { GateQueue, GateDetailScreen } from "./features/governance/GateDecision";
import { PipelineRun } from "./features/pipeline/PipelineRun";
import { Services } from "./features/operate/Services";
import { Housekeeping } from "./features/housekeeping/Housekeeping";
import { Roles } from "./features/roles/Roles";
import { Directory } from "./features/operate/Directory";
import { Agents } from "./features/agents/Agents";
import { RegisterAgent } from "./features/agents/RegisterAgent";
import { AgentDetail } from "./features/agents/AgentDetail";
import { Pipelines } from "./features/pipelines/Pipelines";
import { RegisterPipeline } from "./features/pipelines/RegisterPipeline";
import { PipelineDetail } from "./features/pipelines/PipelineDetail";
import { ActionRuns } from "./features/pipelines/ActionRuns";
import { EgressApprovalQueue, EgressApprovalDetailScreen } from "./features/agents/EgressApprovals";
import { Closing } from "./features/lifecycle/Closing";
import { ClosingNotice } from "./features/lifecycle/ClosingNotice";
import { LegalHolds } from "./features/lifecycle/LegalHolds";
import { LockedRows } from "./features/locking/LockedRows";
import { LockedRequests } from "./features/locking/LockedRequests";
import { LockingAudit } from "./features/locking/LockingAudit";
import { NotebookCode, NotebookConsent, NotebookDone, NotebookLogin } from "./features/notebook/NotebookSignIn";
import { NotebookSignIns } from "./features/notebook/NotebookSignIns";
import { Limits } from "./features/limits/Limits";
import { DeidentificationStandards } from "./features/governance/DeidentificationStandards";
import { AgentChanges } from "./features/agents/AgentChanges";
import { AgentRegistry } from "./features/agents/AgentRegistry";
import { RiskLevels } from "./features/agents/RiskLevels";
import { ToolList } from "./features/agents/ToolList";

/**
 * Somebody who has just signed in lands on the sign-in page for a moment (the session is still being read). Send them where they were going, which the gate
 * kept, and only to a place inside this console. Without a place they were going, home.
 */
function SignedInNow() {
  const going = (useLocation().state as { from?: { pathname: string; search?: string } } | null)?.from;
  const inside = going && going.pathname.startsWith("/") && !going.pathname.startsWith("//") && going.pathname !== "/auth/login";
  return <Navigate to={inside ? `${going.pathname}${going.search ?? ""}` : "/"} replace />;
}

export default function App() {
  const { principal, loading, closed } = useIdentity();
  const location = useLocation();

  // Wait rather than redirect. The acting persona is resolved against the
  // directory, so for a moment after any page load there is no principal yet.
  // Redirecting during that moment sends every deep link to the chooser and
  // then to the home page, which loses wherever the person was going and looks
  // like the console forgetting who they are.
  if (loading) {
    return (
      <PublicFrame>
        <p className="text-sm text-slate-500" role="status">
          Loading the directory
        </p>
      </PublicFrame>
    );
  }

  // Signed in, and the organisation is closing. Nothing else would load, so say why.
  if (closed) return <ClosingNotice />;

  if (!principal) {
    // The front door carries the banner itself, because it renders outside
    // the shell and U6 asserts the banner is on every route without
    // exception.
    //
    // The roles page is reachable here on purpose. It describes the rules
    // rather than revealing data, and requiring an identity before you can
    // read what the identities mean is backwards.
    return (
      <PublicFrame>
        <Routes>
          <Route path="/roles" element={<Roles />} />
          <Route path="/auth/login" element={<Login />} />
          <Route
            path="*"
            element={<Navigate to="/auth/login" replace state={{ from: location }} />}
          />
        </Routes>
      </PublicFrame>
    );
  }

  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/datasets" element={<Datasets />} />
        <Route path="/auth/login" element={<SignedInNow />} />
        <Route path="/datasets/register" element={<RegisterDataset />} />
        <Route path="/departments" element={<Departments />} />
        <Route path="/datasets/:datasetId/ingest" element={<ResumeIngest />} />
        <Route path="/versions/:versionId" element={<VersionDetail />} />
        <Route path="/versions/:versionId/locked-rows" element={<LockedRows />} />
        <Route path="/locked-column-access" element={<LockedRequests />} />
        <Route path="/notebook-sign-in/code" element={<NotebookCode />} />
        <Route path="/notebook-sign-in/login" element={<NotebookLogin />} />
        <Route path="/notebook-sign-in/consent" element={<NotebookConsent />} />
        <Route path="/notebook-sign-in/done" element={<NotebookDone />} />
        <Route path="/notebook-sign-ins" element={<NotebookSignIns />} />
        <Route path="/limits" element={<Limits />} />
        <Route path="/deidentification-standards" element={<DeidentificationStandards />} />
        <Route path="/access-rules" element={<Navigate to="/limits" replace />} />
        <Route path="/release-bars" element={<Navigate to="/deidentification-standards" replace />} />
        <Route path="/agent-registry" element={<AgentRegistry />} />
        <Route path="/risk-levels" element={<RiskLevels />} />
        <Route path="/approved-tools" element={<ToolList />} />
        <Route path="/agent-changes" element={<AgentChanges />} />
        <Route path="/locking-audit" element={<RoleOnly path="/locking-audit" title="Locked data audit"><LockingAudit /></RoleOnly>} />
        <Route path="/audit" element={<RoleOnly path="/audit" title="Access decisions"><AuditLog /></RoleOnly>} />
        <Route path="/gates" element={<GateQueue />} />
        <Route path="/gates/:decisionId" element={<GateDetailScreen />} />
        <Route path="/pipeline-runs/:pipelineRunId" element={<PipelineRun />} />
        <Route path="/agents" element={<RoleOnly path="/agents" title="Agents"><Agents /></RoleOnly>} />
        <Route path="/agents/register" element={<RegisterAgent />} />
        <Route path="/agents/:agentId" element={<AgentDetail />} />
        <Route path="/pipelines" element={<Pipelines />} />
        <Route path="/pipelines/register" element={<RegisterPipeline />} />
        <Route path="/pipelines/:pipelineId" element={<PipelineDetail />} />
        <Route path="/action-runs" element={<RoleOnly path="/action-runs" title="Action runs"><ActionRuns /></RoleOnly>} />
        <Route path="/egress-approvals" element={<RoleOnly path="/egress-approvals" title="Egress approvals"><EgressApprovalQueue /></RoleOnly>} />
        <Route path="/egress-approvals/:approvalId" element={<EgressApprovalDetailScreen />} />
        <Route path="/services" element={<RoleOnly path="/services" title="Services"><Services /></RoleOnly>} />
        <Route path="/housekeeping" element={<Housekeeping />} />
        <Route path="/directory" element={<Directory />} />
        <Route path="/roles" element={<Roles />} />
        <Route path="/closing" element={<Closing />} />
        <Route path="/legal-holds" element={<RoleOnly path="/legal-holds" title="Legal holds"><LegalHolds /></RoleOnly>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
