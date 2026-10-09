// Publish Queue page: private photos scheduled to go public (schedule_publish
// / the Photo Viewer's Publishing section).

import { QueueConfig, QueuePage } from "./QueuePage";

const CONFIG: QueueConfig = {
  kind: "publish",
  title: "Publish Queue",
  intro: (
    <>
      Private photos waiting to go public. At the scheduled time the server resets each photo's
      date posted to that moment and makes it public, so it shows up as new in your photostream
      and your contacts' feeds. Schedule a photo from its <em>Publishing</em> section in the Photo
      Viewer, or ask in Chat.
    </>
  ),
  emptyText: "Nothing scheduled.",
  doneLabel: "published",
  waitingVerb: "Goes public",
  doneVerb: "Published",
  failedHeading: "Couldn't publish",
  recentHeading: "Recently published",
  nowLabel: "Publish now",
  nowAllLabel: "Publish all now",
  removeLabel: "Unschedule",
  nowConfirm: (item) => `Make "${item.photo_title}" public now? Its date posted will be reset to now.`,
  nowAllConfirm: (n) => `Make all ${n} waiting photos public now? Their dates posted will be reset to now.`,
  removeConfirm: (item) => `Unschedule "${item.photo_title}"? It stays private.`,
  actionNames: { now: "publish_now", reschedule: "publish_reschedule", remove: "publish_remove" },
  refreshOn: "publishQueueChanged",
};

export function PublishQueue() {
  return <QueuePage config={CONFIG} />;
}
