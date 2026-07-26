import assert from "node:assert/strict";
import test from "node:test";
import { APPROVED_ANDROID_DEEP_LINK_PREFIX, parseApprovedAndroidDeepLink } from "../../apps/mobile/src/platform/android-deep-link.ts";

const approvedRoutes = [
  "Home",
  "WorkspaceList",
  "WorkspaceDetail",
  "Inbox",
  "RunHistory",
  "Notifications",
  "ModelConnections",
  "AccountSettings"
];

test("승인 Deep Link는 정확한 Scheme·Host와 기존 Android Route 8개만 수락한다", () => {
  assert.equal(APPROVED_ANDROID_DEEP_LINK_PREFIX, "sinsan-daon://app/");
  for (const route of approvedRoutes) {
    assert.equal(parseApprovedAndroidDeepLink(`sinsan-daon://app/${route}`), route);
  }
});

test("Scheme 대소문자·Host·Path·Encoding 우회와 미등록 Route는 모두 Fail-close한다", () => {
  const rejected = [
    null,
    "",
    "SINSAN-DAON://app/Home",
    "sinsan-daon://APP/Home",
    "sinsan-daon://other/Home",
    "https://app/Home",
    "sinsan-daon://app",
    "sinsan-daon://app/",
    "sinsan-daon://app//Home",
    "sinsan-daon://app/Home/",
    [APPROVED_ANDROID_DEEP_LINK_PREFIX + "Home", "extra"].join("/"),
    "sinsan-daon://app/UnknownRoute",
    "sinsan-daon://app/%48ome",
    "sinsan-daon://app/Home%2Fextra",
    "sinsan-daon://app/%252FHome",
    "sinsan-daon://app/Home?route=Inbox",
    "sinsan-daon://app/Home#Inbox",
    "sinsan-daon://app@other/Home"
  ];
  for (const value of rejected) {
    assert.equal(parseApprovedAndroidDeepLink(value), null, `rejected: ${String(value)}`);
  }
});
