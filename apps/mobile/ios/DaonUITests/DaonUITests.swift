import XCTest

final class DaonUITests: XCTestCase {
  private func openRoute(_ route: String, in app: XCUIApplication) {
    let button = app.buttons["\(route) 화면 열기"]
    let navigation = app.scrollViews["공용 Navigation"]
    for _ in 0..<8 where !button.isHittable { navigation.swipeLeft() }
    XCTAssertTrue(button.waitForExistence(timeout: 10), "missing route button: \(route)")
    XCTAssertTrue(button.isHittable, "route button is not hittable: \(route)")
    button.tap()
    XCTAssertTrue(app.staticTexts[route].waitForExistence(timeout: 5), "route title not visible: \(route)")
  }

  func testApprovedNavigationRoutesAreClickable() {
    let app = XCUIApplication()
    app.launch()
    for route in ["Home", "WorkspaceList", "WorkspaceDetail", "Inbox", "RunHistory", "Notifications", "ModelConnections", "AccountSettings"] {
      openRoute(route, in: app)
    }
  }

  func testForegroundBackgroundAndRelaunchPreserveApprovedRoute() {
    let app = XCUIApplication()
    app.launch()
    openRoute("Notifications", in: app)
    XCUIDevice.shared.press(.home)
    app.activate()
    XCTAssertTrue(app.staticTexts["Notifications"].waitForExistence(timeout: 5))
    app.terminate()
    app.launch()
    XCTAssertTrue(app.staticTexts["Notifications"].waitForExistence(timeout: 10))
  }

  func testPermissionControlsAndSettingsBoundary() {
    let app = XCUIApplication()
    app.launch()
    for kind in ["camera", "microphone", "notification"] {
      XCTAssertTrue(app.buttons["\(kind) 권한 요청"].waitForExistence(timeout: 10))
    }
    let settingsButton = app.buttons["앱 권한 설정 열기"]
    let content = app.scrollViews["화면 내용"]
    for _ in 0..<8 where !settingsButton.isHittable { content.swipeUp() }
    XCTAssertTrue(settingsButton.waitForExistence(timeout: 10))
    XCTAssertTrue(settingsButton.isHittable)
    settingsButton.tap()
    let settings = XCUIApplication(bundleIdentifier: "com.apple.Preferences")
    XCTAssertTrue(settings.wait(for: .runningForeground, timeout: 10))
  }

  func testPermissionRequestReflectsOSDecision() {
    guard let expected = ProcessInfo.processInfo.environment["DAON_PERMISSION_EXPECTED"] else {
      XCTFail("DAON_PERMISSION_EXPECTED is required by the deterministic permission phase")
      return
    }
    XCTAssertTrue(["GRANTED", "DENIED"].contains(expected), "DAON_PERMISSION_EXPECTED must be GRANTED or DENIED")
    let app = XCUIApplication()
    app.launch()
    let content = app.scrollViews["화면 내용"]
    XCTAssertTrue(content.waitForExistence(timeout: 10), "screen content is unavailable")
    for kind in ["camera", "microphone", "notification"] {
      let button = app.buttons["\(kind) 권한 요청"]
      for _ in 0..<8 where !button.isHittable { content.swipeUp() }
      XCTAssertTrue(button.waitForExistence(timeout: 10), "missing Production permission button: \(kind)")
      XCTAssertTrue(button.isHittable, "Production permission button is not hittable: \(kind)")
      button.tap()
      let result = app.staticTexts["\(kind) 권한 결과 \(expected)"]
      XCTAssertTrue(result.waitForExistence(timeout: 10), "Production requestPermission result missing: \(kind):\(expected)")
    }
    app.terminate()
  }
}
