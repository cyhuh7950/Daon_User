#!/usr/bin/env bash
set -euo pipefail

: "${SIMULATOR_UDID:?SIMULATOR_UDID is required}"
IOS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPOSITORY_ROOT="$(cd "${IOS_ROOT}/../../.." && pwd)"
DERIVED_DATA="${IOS_DERIVED_DATA:-${REPOSITORY_ROOT}/artifacts/ios-phase-a/DerivedData}"

cd "${REPOSITORY_ROOT}"
xcodebuild clean build \
  -workspace "apps/mobile/ios/Daon.xcworkspace" \
  -scheme Daon \
  -configuration Release \
  -sdk iphonesimulator \
  -destination "platform=iOS Simulator,id=${SIMULATOR_UDID}" \
  -derivedDataPath "${DERIVED_DATA}" \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO
