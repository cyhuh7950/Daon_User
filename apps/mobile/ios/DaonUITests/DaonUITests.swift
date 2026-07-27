import XCTest

final class DaonUITests: XCTestCase {
  override func setUp() {
    super.setUp()
    continueAfterFailure = false
  }

  private func requireRootReady(_ app: XCUIApplication) {
    XCTAssertTrue(app.wait(for: .runningForeground, timeout: 10), "Daon process is not runningForeground")
    let root = app.otherElements["Daon ios 공용 Shell"]
    XCTAssertTrue(root.waitForExistence(timeout: 10), "Daon iOS root shell is unavailable")
    XCTAssertEqual(app.state, .runningForeground, "Daon process left runningForeground before scenario interaction")
  }

  private func launchAndRequireRootReady(_ app: XCUIApplication) {
    app.launch()
    requireRootReady(app)
  }

  private func openRoute(_ route: String, in app: XCUIApplication) {
    let button = app.buttons["\(route) 화면 열기"]
    let navigation = app.otherElements["공용 Navigation"]
    XCTAssertTrue(navigation.waitForExistence(timeout: 10), "shared navigation is unavailable")
    for _ in 0..<8 where !button.isHittable { navigation.swipeLeft() }
    XCTAssertTrue(button.waitForExistence(timeout: 10), "missing route button: \(route)")
    XCTAssertTrue(button.isHittable, "route button is not hittable: \(route)")
    button.tap()
    XCTAssertTrue(app.staticTexts[route].waitForExistence(timeout: 5), "route title not visible: \(route)")
  }

  func testApprovedNavigationRoutesAreClickable() {
    let app = XCUIApplication()
    launchAndRequireRootReady(app)
    for route in ["Home", "WorkspaceList", "WorkspaceDetail", "Inbox", "RunHistory", "Notifications", "ModelConnections", "AccountSettings"] {
      openRoute(route, in: app)
    }
  }

  func testForegroundBackgroundAndRelaunchPreserveApprovedRoute() {
    let app = XCUIApplication()
    launchAndRequireRootReady(app)
    openRoute("Notifications", in: app)
    XCUIDevice.shared.press(.home)
    app.activate()
    requireRootReady(app)
    XCTAssertTrue(app.staticTexts["Notifications"].waitForExistence(timeout: 5))
    app.terminate()
    launchAndRequireRootReady(app)
    XCTAssertTrue(app.staticTexts["Notifications"].waitForExistence(timeout: 10))
  }

  func testSystemOpenDeepLinksPreserveForegroundAndRoute() {
    let app = XCUIApplication()
    launchAndRequireRootReady(app)
    guard #available(iOS 26.0, *) else {
      XCTFail("XCUIDevice.shared.system.open requires iOS 26.0 or later")
      return
    }

    let warmLinks = [
      ("WorkspaceList", "sinsan-daon://app/WorkspaceList"),
      ("WorkspaceDetail", "sinsan-daon://app/WorkspaceDetail"),
      ("Inbox", "sinsan-daon://app/Inbox"),
      ("RunHistory", "sinsan-daon://app/RunHistory"),
      ("Notifications", "sinsan-daon://app/Notifications"),
      ("ModelConnections", "sinsan-daon://app/ModelConnections"),
      ("AccountSettings", "sinsan-daon://app/AccountSettings")
    ]
    for (route, rawURL) in warmLinks {
      guard let url = URL(string: rawURL) else {
        XCTFail("invalid approved deep link fixture: \(rawURL)")
        return
      }
      XCUIDevice.shared.system.open(url)
      requireRootReady(app)
      XCTAssertTrue(app.staticTexts[route].waitForExistence(timeout: 10), "route title not visible after system open: \(route)")
    }

