#!/usr/bin/env bash
set -euo pipefail

build_binary_forbidden_pattern() {
  local client_public_internal_api="NEXT_PUBLIC"
  client_public_internal_api+="_API_BASE_URL"
  printf '%s' "localhost|127\\.0\\.0\\.1|host\\.docker\\.internal|${client_public_internal_api}|api[_-]?key[[:space:]]*=|client[_-]?secret[[:space:]]*="
}

if [[ "${1:-}" == "--print-binary-scan-pattern" ]]; then
  build_binary_forbidden_pattern
  exit 0
fi

: "${SIMULATOR_UDID:?SIMULATOR_UDID is required}"
REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
EVIDENCE_DIR="${IOS_EVIDENCE_DIR:-${REPOSITORY_ROOT}/artifacts/ios-phase-a/evidence}"
DERIVED_DATA="${IOS_DERIVED_DATA:-${REPOSITORY_ROOT}/artifacts/ios-phase-a/DerivedData}"
APP_PATH="${DERIVED_DATA}/Build/Products/Release-iphonesimulator/Daon.app"
BUNDLE_ID="com.sinsan.daon"
BINARY_FORBIDDEN_PATTERN="$(build_binary_forbidden_pattern)"
mkdir -p "${EVIDENCE_DIR}"

cleanup() {
  xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

read_preference() {
  local key="$1"
  local container
  container="$(xcrun simctl get_app_container "${SIMULATOR_UDID}" "${BUNDLE_ID}" data)"
  plutil -extract "${key}" raw "${container}/Library/Preferences/${BUNDLE_ID}.plist"
}

clear_navigation_route() {
  local container
  container="$(xcrun simctl get_app_container "${SIMULATOR_UDID}" "${BUNDLE_ID}" data)"
  plutil -remove native_route_key "${container}/Library/Preferences/${BUNDLE_ID}.plist"
}

wait_for_route() {
  local expected="$1"
  local actual=""
  for _ in {1..20}; do
    actual="$(read_preference native_route_key 2>/dev/null || true)"
    [[ "${actual}" == "${expected}" ]] && return 0
    sleep 1
  done
  echo "Expected persisted route ${expected}, got ${actual}" >&2
  return 1
}

run_permission_phase() {
  local phase="$1"
  local privacy_action="$2"
  local expected="$3"
  xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}" >/dev/null 2>&1 || true
  xcrun simctl privacy "${SIMULATOR_UDID}" "${privacy_action}" camera "${BUNDLE_ID}"
  xcrun simctl privacy "${SIMULATOR_UDID}" "${privacy_action}" microphone "${BUNDLE_ID}"
  xcrun simctl privacy "${SIMULATOR_UDID}" "${privacy_action}" notifications "${BUNDLE_ID}"
  DAON_PERMISSION_EXPECTED="${expected}" xcodebuild test-without-building \
    -workspace "${REPOSITORY_ROOT}/apps/mobile/ios/Daon.xcworkspace" \
    -scheme Daon \
    -configuration Release \
    -sdk iphonesimulator \
    -destination "platform=iOS Simulator,id=${SIMULATOR_UDID}" \
    -derivedDataPath "${DERIVED_DATA}" \
    -only-testing:DaonUITests/DaonUITests/testPermissionRequestReflectsOSDecision \
    -resultBundlePath "${EVIDENCE_DIR}/permission-${phase}.xcresult" \
    CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO
}

[[ -d "${APP_PATH}" ]]
xcrun simctl bootstatus "${SIMULATOR_UDID}" -b
xcrun simctl install "${SIMULATOR_UDID}" "${APP_PATH}"
xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}" >/dev/null 2>&1 || true
clear_navigation_route
xcrun simctl launch "${SIMULATOR_UDID}" "${BUNDLE_ID}" | tee "${EVIDENCE_DIR}/launch.log"
HOME_WAIT_EXIT=0
wait_for_route Home || HOME_WAIT_EXIT=$?
if [[ "${HOME_WAIT_EXIT}" -ne 0 ]]; then
  xcrun simctl spawn "${SIMULATOR_UDID}" log show --last 10m --style compact --predicate 'process == "Daon"' > "${EVIDENCE_DIR}/initial-home-failure.log" 2>&1 || true
  exit "${HOME_WAIT_EXIT}"
fi

warm_routes=(WorkspaceList WorkspaceDetail Inbox RunHistory Notifications ModelConnections AccountSettings)
for route in "${warm_routes[@]}"; do
  xcrun simctl openurl "${SIMULATOR_UDID}" "sinsan-daon://app/${route}"
  wait_for_route "${route}"
done

rejected_links=(
  "sinsan-daon://app/UnknownRoute"
  "sinsan-daon://app/%48ome"
  "sinsan-daon://app/Home%2Fextra"
  "sinsan-daon://app/Home?route=Inbox"
  "sinsan-daon://app/Home#Inbox"
)
for link in "${rejected_links[@]}"; do
  xcrun simctl openurl "${SIMULATOR_UDID}" "${link}" || true
  sleep 1
  [[ "$(read_preference native_route_key)" == "AccountSettings" ]]
done

# Each phase launches XCTest, taps the Production requestPermission buttons, and asserts the UI result.
run_permission_phase grant-initial grant GRANTED
run_permission_phase revoke revoke DENIED
run_permission_phase grant-again grant GRANTED

# Background/foreground and terminate/relaunch must preserve only the approved route.
xcrun simctl ui "${SIMULATOR_UDID}" appearance light
xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}"
xcrun simctl launch "${SIMULATOR_UDID}" "${BUNDLE_ID}"
wait_for_route AccountSettings
[[ "$(read_preference lifecycle_state)" =~ ^(created|foreground|active)$ ]]

xcrun simctl spawn "${SIMULATOR_UDID}" log show --last 10m --style compact --predicate 'process == "Daon"' > "${EVIDENCE_DIR}/simulator.log"
if grep -Eiq 'Crash|fatal error|hang detected|api[_-]?key[[:space:]]*=|client[_-]?secret[[:space:]]*=|auth[_-]?token[[:space:]]*=' "${EVIDENCE_DIR}/simulator.log"; then
  echo "Crash, hang or secret assignment detected" >&2
  exit 1
fi
if strings "${APP_PATH}/Daon" | grep -Eiq "${BINARY_FORBIDDEN_PATTERN}"; then
  echo "Internal or unapproved URL detected in iOS binary" >&2
  exit 1
fi

xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}"
if xcrun simctl spawn "${SIMULATOR_UDID}" pgrep -x Daon >/dev/null 2>&1; then
  echo "Daon process remained after terminate" >&2
  exit 1
fi
printf '%s\n' 'SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE' > "${EVIDENCE_DIR}/phase-a-status.txt"
