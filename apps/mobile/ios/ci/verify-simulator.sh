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

is_allowed_permission_failure_code() {
  case "$1" in
    ALERT_TITLE_MISSING|ALERT_COUNT_MISMATCH|ALERT_ALLOW_MISSING|ALERT_DISMISSAL_FAILED|SETTINGS_FOREGROUND_FAILED|SETTINGS_NOTIFICATION_SEMANTIC_ROW_ZERO|SETTINGS_NOTIFICATION_SEMANTIC_ROW_AMBIGUOUS|SETTINGS_NOTIFICATION_ROW_ZERO|SETTINGS_NOTIFICATION_ROW_AMBIGUOUS|SETTINGS_NOTIFICATION_ROW_MISSING|SETTINGS_SWITCH_MISSING|SETTINGS_SWITCH_VALUE_FAILED|APP_RETURN_ROOT_FAILED|PRODUCTION_RESULT_MISSING|STAGE_PHASE_EXPECTED_BINDING|STAGE_PHASE_EXPECTED_MATCHED|STAGE_APP_LAUNCH_ROOT|STAGE_CAMERA_REQUEST|STAGE_CAMERA_RESULT|STAGE_MICROPHONE_REQUEST|STAGE_MICROPHONE_RESULT|STAGE_NOTIFICATION_REQUEST|STAGE_ALERT_TITLE|STAGE_ALERT_COUNT|STAGE_ALERT_ALLOW|STAGE_ALERT_DISMISSAL|STAGE_SETTINGS_FOREGROUND|STAGE_SETTINGS_NOTIFICATION_ROW|STAGE_SETTINGS_NOTIFICATION_QUERY_CREATED|STAGE_SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED|STAGE_SETTINGS_NOTIFICATION_COUNT_SINGLE|STAGE_SETTINGS_NOTIFICATION_ELEMENT_READY|STAGE_SETTINGS_NOTIFICATION_ROW_TAP_PENDING|STAGE_SETTINGS_SWITCH_READ|STAGE_SETTINGS_SWITCH_TOGGLE|STAGE_SETTINGS_SWITCH_VERIFY|STAGE_APP_RETURN_ROOT|STAGE_NOTIFICATION_RESULT|UNKNOWN_XCTEST_FAILURE) return 0 ;;
    *) return 1 ;;
  esac
}

permission_stage_failure_code_from_log() {
  local log_file="$1"
  local marker=""
  marker="$(grep -Eo 'DAON_PERMISSION_XCTEST_STAGE=(PHASE_EXPECTED_BINDING|PHASE_EXPECTED_MATCHED|APP_LAUNCH_ROOT|CAMERA_REQUEST|CAMERA_RESULT|MICROPHONE_REQUEST|MICROPHONE_RESULT|NOTIFICATION_REQUEST|ALERT_TITLE|ALERT_COUNT|ALERT_ALLOW|ALERT_DISMISSAL|SETTINGS_FOREGROUND|SETTINGS_NOTIFICATION_ROW|SETTINGS_NOTIFICATION_QUERY_CREATED|SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED|SETTINGS_NOTIFICATION_COUNT_SINGLE|SETTINGS_NOTIFICATION_ELEMENT_READY|SETTINGS_NOTIFICATION_ROW_TAP_PENDING|SETTINGS_SWITCH_READ|SETTINGS_SWITCH_TOGGLE|SETTINGS_SWITCH_VERIFY|APP_RETURN_ROOT|NOTIFICATION_RESULT)$' "${log_file}" | tail -n 1 || true)"
  if [[ -n "${marker}" ]]; then
    printf 'STAGE_%s' "${marker#DAON_PERMISSION_XCTEST_STAGE=}"
  else
    printf 'UNKNOWN_XCTEST_FAILURE'
  fi
}