    let rejectedLinks = [
      "sinsan-daon://app/UnknownRoute",
      "sinsan-daon://app/%48ome",
      "sinsan-daon://app/Home%2Fextra",
      "sinsan-daon://app/Home?route=Inbox",
      "sinsan-daon://app/Home#Inbox"
    ]
    for rawURL in rejectedLinks {
      guard let url = URL(string: rawURL) else {
        XCTFail("invalid rejected deep link fixture: \(rawURL)")
        return
      }
      XCUIDevice.shared.system.open(url)
      requireRootReady(app)
      XCTAssertTrue(app.staticTexts["AccountSettings"].waitForExistence(timeout: 10), "rejected deep link changed the approved route: \(rawURL)")
    }
  }

  func testPermissionControlsAndSettingsBoundary() {
    let app = XCUIApplication()
    launchAndRequireRootReady(app)
    for kind in ["camera", "microphone", "notification"] {
      XCTAssertTrue(app.buttons["\(kind) 권한 요청"].waitForExistence(timeout: 10))
    }
    let settingsButton = app.buttons["앱 권한 설정 열기"]
    let content = app.otherElements["화면 내용"]
    XCTAssertTrue(content.waitForExistence(timeout: 10), "screen content is unavailable")
    for _ in 0..<8 where !settingsButton.isHittable { content.swipeUp() }
    XCTAssertTrue(settingsButton.waitForExistence(timeout: 10))
    XCTAssertTrue(settingsButton.isHittable)
    settingsButton.tap()
    let settings = XCUIApplication(bundleIdentifier: "com.apple.Preferences")
    XCTAssertTrue(settings.wait(for: .runningForeground, timeout: 10))
  }

  func testPermissionRequestReflectsOSDecision() throws {
    permissionXCTestStage(.phaseExpectedBinding)
    guard let rawPhase = ProcessInfo.processInfo.environment["DAON_PERMISSION_PHASE"] else {
      XCTFail("DAON_PERMISSION_PHASE is required by the deterministic permission phase")
      return
    }
    permissionXCTestStage(.phaseEnvironmentPresent)
    guard let phase = PermissionPhase(rawValue: rawPhase) else {
      XCTFail("DAON_PERMISSION_PHASE must be grant-initial, revoke or grant-again")
      return
    }
    permissionXCTestStage(.phaseAllowed)
    guard let expected = ProcessInfo.processInfo.environment["DAON_PERMISSION_EXPECTED"] else {
      XCTFail("DAON_PERMISSION_EXPECTED is required by the deterministic permission phase")
      return
    }
    permissionXCTestStage(.expectedEnvironmentPresent)
    let expectedIsAllowed = ["GRANTED", "DENIED"].contains(expected)
    XCTAssertTrue(expectedIsAllowed, "DAON_PERMISSION_EXPECTED must be GRANTED or DENIED")
    guard expectedIsAllowed else { return }
    permissionXCTestStage(.expectedAllowed)
    let phaseExpected = phase == .revoke ? "DENIED" : "GRANTED"
    XCTAssertEqual(expected, phaseExpected, "permission phase and expected result must remain coupled")
    guard expected == phaseExpected else { return }
    permissionXCTestStage(.phaseExpectedMatched)
    let app = XCUIApplication()
    permissionXCTestStage(.appLaunchRoot)
    launchAndRequireRootReady(app)
    let content = app.otherElements["화면 내용"]
    XCTAssertTrue(content.waitForExistence(timeout: 10), "screen content is unavailable")

    if phase == .revoke {
      try setNotificationAuthorization(enabled: false, in: app, content: content)
    } else if phase == .grantAgain {
      try setNotificationAuthorization(enabled: true, in: app, content: content)
    }

    for kind in ["camera", "microphone", "notification"] {
      switch kind {
      case "camera": permissionXCTestStage(.cameraRequest)
      case "microphone": permissionXCTestStage(.microphoneRequest)
      default: permissionXCTestStage(.notificationRequest)
      }
      let button = app.buttons["\(kind) 권한 요청"]
      for _ in 0..<8 where !button.isHittable { content.swipeUp() }
      XCTAssertTrue(button.waitForExistence(timeout: 10), "missing Production permission button: \(kind)")
      XCTAssertTrue(button.isHittable, "Production permission button is not hittable: \(kind)")
      button.tap()
      if kind == "notification" && phase == .grantInitial {
        try approveExpectedNotificationAlert()
      }
      let result = app.staticTexts["\(kind) 권한 결과 \(expected)"]
      switch kind {
      case "camera": permissionXCTestStage(.cameraResult)
      case "microphone": permissionXCTestStage(.microphoneResult)
      default: permissionXCTestStage(.notificationResult)
      }
      XCTAssertTrue(result.waitForExistence(timeout: 10), "Production requestPermission result missing: \(kind):\(expected)")
    }
    app.terminate()
  }

  private enum PermissionPhase: String {
    case grantInitial = "grant-initial"
    case revoke
    case grantAgain = "grant-again"
  }

  private enum PermissionXCTestStage: String {
    case phaseExpectedBinding = "PHASE_EXPECTED_BINDING"
    case phaseEnvironmentPresent = "PHASE_ENV_PRESENT"
    case phaseAllowed = "PHASE_ALLOWED"
    case expectedEnvironmentPresent = "EXPECTED_ENV_PRESENT"
    case expectedAllowed = "EXPECTED_ALLOWED"
    case phaseExpectedMatched = "PHASE_EXPECTED_MATCHED"
    case appLaunchRoot = "APP_LAUNCH_ROOT"
    case cameraRequest = "CAMERA_REQUEST"
    case cameraResult = "CAMERA_RESULT"
    case microphoneRequest = "MICROPHONE_REQUEST"
    case microphoneResult = "MICROPHONE_RESULT"
    case notificationRequest = "NOTIFICATION_REQUEST"
    case alertTitle = "ALERT_TITLE"
    case alertCount = "ALERT_COUNT"
    case alertAllow = "ALERT_ALLOW"
    case alertDismissal = "ALERT_DISMISSAL"
    case settingsForeground = "SETTINGS_FOREGROUND"
    case settingsNotificationRow = "SETTINGS_NOTIFICATION_ROW"
    case settingsSwitchRead = "SETTINGS_SWITCH_READ"
    case settingsSwitchToggle = "SETTINGS_SWITCH_TOGGLE"
    case settingsSwitchVerify = "SETTINGS_SWITCH_VERIFY"
    case appReturnRoot = "APP_RETURN_ROOT"
    case notificationResult = "NOTIFICATION_RESULT"
  }

  private func permissionXCTestStage(_ stage: PermissionXCTestStage) {
    print("DAON_PERMISSION_XCTEST_STAGE=\(stage.rawValue)")
  }

  private enum PermissionUIContractError: Error {
    case missingExactElement(String)
    case unsupportedSwitchValue(String)
  }

  private func requireExactElement(_ candidates: [XCUIElement], description: String) throws -> XCUIElement {
    var matches: [XCUIElement] = []
    for candidate in candidates {
      if candidate.waitForExistence(timeout: matches.isEmpty ? 10 : 0.25) {
        matches.append(candidate)
      }
    }
    guard matches.count == 1, let match = matches.popLast() else {
      XCTFail("missing or ambiguous exact system element: \(description)")
      throw PermissionUIContractError.missingExactElement(description)
    }
    return match
  }

  private func approveExpectedNotificationAlert() throws {
    let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
    permissionXCTestStage(.alertTitle)
    _ = try requireExactElement([
      springboard.alerts.staticTexts["“Daon” Would Like to Send You Notifications"],
      springboard.alerts.staticTexts["\"Daon\" Would Like to Send You Notifications"],
      springboard.alerts.staticTexts["“Daon”이(가) 알림을 보내고자 합니다"]
    ], description: "Daon Notification alert title")
    permissionXCTestStage(.alertCount)
    XCTAssertEqual(springboard.alerts.count, 1, "expected one Notification system alert")
    permissionXCTestStage(.alertAllow)
    let allowButton = try requireExactElement([
      springboard.alerts.buttons["Allow"],
      springboard.alerts.buttons["허용"]
    ], description: "Notification Allow button")
    XCTAssertTrue(allowButton.isHittable, "Notification Allow button is not hittable")
    allowButton.tap()
    permissionXCTestStage(.alertDismissal)
    let dismissed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: allowButton)
    XCTAssertEqual(XCTWaiter.wait(for: [dismissed], timeout: 5), .completed, "Notification alert did not close after Allow")
  }

  private func setNotificationAuthorization(enabled target: Bool, in app: XCUIApplication, content: XCUIElement) throws {
    let settingsButton = app.buttons["앱 권한 설정 열기"]
    for _ in 0..<8 where !settingsButton.isHittable { content.swipeUp() }
    XCTAssertTrue(settingsButton.waitForExistence(timeout: 10), "Production application settings button is unavailable")
    XCTAssertTrue(settingsButton.isHittable, "Production application settings button is not hittable")
    settingsButton.tap()

    let settings = XCUIApplication(bundleIdentifier: "com.apple.Preferences")
    permissionXCTestStage(.settingsForeground)
    XCTAssertTrue(settings.wait(for: .runningForeground, timeout: 10), "Settings app is not runningForeground")
    permissionXCTestStage(.settingsNotificationRow)
    let notificationsRow = try requireExactElement([
      settings.cells["Notifications"],
      settings.cells["알림"]
    ], description: "Notification settings row")
    XCTAssertTrue(notificationsRow.isHittable, "Notification settings row is not hittable")
    notificationsRow.tap()

    permissionXCTestStage(.settingsSwitchRead)
    let allowNotifications = try requireExactElement([
      settings.switches["Allow Notifications"],
      settings.switches["알림 허용"]
    ], description: "Allow Notifications switch")
    XCTAssertTrue(allowNotifications.isHittable, "Allow Notifications switch is not hittable")
    let before = try notificationSwitchIsEnabled(allowNotifications)
    if before != target {
      permissionXCTestStage(.settingsSwitchToggle)
      allowNotifications.tap()
    }
    permissionXCTestStage(.settingsSwitchVerify)
    waitForNotificationSwitch(allowNotifications, enabled: target)
    XCTAssertEqual(try notificationSwitchIsEnabled(allowNotifications), target, "Allow Notifications switch did not reach the required phase state")

    permissionXCTestStage(.appReturnRoot)
    app.activate()
    requireRootReady(app)
  }

  private func notificationSwitchIsEnabled(_ element: XCUIElement) throws -> Bool {
    if let number = element.value as? NSNumber {
      return number.boolValue
    }
    guard let value = element.value as? String else {
      XCTFail("Allow Notifications switch has no supported value")
      throw PermissionUIContractError.unsupportedSwitchValue("missing")
    }
    switch value {
    case "1", "On", "켜짐": return true
    case "0", "Off", "꺼짐": return false
    default:
      XCTFail("Allow Notifications switch returned an unsupported value")
      throw PermissionUIContractError.unsupportedSwitchValue(value)
    }
  }

  private func waitForNotificationSwitch(_ element: XCUIElement, enabled: Bool) {
    let acceptedValues = enabled ? ["1", "On", "켜짐"] : ["0", "Off", "꺼짐"]
    let predicate = NSPredicate { object, _ in
      guard let candidate = object as? XCUIElement else { return false }
      if let number = candidate.value as? NSNumber { return number.boolValue == enabled }
      guard let value = candidate.value as? String else { return false }
      return acceptedValues.contains(value)
    }
    let expectation = XCTNSPredicateExpectation(predicate: predicate, object: element)
    XCTAssertEqual(XCTWaiter.wait(for: [expectation], timeout: 5), .completed, "Allow Notifications switch value did not update")
  }
}
