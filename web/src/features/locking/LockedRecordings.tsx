/**
 * A sealed version's recordings, on the page of the version that holds them.
 *
 * Each recording is locked under the key of the patient it is about. This says how many there are and how many patients, and what opens them: a
 * pipeline run, while it is running on this version, and nothing else. It shows counts and never a recording.
 */

import { useRecordingsSummary } from "../../api/recordings";
import { DevelopmentSamplesSection } from "./DevelopmentSamples";
import { Failure, Section } from "../../components/states";
import { ApiError } from "../../api/client";

export function LockedRecordingsSection({ versionId }: { versionId: string }) {
  const summary = useRecordingsSummary(versionId);
  // Most versions hold no locked recordings: nothing to show, and nothing to explain.
  if (summary.isLoading || (summary.isSuccess && summary.data === null)) return null;
  if (summary.error && !(summary.error instanceof ApiError && summary.error.status === 404)) {
    return <Failure error={summary.error} what="the locked recordings of this version" />;
  }
  const s = summary.data;
  if (!s) return null;
  const erased = s.erased_recordings;

  return (
    <>
    <Section
      title="Locked recordings"
      description="Each recording is locked under the key of the patient it is about. Destroying a patient's key makes their recordings unreadable for good, and the sealed version never changes."
    >
      <dl data-testid="locked-recordings" className="grid max-w-xl grid-cols-[auto,1fr] gap-x-6 gap-y-1 text-sm">
        <dt className="text-slate-500">Recordings</dt>
        <dd>{s.recordings}</dd>
        <dt className="text-slate-500">Patients</dt>
        <dd>{s.patients}</dd>
        <dt className="text-slate-500">Unreadable because their patient was erased</dt>
        <dd data-testid="locked-recordings-erased">{erased}</dd>
      </dl>
      <p className="mt-2 text-xs text-slate-500">
        A pipeline run opens a recording only while it is running on this version, and every opening is recorded with the run named. A run leaves out the
        recordings of patients who were erased.
      </p>
    </Section>
    <DevelopmentSamplesSection versionId={versionId} />
    </>
  );
}
