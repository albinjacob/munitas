/**
 * Routing, and the gate in front of it.
 *
 * Without a resolved principal every route redirects to `/auth/login`: a
 * real session or nothing. The gate exists so the audit trail always names
 * somebody who actually signed in, never a principal nobody proved.
 */

import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { UnauthenticatedBanner } from "./identity/IdentityBar";
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
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <UnauthenticatedBanner />
        <p className="p-6 text-sm text-slate-500" role="status">
          Loading the directory
        </p>
      </div>
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
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <UnauthenticatedBanner />
        <Routes>
          <Route path="/roles" element={<Roles />} />
          <Route path="/auth/login" element={<Login />} />
          <Route
            path="*"
            element={<Navigate to="/auth/login" replace state={{ from: location }} />}
          />
        </Routes>
      </div>
    );
  }

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/datasets" element={<Datasets />} />
        <Route path="/datasets/register" element={<RegisterDataset />} />
        <Route path="/departments" element={<Departments />} />
        <Route path="/datasets/:datasetId/ingest" element={<ResumeIngest />} />
        <Route path="/versions/:versionId" element={<VersionDetail />} />
        <Route path="/audit" element={<AuditLog />} />
        <Route path="/gates" element={<GateQueue />} />
        <Route path="/gates/:decisionId" element={<GateDetailScreen />} />
        <Route path="/pipeline-runs/:pipelineRunId" element={<PipelineRun />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/register" element={<RegisterAgent />} />
        <Route path="/agents/:agentId" element={<AgentDetail />} />
        <Route path="/pipelines" element={<Pipelines />} />
        <Route path="/pipelines/register" element={<RegisterPipeline />} />
        <Route path="/pipelines/:pipelineId" element={<PipelineDetail />} />
        <Route path="/action-runs" element={<ActionRuns />} />
        <Route path="/egress-approvals" element={<EgressApprovalQueue />} />
        <Route path="/egress-approvals/:approvalId" element={<EgressApprovalDetailScreen />} />
        <Route path="/services" element={<Services />} />
        <Route path="/housekeeping" element={<Housekeeping />} />
        <Route path="/directory" element={<Directory />} />
        <Route path="/roles" element={<Roles />} />
        <Route path="/closing" element={<Closing />} />
        <Route path="/legal-holds" element={<LegalHolds />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
