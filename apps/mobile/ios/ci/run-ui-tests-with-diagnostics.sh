#!/usr/bin/env bash
set -uo pipefail

: "${SIMULATOR_UDID:?SIMULATOR_UDID is required}"
REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
EVIDENCE_DIR="${IOS_EVIDENCE_DIR:-${REPOSITORY_ROOT}/artifacts/ios-phase-a/evidence}"
DERIVED_DATA="${IOS_DERIVED_DATA:-${REPOSITORY_ROOT}/artifacts/ios-phase-a/DerivedData}"
RESULT_BUNDLE="${IOS_RESULT_BUNDLE:-${EVIDENCE_DIR}/DaonUITests.xcresult}"
DIAGNOSTICS_DIR="${EVIDENCE_DIR}/diagnostics"
DIAGNOSTIC_REPORTS_DIR="${IOS_DIAGNOSTIC_REPORTS_DIR:-${HOME}/Library/Logs/DiagnosticReports}"
STATUS_FILE="${DIAGNOSTICS_DIR}/diagnostic-status.txt"
START_MARKER="${DIAGNOSTICS_DIR}/ui-test-start.marker"
CRASH_REPORT_INDEX="${DIAGNOSTICS_DIR}/crash-report-files.txt"
CRASH_REPORT_PATHS="${RUNNER_TEMP:-${DIAGNOSTICS_DIR}}/daon-crash-report-paths-$$.bin"
mkdir -p "${DIAGNOSTICS_DIR}"
: > "${STATUS_FILE}"
: > "${CRASH_REPORT_INDEX}"
touch "${START_MARKER}"
trap 'rm -f "${CRASH_REPORT_PATHS}"' EXIT

record_status() {
  printf '%s=%s\n' "$1" "$2" >> "${STATUS_FILE}"
}

collect_to_file() {
  local name="$1"
  local output="$2"
  shift 2
  "$@" > "${output}" 2> "${output}.stderr"
  local status=$?
  if [[ ${status} -eq 0 ]]; then
    record_status "${name}" success
  else
    record_status "${name}" "failure:${status}"
  fi
  return 0
}

TEST_START_UTC="$(date -u '+%Y-%m-%d %H:%M:%S%z')"
xcodebuild test \
  -workspace "${REPOSITORY_ROOT}/apps/mobile/ios/Daon.xcworkspace" \
  -scheme Daon \
  -configuration Release \
  -sdk iphonesimulator \
  -destination "platform=iOS Simulator,id=${SIMULATOR_UDID}" \
  -derivedDataPath "${DERIVED_DATA}" \
  -only-testing:DaonUITests/DaonUITests/testApprovedNavigationRoutesAreClickable \
  -only-testing:DaonUITests/DaonUITests/testForegroundBackgroundAndRelaunchPreserveApprovedRoute \
  -only-testing:DaonUITests/DaonUITests/testSystemOpenDeepLinksPreserveForegroundAndRoute \
  -only-testing:DaonUITests/DaonUITests/testPermissionControlsAndSettingsBoundary \
  -resultBundlePath "${RESULT_BUNDLE}" \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO
TEST_EXIT_CODE=$?
TEST_END_UTC="$(date -u '+%Y-%m-%d %H:%M:%S%z')"
record_status test_exit_code "${TEST_EXIT_CODE}"
record_status test_start_utc "${TEST_START_UTC}"
record_status test_end_utc "${TEST_END_UTC}"

if [[ -d "${RESULT_BUNDLE}" ]]; then
  collect_to_file xcresult_summary "${DIAGNOSTICS_DIR}/xcresult-summary.json" \
    xcrun xcresulttool get test-results summary --path "${RESULT_BUNDLE}"
  collect_to_file xcresult_tests "${DIAGNOSTICS_DIR}/xcresult-tests.json" \
    xcrun xcresulttool get test-results tests --path "${RESULT_BUNDLE}"
  mkdir -p "${DIAGNOSTICS_DIR}/xcresult-attachments"
  xcrun xcresulttool export attachments --path "${RESULT_BUNDLE}" --output-path "${DIAGNOSTICS_DIR}/xcresult-attachments" \
    > "${DIAGNOSTICS_DIR}/xcresult-attachments.stdout" 2> "${DIAGNOSTICS_DIR}/xcresult-attachments.stderr"
  ATTACHMENT_STATUS=$?
  if [[ ${ATTACHMENT_STATUS} -eq 0 ]]; then
    record_status xcresult_attachments success
  else
    record_status xcresult_attachments "failure:${ATTACHMENT_STATUS}"
  fi
else
  record_status xcresult_summary result_bundle_missing
  record_status xcresult_tests result_bundle_missing
  record_status xcresult_attachments result_bundle_missing
fi

collect_to_file simulator_unified_log "${DIAGNOSTICS_DIR}/daon-unified.log" \
  xcrun simctl spawn "${SIMULATOR_UDID}" log show --style compact \
  --start "${TEST_START_UTC}" --end "${TEST_END_UTC}" \
  --predicate 'process == "Daon" OR processImagePath ENDSWITH "/Daon"'

if [[ -d "${DIAGNOSTIC_REPORTS_DIR}" ]]; then
  find "${DIAGNOSTIC_REPORTS_DIR}" -type f -newer "${START_MARKER}" \
    \( -name 'Daon*.ips' -o -name 'Daon*.crash' -o -name 'Daon*.diag' \) -print0 \
    > "${CRASH_REPORT_PATHS}" 2> "${DIAGNOSTICS_DIR}/crash-report-find.stderr"
  CRASH_FIND_STATUS=$?
  if [[ ${CRASH_FIND_STATUS} -eq 0 ]]; then
    record_status crash_report_find success
    while IFS= read -r -d '' report; do
      report_name="$(basename "${report}")"
      printf '%s\n' "${report_name}" >> "${CRASH_REPORT_INDEX}"
      cp "${report}" "${DIAGNOSTICS_DIR}/${report_name}"
      COPY_STATUS=$?
      if [[ ${COPY_STATUS} -eq 0 ]]; then
        record_status "crash_report_copy_${report_name}" success
      else
        record_status "crash_report_copy_${report_name}" "failure:${COPY_STATUS}"
      fi
    done < "${CRASH_REPORT_PATHS}"
  else
    record_status crash_report_find "failure:${CRASH_FIND_STATUS}"
  fi
else
  record_status crash_report_find directory_missing
fi

exit "${TEST_EXIT_CODE}"
