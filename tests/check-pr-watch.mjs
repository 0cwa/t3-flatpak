// Exercise the shipped reactor with deterministic API responses, not a live PR.
import assert from "node:assert/strict";
import { prWatchTest as t } from "/app/bin/t3code/resources/app.asar/apps/server/dist/server-bi3w5spO.mjs";
import { es as runPromise } from "/app/bin/t3code/resources/app.asar/apps/server/dist/Schema-znhrSbG8.mjs";

const now = new Date().toISOString();
const initialWatch = () => ({
  startedAt: now, headSha: "abc123", failedChecks: [], passed: false,
  passedChecks: [], conflicting: false, remarksThrough: now,
  remarkIds: [], wakes: 0, checksAwaitedSince: null,
});

await runPromise(t.gen(function* () {
  let watch = initialWatch();
  let status = "pending";
  let commentsFail = false;
  let detailFails = false;
  let hasComment = false;
  let uuid = 0;
  const wakes = [];
  const commands = [];
  const engine = { dispatch: command => t.sync(() => {
    commands.push(command);
    watch = command.watch;
    if (command.wake) wakes.push(command.wake);
  }) };
  const projections = { getThreadsWithPullRequests: () => t.succeed([{
    id: "test-thread", projectId: "test-project", settledAt: null,
    settledOverride: null, archivedAt: null,
    lineage: { relationshipToParent: null },
    pullRequests: [{ host: "github.com", repository: "test/repo", number: 1,
      url: "https://github.com/test/repo/pull/1", source: "agent", snapshot: null, watch }],
  }]) };
  const comment = { id: "new-comment", kind: "comment", author: { login: "reviewer" },
    body: "Review completed", createdAt: new Date(Date.parse(now) + 1).toISOString(),
    editedAt: null, path: null, reviewState: null, url: null };
  const pullRequests = {
    watchFingerprint: () => t.succeed({ status, remarks: hasComment ? "new-comment" : "unchanged" }),
    invalidate: () => t.succeed(undefined),
    detail: () => detailFails ? t.fail(new Error("Check endpoint unavailable")) : t.succeed({
      state: "open", headSha: "abc123", baseBranch: "main", mergeability: "mergeable",
      viewer: "viewer", author: { login: "author" }, capabilities: { checksAlwaysReport: true },
      checks: [{ name: "build", status, required: true,
        url: "https://github.com/test/repo/actions/runs/1" }],
    }),
    activity: () => commentsFail ? t.fail(new Error("Comment endpoint unavailable")) : t.succeed({
      comments: hasComment ? [comment] : [], reviewThreads: [],
      commentsTruncated: false, reviewThreadsTruncated: false,
    }),
  };
  const make = () => t.make.pipe(
    t.provideService(t.OrchestratorV2, engine), t.provideService(t.ProjectionStoreV2, projections),
    t.provideService(t.PullRequestService, pullRequests),
    t.provideService(t.SecureRandom, { uuidV4: () => t.succeed(`test-${++uuid}`) }),
  );
  let reactor = yield* make();
  yield* reactor.sweep;
  assert.equal(wakes.length, 0);

  status = "success";
  hasComment = true;
  commentsFail = true;
  yield* reactor.sweep;
  assert.equal(wakes.length, 1, "Successful required checks must wake despite a failed comment read");
  assert.match(wakes[0].text, /required check passed/);
  assert.equal(watch.remarksThrough, now, "Unavailable comments must not advance their cursor");

  // More than the eight-read give-up threshold must keep a readable PR watched.
  for (let i = 0; i < 9; i++) yield* reactor.sweep;
  assert(watch, "Comment failures must not end a watch with readable check data");
  assert.equal(wakes.length, 1, "Retries must not repeat the successful check notification");

  // Reconstructing the reactor models restart: persisted watch state deduplicates.
  reactor = yield* make();
  yield* reactor.sweep;
  assert.equal(wakes.length, 1);
  commentsFail = false;
  yield* reactor.sweep;
  assert.equal(wakes.length, 2, "Recovered comments must still be delivered");
  assert.match(wakes[1].text, /Review completed/);
  assert.equal(watch.remarksThrough, comment.createdAt);
  yield* reactor.sweep;
  assert.equal(wakes.length, 2, "Recovered comments must be delivered once");

  status = "pending";
  yield* reactor.sweep;
  status = "failure";
  commentsFail = true;
  // Force an activity read while a failed check becomes visible.
  reactor = yield* make();
  yield* reactor.sweep;
  assert.equal(wakes.length, 3);
  assert.match(wakes[2].text, /Checks failed/);

  detailFails = true;
  const before = commands.length;
  yield* reactor.sweep;
  assert.equal(commands.length, before, "Failed detail reads must not invent check updates");
  assert.equal(wakes.length, 3);
}));
console.log("PR watch passed: check completion/failure during comment outage, retry, restart, recovery, and deduplication.");
