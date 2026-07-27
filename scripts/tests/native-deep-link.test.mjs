import assert from "node:assert/strict";
import test from "node:test";
import {
  APPROVED_NATIVE_DEEP_LINK_PREFIX,
  parseApprovedNativeDeepLink
} from "../../apps/mobile/src/platform/native-deep-link.ts";
import {
  APPROVED_ANDROID_DEEP_LINK_PREFIX,
  parseApprovedAndroidDeepLink
} from "../../apps/mobile/src/platform/android-deep-link.ts";

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

test("공용 Deep Link Parser는 승인 Prefix와 iOS·Android 공통 Route 8개만 수락한다", () => {
  assert.equal(APPROVED_NATIVE_DEEP_LINK_PREFIX, "sinsan-daon://app/");
  assert.equal(APPROVED_ANDROID_DEEP_LINK_PREFIX, APPROVED_NATIVE_DEEP_LINK_PREFIX);
  for (const route of approvedRoutes) {
    const value = `sinsan-daon://app/${route}`;
    assert.equal(parseApprovedNativeDeepLink(value), route);
    assert.equal(parseApprovedAndroidDeepLink(value), route);
  }
});

test("공용 Deep Link Parser는 대소문자·Host·Path·Encoding·Query·Fragment 우회를 Fail-close한다", () => {
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
    [APPROVED_NATIVE_DEEP_LINK_PREFIX + "Home", "extra"].join("/"),
    "sinsan-daon://app/UnknownRoute",
    "sinsan-daon://app/%48ome",
    "sinsan-daon://app/Home%2Fextra",
    "sinsan-daon://app/%252FHome",
    "sinsan-daon://app/Home?route=Inbox",
    "sinsan-daon://app/Home#Inbox",
    "sinsan-daon://app@other/Home"
  ];
  for (const value of rejected) {
    assert.equal(parseApprovedNativeDeepLink(value), null, `rejected: ${String(value)}`);
    assert.equal(parseApprovedAndroidDeepLink(value), null, `android rejected: ${String(value)}`);
  }
});
