// Group Queue page: photos waiting to be added to a group pool — retries
// after a group's daily posting limit, or adds scheduled with
// add_to_group(queue=true).

import { QueueConfig, QueuePage } from "./QueuePage";

const CONFIG: QueueConfig = {
  kind: "groups",
  title: "Group Queue",
  intro: (
    <>
      Photos waiting to be added to a group pool — either because the group's daily posting limit
      was reached (retried automatically after it resets) or because they were scheduled for later
      from Chat. The server posts each one at its scheduled time.
    </>
  ),
  emptyText: "Nothing waiting.",
  doneLabel: "added",
  waitingVerb: "Posts",
  doneVerb: "Added",
  failedHeading: "Couldn't add",
  recentHeading: "Recently added",
  nowLabel: "Post now",
  nowAllLabel: "Post all now",
  removeLabel: "Remove",
  nowConfirm: (item) => `Add "${item.photo_title}" to ${item.group_name} now?`,
  nowAllConfirm: (n) => `Try all ${n} waiting group adds now, ignoring their schedules?`,
  removeConfirm: (item) => `Remove "${item.photo_title}" → ${item.group_name} from the queue? It won't be posted.`,
  actionNames: { now: "group_post_now", reschedule: "group_reschedule", remove: "group_remove", due: "group_post_due" },
  dueLabel: "Post due now",
  subtitle: (item) => (
    <div className="pub-group">
      to{" "}
      <a href={item.group_url} target="_blank" rel="noreferrer">{item.group_name}</a>
    </div>
  ),
};

export function GroupQueue() {
  return <QueuePage config={CONFIG} />;
}