permission_failure_code_from_log() {
  local log_file="$1"
  local code="UNKNOWN_XCTEST_FAILURE"
  if grep -Fq 'missing or ambiguous exact system element: Daon Notification alert title' "${log_file}"; then
    code="ALERT_TITLE_MISSING"
  elif grep -Fq 'expected one Notification system alert' "${log_file}"; then
    code="ALERT_COUNT_MISMATCH"
  elif grep -Fq 'missing or ambiguous exact system element: Notification Allow button' "${log_file}" || grep -Fq 'Notification Allow button is not hittable' "${log_file}"; then
    code="ALERT_ALLOW_MISSING"
  elif grep -Fq 'Notification alert did not close after Allow' "${log_file}"; then
    code="ALERT_DISMISSAL_FAILED"
  elif grep -Fq 'Settings app is not runningForeground' "${log_file}"; then
    code="SETTINGS_FOREGROUND_FAILED"
  elif grep -Fq 'missing or ambiguous exact system element: Notification settings row [SEMANTIC_ZERO]' "${log_file}"; then
    code="SETTINGS_NOTIFICATION_SEMANTIC_ROW_ZERO"
  elif grep -Fq 'missing or ambiguous exact system element: Notification settings row [SEMANTIC_AMBIGUOUS]' "${log_file}"; then
    code="SETTINGS_NOTIFICATION_SEMANTIC_ROW_AMBIGUOUS"
  elif grep -Fq 'missing or ambiguous exact system element: Notification settings row [ZERO]' "${log_file}"; then
    code="SETTINGS_NOTIFICATION_ROW_ZERO"
  elif grep -Fq 'missing or ambiguous exact system element: Notification settings row [AMBIGUOUS]' "${log_file}"; then
    code="SETTINGS_NOTIFICATION_ROW_AMBIGUOUS"
  elif grep -Fq 'missing or ambiguous exact system element: Notification settings row' "${log_file}" || grep -Fq 'Notification settings row is not hittable' "${log_file}"; then
    code="SETTINGS_NOTIFICATION_ROW_MISSING"
  elif grep -Fq 'missing or ambiguous exact system element: Allow Notifications switch' "${log_file}" || grep -Fq 'Allow Notifications switch is not hittable' "${log_file}"; then
    code="SETTINGS_SWITCH_MISSING"
  elif grep -Fq 'Allow Notifications switch has no supported value' "${log_file}" || grep -Fq 'Allow Notifications switch returned an unsupported value' "${log_file}" || grep -Fq 'Allow Notifications switch value did not update' "${log_file}" || grep -Fq 'Allow Notifications switch did not reach the required phase state' "${log_file}"; then
    code="SETTINGS_SWITCH_VALUE_FAILED"
  elif grep -Fq 'Daon process is not runningForeground' "${log_file}" || grep -Fq 'Daon iOS root shell is unavailable' "${log_file}" || grep -Fq 'Daon process left runningForeground before scenario interaction' "${log_file}"; then
    code="APP_RETURN_ROOT_FAILED"
  elif grep -Fq 'Production requestPermission result missing:' "${log_file}"; then
    code="PRODUCTION_RESULT_MISSING"
  fi
  if [[ "${code}" == "UNKNOWN_XCTEST_FAILURE" ]]; then
    code="$(permission_stage_failure_code_from_log "${log_file}")"
  fi
  printf '%s' "${code}"
}

report_permission_xctest_failure() {
  local log_file="$1"
  local phase="$2"
  local original_exit="$3"
  local code
  case "${phase}" in
    grant-initial|revoke|grant-again) ;;
    *) return 64 ;;
  esac
  code="$(permission_failure_code_from_log "${log_file}")"
  if ! is_allowed_permission_failure_code "${code}"; then
    code="UNKNOWN_XCTEST_FAILURE"
  fi
  printf '::error::CODE=%s PHASE=%s EXIT=%d\n' "${code}" "${phase}" "${original_exit}"
}

run_permission_phase() {
  local phase="$1"
  local privacy_action="$2"
  local permission_test_method
  local permission_log="${EVIDENCE_DIR}/permission-${phase}.log"
  local -a permission_pipeline_status
  local xcode_exit
  local tee_exit
  case "${phase}" in
    grant-initial) permission_test_method="testPermissionGrantInitial" ;;
    revoke) permission_test_method="testPermissionRevoke" ;;
    grant-again) permission_test_method="testPermissionGrantAgain" ;;
    *) return 64 ;;
  esac
  xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}" >/dev/null 2>&1 || true
  DAON_SIM_PERMISSION_SERVICE="camera"
  xcrun simctl privacy "${SIMULATOR_UDID}" "${privacy_action}" camera "${BUNDLE_ID}"
  DAON_SIM_PERMISSION_SERVICE="microphone"
  xcrun simctl privacy "${SIMULATOR_UDID}" "${privacy_action}" microphone "${BUNDLE_ID}"
  DAON_SIM_PERMISSION_SERVICE=""
  xcodebuild test-without-building \
    -workspace "${REPOSITORY_ROOT}/apps/mobile/ios/Daon.xcworkspace" \
    -scheme Daon \
    -configuration Release \
    -sdk iphonesimulator \
    -destination "platform=iOS Simulator,id=${SIMULATOR_UDID}" \
    -derivedDataPath "${DERIVED_DATA}" \
    -only-testing:DaonUITests/DaonUITests/${permission_test_method} \
    -resultBundlePath "${EVIDENCE_DIR}/permission-${phase}.xcresult" \
    CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO 2>&1 | tee "${permission_log}" \
    && permission_pipeline_status=("${PIPESTATUS[@]}") \
    || permission_pipeline_status=("${PIPESTATUS[@]}")
  xcode_exit="${permission_pipeline_status[0]}"
  tee_exit="${permission_pipeline_status[1]}"
  if [[ "${xcode_exit}" -ne 0 ]]; then
    report_permission_xctest_failure "${permission_log}" "${phase}" "${xcode_exit}" || true
    return "${xcode_exit}"
  fi
  if [[ "${tee_exit}" -ne 0 ]]; then
    return "${tee_exit}"
  fi
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
run_permission_phase grant-initial grant
DAON_SIM_STAGE="PERMISSION_REVOKE"
run_permission_phase revoke revoke
DAON_SIM_STAGE="PERMISSION_GRANT_AGAIN"
run_permission_phase grant-again grant

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
