#!/usr/bin/env bash
set -Eeuo pipefail

build_binary_forbidden_pattern() {
  local client_public_internal_api="NEXT_PUBLIC"
  client_public_internal_api+="_API_BASE_URL"
  printf '%s' "localhost|127\\.0\\.0\\.1|host\\.docker\\.internal|${client_public_internal_api}|api[_-]?key[[:space:]]*=|client[_-]?secret[[:space:]]*="
}

if [[ "${1:-}" == "--print-binary-scan-pattern" ]]; then
  build_binary_forbidden_pattern
  exit 0
fi

DAON_SIM_STAGE="INITIALIZE"
DAON_SIM_PERMISSION_SERVICE=""

is_allowed_sim_stage() {
  case "$1" in
    INITIALIZE|APP_ARTIFACT|BOOT_STATUS|INSTALL|INITIAL_TERMINATE|ROUTE_CLEAR|LAUNCH|HOME_READY|PERMISSION_GRANT_INITIAL|PERMISSION_REVOKE|PERMISSION_GRANT_AGAIN|LIFECYCLE_APPEARANCE|LIFECYCLE_TERMINATE|LIFECYCLE_RELAUNCH|LIFECYCLE_READY|LIFECYCLE_STATE|FINAL_LOG_CAPTURE|FINAL_LOG_SCAN|FINAL_BINARY_SCAN|FINAL_TERMINATE|FINAL_PROCESS_CHECK|STATUS_WRITE|UNCLASSIFIED) return 0 ;;
    *) return 1 ;;
  esac
}

is_allowed_permission_service() {
  case "$1" in
    camera|microphone|notifications) return 0 ;;
    *) return 1 ;;
  esac
}

report_simulator_failure() {
  local failed_exit=$?
  local failed_stage="UNCLASSIFIED"
  if is_allowed_sim_stage "${DAON_SIM_STAGE}"; then
    failed_stage="${DAON_SIM_STAGE}"
  fi
  printf 'DAON_SIM_FAILED_STAGE=%s\n' "${failed_stage}" >&2
  if is_allowed_permission_service "${DAON_SIM_PERMISSION_SERVICE}"; then
    printf 'DAON_SIM_FAILED_PERMISSION_SERVICE=%s\n' "${DAON_SIM_PERMISSION_SERVICE}" >&2
  fi
  printf 'DAON_SIM_FAILED_EXIT=%d\n' "${failed_exit}" >&2
  exit "${failed_exit}"
}

trap report_simulator_failure ERR

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
  return 1
}

is_approved_route() {
  case "$1" in
    Home|WorkspaceList|WorkspaceDetail|Inbox|RunHistory|Notifications|ModelConnections|AccountSettings) return 0 ;;
    *) return 1 ;;
  esac
}

wait_for_route_with_evidence() {
  local expected="$1"
  local actual=""
  local wait_exit=0
  local evidence_file

  is_approved_route "${expected}" || return 64
  wait_for_route "${expected}" || wait_exit=$?
  [[ "${wait_exit}" -eq 0 ]] && return 0

  actual="$(read_preference native_route_key 2>/dev/null || true)"
  evidence_file="${EVIDENCE_DIR}/route-wait-failure-${expected}.log"
  xcrun simctl spawn "${SIMULATOR_UDID}" log show --last 10m --style compact --predicate 'process == "Daon"' > "${evidence_file}" 2>&1 || true

  printf 'DAON_ROUTE_WAIT_EXPECTED=%s\n' "${expected}" >&2
  if is_approved_route "${actual}"; then
    printf 'DAON_ROUTE_WAIT_ACTUAL=%s\n' "${actual}" >&2
  fi
  grep -Eo 'DAON_PENDING_DEEP_LINK_RECEIVED|DAON_ROUTE_SAVED=(Home|WorkspaceList|WorkspaceDetail|Inbox|RunHistory|Notifications|ModelConnections|AccountSettings)|DAON_LIFECYCLE_STATE=(created|background|foreground|active)' "${evidence_file}" >&2 || true
  return "${wait_exit}"
}

