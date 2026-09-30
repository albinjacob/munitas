/**
 * The banner shown before anybody is signed in.
 *
 * It is not dismissible. A warning you can close is a warning that is closed,
 * and this one has to survive the whole time nobody is resolved: anyone
 * looking at this console before then is looking at access decisions about
 * clinical recordings, and they need to know at a glance that the name that
 * is about to appear in the sidebar will be proved by a real login, not
 * picked.
 */

export function UnauthenticatedBanner() {
  return (
    <div
      role="status"
      data-testid="unauthenticated-banner"
      className="bg-amber-100 border-b border-amber-300 px-4 py-2 text-sm text-amber-900"
    >
      <strong className="font-semibold">Nobody is signed in.</strong>{" "}
      Sign in to continue. Nothing you can do here is shown to anyone, and
      nothing you have not proved is claimed on your behalf, until you do.
    </div>
  );
}
