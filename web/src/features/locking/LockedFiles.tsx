/**
 * A sealed version's files, on the page of the version that holds them.
 *
 * Each file is locked under the key of the patient it is about. This says how many there are and how many patients, and what opens them: a
 * pipeline run, while it is running on this version, and nothing else. It shows counts and never a file.
 */

import { useFilesSummary } from "../../api/files";
import { DevelopmentSamplesSection } from "./DevelopmentSamples";
import { Failure, Section } from "../../components/states";
import { ApiError } from "../../api/client";

export function LockedFilesSection({ versionId }: { versionId: string }) {
  const summary = useFilesSummary(versionId);
  // Most versions hold no locked files: nothing to show, and nothing to explain.
  if (summary.isLoading || (summary.isSuccess && summary.data === null)) return null;
  if (summary.error && !(summary.error instanceof ApiError && summary.error.status === 404)) {
    return <Failure error={summary.error} what="the locked files of this version" />;
  }
  const s = summary.data;
  if (!s) return null;
  const erased = s.erased_files;

  return (
    <>
    <Section
      title="Locked files"
      description="Each file is locked under the key of the person it is about. Destroying a person's key makes their files unreadable for good, and the sealed version never changes."
    >
      <dl data-testid="locked-files" className="grid max-w-xl grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
        <dt className="text-slate-500">Files</dt>
        <dd>{s.files}</dd>
        <dt className="text-slate-500">People</dt>
        <dd>{s.people}</dd>
        <dt className="text-slate-500">Unreadable because the person was erased</dt>
        <dd data-testid="locked-files-erased">{erased}</dd>
      </dl>
      <p className="mt-2 text-xs text-slate-500">
        A pipeline run opens a file only while it is running on this version, and every opening is recorded with the run named. A run leaves out the
        files of people who were erased.
      </p>
    </Section>
    <DevelopmentSamplesSection versionId={versionId} />
    </>
  );
}