run_permission_phase() {
  local phase="$1"
  local privacy_action="$2"
  local expected="$3"
  xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}" >/dev/null 2>&1 || true
  DAON_SIM_PERMISSION_SERVICE="camera"
  xcrun simctl privacy "${SIMULATOR_UDID}" "${privacy_action}" camera "${BUNDLE_ID}"
  DAON_SIM_PERMISSION_SERVICE="microphone"
  xcrun simctl privacy "${SIMULATOR_UDID}" "${privacy_action}" microphone "${BUNDLE_ID}"
  DAON_SIM_PERMISSION_SERVICE="notifications"
  xcrun simctl privacy "${SIMULATOR_UDID}" "${privacy_action}" notifications "${BUNDLE_ID}"
  DAON_SIM_PERMISSION_SERVICE=""
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

DAON_SIM_STAGE="APP_ARTIFACT"
[[ -d "${APP_PATH}" ]]
DAON_SIM_STAGE="BOOT_STATUS"
xcrun simctl bootstatus "${SIMULATOR_UDID}" -b
DAON_SIM_STAGE="INSTALL"
xcrun simctl install "${SIMULATOR_UDID}" "${APP_PATH}"
DAON_SIM_STAGE="INITIAL_TERMINATE"
xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}" >/dev/null 2>&1 || true
DAON_SIM_STAGE="ROUTE_CLEAR"
clear_navigation_route
DAON_SIM_STAGE="LAUNCH"
xcrun simctl launch "${SIMULATOR_UDID}" "${BUNDLE_ID}" | tee "${EVIDENCE_DIR}/launch.log"
DAON_SIM_STAGE="HOME_READY"
wait_for_route_with_evidence Home

# Each phase launches XCTest, taps the Production requestPermission buttons, and asserts the UI result.
DAON_SIM_STAGE="PERMISSION_GRANT_INITIAL"
run_permission_phase grant-initial grant GRANTED
DAON_SIM_STAGE="PERMISSION_REVOKE"
run_permission_phase revoke revoke DENIED
DAON_SIM_STAGE="PERMISSION_GRANT_AGAIN"
run_permission_phase grant-again grant GRANTED

# Background/foreground and terminate/relaunch must preserve only the approved route.
DAON_SIM_STAGE="LIFECYCLE_APPEARANCE"
xcrun simctl ui "${SIMULATOR_UDID}" appearance light
DAON_SIM_STAGE="LIFECYCLE_TERMINATE"
xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}"
DAON_SIM_STAGE="LIFECYCLE_RELAUNCH"
xcrun simctl launch "${SIMULATOR_UDID}" "${BUNDLE_ID}"
DAON_SIM_STAGE="LIFECYCLE_READY"
wait_for_route_with_evidence Home
DAON_SIM_STAGE="LIFECYCLE_STATE"
[[ "$(read_preference lifecycle_state)" =~ ^(created|foreground|active)$ ]]

DAON_SIM_STAGE="FINAL_LOG_CAPTURE"
xcrun simctl spawn "${SIMULATOR_UDID}" log show --last 10m --style compact --predicate 'process == "Daon"' > "${EVIDENCE_DIR}/simulator.log"
DAON_SIM_STAGE="FINAL_LOG_SCAN"
if grep -Eiq 'Crash|fatal error|hang detected|api[_-]?key[[:space:]]*=|client[_-]?secret[[:space:]]*=|auth[_-]?token[[:space:]]*=' "${EVIDENCE_DIR}/simulator.log"; then
  echo "Crash, hang or secret assignment detected" >&2
  exit 1
fi
DAON_SIM_STAGE="FINAL_BINARY_SCAN"
if strings "${APP_PATH}/Daon" | grep -Eiq "${BINARY_FORBIDDEN_PATTERN}"; then
  echo "Internal or unapproved URL detected in iOS binary" >&2
  exit 1
fi

DAON_SIM_STAGE="FINAL_TERMINATE"
xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}"
DAON_SIM_STAGE="FINAL_PROCESS_CHECK"
if xcrun simctl spawn "${SIMULATOR_UDID}" pgrep -x Daon >/dev/null 2>&1; then
  echo "Daon process remained after terminate" >&2
  exit 1
fi
DAON_SIM_STAGE="STATUS_WRITE"
printf '%s\n' 'SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE' > "${EVIDENCE_DIR}/phase-a-status.txt"
