"use client";

import { NotificationInboxWorkspace } from "@daon-user/ui/notification-inbox";
import { notificationInboxApi } from "../lib/notification-inbox-api.js";

export function WebNotificationInboxWorkspace(props) {
  return <NotificationInboxWorkspace {...props} api={notificationInboxApi} />;
}
