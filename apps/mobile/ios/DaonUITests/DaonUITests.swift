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

  func testPermissionGrantInitial() throws {
    try runPermissionPhase(.grantInitial, expected: "GRANTED")
  }

  func testPermissionRevoke() throws {
    try runPermissionPhase(.revoke, expected: "DENIED")
  }

  func testPermissionGrantAgain() throws {
    try runPermissionPhase(.grantAgain, expected: "GRANTED")
  }

  private func runPermissionPhase(_ phase: PermissionPhase, expected: String) throws {
    permissionXCTestStage(.phaseExpectedBinding)
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
    case settingsNotificationQueryCreated = "SETTINGS_NOTIFICATION_QUERY_CREATED"
    case settingsNotificationQueryWaitCompleted = "SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED"
    case settingsNotificationScrollSearch = "SETTINGS_NOTIFICATION_SCROLL_SEARCH"
    case settingsNotificationCountSingle = "SETTINGS_NOTIFICATION_COUNT_SINGLE"
    case settingsNotificationElementReady = "SETTINGS_NOTIFICATION_ELEMENT_READY"
    case settingsNotificationRowTapPending = "SETTINGS_NOTIFICATION_ROW_TAP_PENDING"
    case settingsSearchButton = "SETTINGS_SEARCH_BUTTON"
    case settingsSearchField = "SETTINGS_SEARCH_FIELD"
    case settingsSearchResult = "SETTINGS_SEARCH_RESULT"
    case settingsSearchAppSurface = "SETTINGS_SEARCH_APP_SURFACE"
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
    case notificationSettingsRowAbsent
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

  private func settingsDiagnosticToken(_ rawValue: String, maximumLength: Int) -> String {
    var token = ""
    for scalar in rawValue.unicodeScalars {
      let value = scalar.value
      let isASCIILetterOrDigit = (48...57).contains(value) || (65...90).contains(value) || (97...122).contains(value)
      let isSafePunctuation = [43, 45, 46, 47, 95, 123, 125].contains(value)
      let component: String
      if isASCIILetterOrDigit || isSafePunctuation {
        component = String(scalar)
      } else if value > 127 {
        component = "u{\(String(value, radix: 16, uppercase: true))}"
      } else {
        component = "_"
      }
      guard token.count + component.count <= maximumLength else { break }
      token.append(component)
    }
    return token.isEmpty ? "_empty_" : token
  }

  private func emitSettingsAccessibilitySummary(in settings: XCUIApplication) {
    let maximumElementCount = 16
    let maximumTokenLength = 80
    let maximumSummaryLength = 4096
    let elementGroups: [(type: String, elements: [XCUIElement])] = [
      ("cell", settings.cells.allElementsBoundByAccessibilityElement),
      ("button", settings.buttons.allElementsBoundByAccessibilityElement),
      ("staticText", settings.staticTexts.allElementsBoundByAccessibilityElement),
      ("switch", settings.switches.allElementsBoundByAccessibilityElement)
    ]
    var items: [String] = []
    elementLoop: for group in elementGroups {
      for element in group.elements {
        guard items.count < maximumElementCount else { break elementLoop }
        let label = settingsDiagnosticToken(element.label, maximumLength: maximumTokenLength)
        let identifier = settingsDiagnosticToken(element.identifier, maximumLength: maximumTokenLength)
        let hittable = element.isHittable ? "1" : "0"
        items.append("elementType=\(group.type),label=\(label),identifier=\(identifier),isHittable=\(hittable)")
      }
    }
    let itemPayload = items.isEmpty ? "_none_" : items.joined(separator: ";")
    let summary = "DAON_SETTINGS_ACCESSIBILITY_SUMMARY=v1|count=\(items.count)|items=\(itemPayload)"
    guard summary.count <= maximumSummaryLength else {
      print("DAON_SETTINGS_ACCESSIBILITY_SUMMARY=v1|count=0|items=_none_")
      return
    }
    print(summary)
  }

  private func emitSettingsSearchAccessibilitySummary(in settings: XCUIApplication) {
    let maximumElementCount = 16
    let maximumTokenLength = 48
    let maximumSummaryLength = 4096
    let elementGroups: [(type: String, elements: [XCUIElement])] = [
      ("searchField", settings.searchFields.allElementsBoundByAccessibilityElement),
      ("textField", settings.textFields.allElementsBoundByAccessibilityElement)
    ]
    var items: [String] = []
    elementLoop: for group in elementGroups {
      for element in group.elements {
        guard items.count < maximumElementCount else { break elementLoop }
        let rawValue: String
        if let stringValue = element.value as? String {
          rawValue = stringValue
        } else if let numberValue = element.value as? NSNumber {
          rawValue = numberValue.stringValue
        } else {
          rawValue = ""
        }
        let label = settingsDiagnosticToken(element.label, maximumLength: maximumTokenLength)
        let identifier = settingsDiagnosticToken(element.identifier, maximumLength: maximumTokenLength)
        let value = settingsDiagnosticToken(rawValue, maximumLength: maximumTokenLength)
        let hittable = element.isHittable ? "1" : "0"
        items.append("elementType=\(group.type),label=\(label),identifier=\(identifier),value=\(value),isHittable=\(hittable)")
      }
    }
    let itemPayload = items.isEmpty ? "_none_" : items.joined(separator: ";")
    let summary = "DAON_SETTINGS_SEARCH_ACCESSIBILITY_SUMMARY=v1|count=\(items.count)|items=\(itemPayload)"
    guard summary.count <= maximumSummaryLength else {
      print("DAON_SETTINGS_SEARCH_ACCESSIBILITY_SUMMARY=v1|count=0|items=_none_")
      return
    }
    print(summary)
  }
  private func emitSettingsSearchSurfaceSummary(in settings: XCUIApplication) {
    let maximumElementCount = 24
    let maximumTokenLength = 48
    let maximumSummaryLength = 4096
    let exactSearchButtons = settings.buttons.matching(identifier: "com.apple.settings.search").allElementsBoundByAccessibilityElement
    let remainingButtons = settings.buttons.allElementsBoundByAccessibilityElement.filter {
      $0.identifier != "com.apple.settings.search"
    }
    let elementGroups: [(type: String, elements: [XCUIElement])] = [
      ("textView", settings.textViews.allElementsBoundByAccessibilityElement),
      ("other", settings.otherElements.allElementsBoundByAccessibilityElement),
      ("button", exactSearchButtons + remainingButtons),
      ("staticText", settings.staticTexts.allElementsBoundByAccessibilityElement)
    ]
    var items: [String] = []
    elementLoop: for group in elementGroups {
      for element in group.elements {
        guard items.count < maximumElementCount else { break elementLoop }
        let rawLabel = element.label
        let rawIdentifier = element.identifier
        let rawValue: String
        if let stringValue = element.value as? String {
          rawValue = stringValue
        } else if let numberValue = element.value as? NSNumber {
          rawValue = numberValue.stringValue
        } else {
          rawValue = ""
        }
        guard !rawLabel.isEmpty || !rawIdentifier.isEmpty || !rawValue.isEmpty || element.isHittable else {
          continue
        }
        let label = settingsDiagnosticToken(rawLabel, maximumLength: maximumTokenLength)
        let identifier = settingsDiagnosticToken(rawIdentifier, maximumLength: maximumTokenLength)
        let value = settingsDiagnosticToken(rawValue, maximumLength: maximumTokenLength)
        let hittable = element.isHittable ? "1" : "0"
        items.append("elementType=\(group.type),label=\(label),identifier=\(identifier),value=\(value),isHittable=\(hittable)")
      }
    }
    let itemPayload = items.isEmpty ? "_none_" : items.joined(separator: ";")
    let summary = "DAON_SETTINGS_SEARCH_SURFACE_SUMMARY=v1|count=\(items.count)|items=\(itemPayload)"
    guard summary.count <= maximumSummaryLength else {
      print("DAON_SETTINGS_SEARCH_SURFACE_SUMMARY=v1|count=0|items=_none_")
      return
    }
    print(summary)
  }
  private func emitSettingsSearchResultSummary(in settings: XCUIApplication) {
    let maximumElementCount = 24
    let maximumTokenLength = 48
    let maximumSummaryLength = 4096
    let elementGroups: [(type: String, elements: [XCUIElement])] = [
      ("cell", settings.cells.allElementsBoundByAccessibilityElement),
      ("button", settings.buttons.allElementsBoundByAccessibilityElement),
      ("staticText", settings.staticTexts.allElementsBoundByAccessibilityElement),
      ("other", settings.otherElements.allElementsBoundByAccessibilityElement)
    ]
    var items: [String] = []
    elementLoop: for group in elementGroups {
      for element in group.elements {
        guard items.count < maximumElementCount else { break elementLoop }
        let rawLabel = element.label
        let rawIdentifier = element.identifier
        let rawValue: String
        if let stringValue = element.value as? String { rawValue = stringValue }
        else if let numberValue = element.value as? NSNumber { rawValue = numberValue.stringValue }
        else { rawValue = "" }
        guard !rawLabel.isEmpty || !rawIdentifier.isEmpty || !rawValue.isEmpty || element.isHittable else { continue }
        let label = settingsDiagnosticToken(rawLabel, maximumLength: maximumTokenLength)
        let identifier = settingsDiagnosticToken(rawIdentifier, maximumLength: maximumTokenLength)
        let value = settingsDiagnosticToken(rawValue, maximumLength: maximumTokenLength)
        let hittable = element.isHittable ? "1" : "0"
        items.append("elementType=\(group.type),label=\(label),identifier=\(identifier),value=\(value),isHittable=\(hittable)")
      }
    }
    let payload = items.isEmpty ? "_none_" : items.joined(separator: ";")
    let summary = "DAON_SETTINGS_SEARCH_RESULT_SUMMARY=v1|count=\(items.count)|items=\(payload)"
    guard summary.count <= maximumSummaryLength else { print("DAON_SETTINGS_SEARCH_RESULT_SUMMARY=v1|count=0|items=_none_"); return }
    print(summary)
  }
  private func requireExactNotificationSettingsRow(in settings: XCUIApplication, allowAbsent: Bool = false) throws -> XCUIElement {
    let exactLabelPredicate = NSPredicate(format: "label == %@ OR label == %@", "Notifications", "알림")
    let directQuery = settings.descendants(matching: .any).matching(exactLabelPredicate)
    let semanticCellQuery = settings.cells.containing(exactLabelPredicate)
    let compositeLabelPredicate = NSPredicate(format: "label == %@ OR label BEGINSWITH %@ OR label == %@ OR label BEGINSWITH %@", "Notifications", "Notifications,", "알림", "알림,")
    let compositeCellQuery = settings.cells.matching(compositeLabelPredicate)
    permissionXCTestStage(.settingsNotificationQueryCreated)
    let appeared = XCTNSPredicateExpectation(
      predicate: NSPredicate { object, _ in
        guard let directQuery = object as? XCUIElementQuery else { return false }
        let directElements = directQuery.allElementsBoundByAccessibilityElement.filter { $0.isHittable }
        if !directElements.isEmpty { return true }
        return !semanticCellQuery.allElementsBoundByAccessibilityElement.filter { $0.isHittable }.isEmpty
      },
      object: directQuery
    )
    _ = XCTWaiter.wait(for: [appeared], timeout: 10)
    permissionXCTestStage(.settingsNotificationQueryWaitCompleted)

    func collectNotificationCandidates() -> (exactLabels: [XCUIElement], direct: [XCUIElement], semantic: [XCUIElement], composite: [XCUIElement]) {
      let exactLabelElements = directQuery.allElementsBoundByAccessibilityElement
      let directElements = exactLabelElements.filter { $0.isHittable }
      let semanticCells = semanticCellQuery.allElementsBoundByAccessibilityElement.filter { $0.isHittable }
      let compositeCells = exactLabelElements.isEmpty && semanticCells.isEmpty
        ? compositeCellQuery.allElementsBoundByAccessibilityElement.filter { $0.isHittable }
        : []
      return (exactLabelElements, directElements, semanticCells, compositeCells)
    }

    var candidates = collectNotificationCandidates()
    if candidates.exactLabels.isEmpty && candidates.direct.isEmpty && candidates.semantic.isEmpty && candidates.composite.isEmpty {
      permissionXCTestStage(.settingsNotificationScrollSearch)
      for _ in 0..<4 {
        settings.swipeUp()
        candidates = collectNotificationCandidates()
        if !candidates.exactLabels.isEmpty || !candidates.direct.isEmpty || !candidates.semantic.isEmpty || !candidates.composite.isEmpty {
          break
        }
      }
    }

    if candidates.direct.count > 1 {
      XCTFail("missing or ambiguous exact system element: Notification settings row [AMBIGUOUS]")
      throw PermissionUIContractError.missingExactElement("Notification settings row")
    }
    var selectedElements: [XCUIElement]
    if candidates.direct.count == 1 {
      selectedElements = candidates.direct
    } else if !candidates.semantic.isEmpty {
      guard candidates.semantic.count == 1 else {
        XCTFail("missing or ambiguous exact system element: Notification settings row [SEMANTIC_AMBIGUOUS]")
        throw PermissionUIContractError.missingExactElement("Notification settings row")
      }
      selectedElements = candidates.semantic
    } else if !candidates.exactLabels.isEmpty {
      XCTFail("missing or ambiguous exact system element: Notification settings row [LABEL_NONHITTABLE]")
      throw PermissionUIContractError.missingExactElement("Notification settings row")
    } else {
      if candidates.composite.isEmpty {
        emitSettingsAccessibilitySummary(in: settings)
        if allowAbsent {
          throw PermissionUIContractError.notificationSettingsRowAbsent
        }
        XCTFail("missing or ambiguous exact system element: Notification settings row [COMPOSITE_ZERO]")
        throw PermissionUIContractError.notificationSettingsRowAbsent
      }
      guard candidates.composite.count == 1 else {
        XCTFail("missing or ambiguous exact system element: Notification settings row [COMPOSITE_AMBIGUOUS]")
        throw PermissionUIContractError.missingExactElement("Notification settings row")
      }
      selectedElements = candidates.composite
    }
    permissionXCTestStage(.settingsNotificationCountSingle)
    guard let element = selectedElements.popLast() else {
      XCTFail("missing or ambiguous exact system element: Notification settings row")
      throw PermissionUIContractError.missingExactElement("Notification settings row")
    }
    permissionXCTestStage(.settingsNotificationElementReady)
    return element
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

  private func findExactNotificationSwitch(in settings: XCUIApplication) throws -> XCUIElement? {
    let exactSwitchPredicate = NSPredicate(format: "label == %@ OR label == %@", "Allow Notifications", "알림 허용")
    let exactSwitchQuery = settings.switches.matching(exactSwitchPredicate)
    let appeared = XCTNSPredicateExpectation(
      predicate: NSPredicate { object, _ in
        guard let exactSwitchQuery = object as? XCUIElementQuery else { return false }
        return !exactSwitchQuery.allElementsBoundByAccessibilityElement.filter { $0.isHittable }.isEmpty
      },
      object: exactSwitchQuery
    )
    _ = XCTWaiter.wait(for: [appeared], timeout: 10)
    var exactSwitches = exactSwitchQuery.allElementsBoundByAccessibilityElement.filter { $0.isHittable }
    guard exactSwitches.count <= 1 else {
      XCTFail("missing or ambiguous exact system element: Allow Notifications switch")
      throw PermissionUIContractError.missingExactElement("Allow Notifications switch")
    }
    return exactSwitches.popLast()
  }

  private func openDaonNotificationSettingsViaIOS26Search(in settings: XCUIApplication) throws -> XCUIElement {
    guard #available(iOS 26.0, *) else {
      XCTFail("missing or ambiguous exact system element: Settings search button")
      throw PermissionUIContractError.missingExactElement("Settings search button")
    }

    permissionXCTestStage(.settingsSearchButton)
    var searchFields: [XCUIElement] = []
    for _ in 0..<6 {
      settings.swipeDown()
      searchFields = settings.searchFields.allElementsBoundByAccessibilityElement.filter { $0.isHittable }
      if searchFields.count == 1 {
        break
      }
      guard searchFields.count <= 1 else {
        XCTFail("missing or ambiguous exact system element: Settings search field")
        throw PermissionUIContractError.missingExactElement("Settings search field")
      }
    }

    permissionXCTestStage(.settingsSearchField)
    guard searchFields.count == 1, let searchField = searchFields.popLast() else {
      emitSettingsSearchAccessibilitySummary(in: settings)
      emitSettingsSearchSurfaceSummary(in: settings)
      XCTFail("missing or ambiguous exact system element: Settings search field")
      throw PermissionUIContractError.missingExactElement("Settings search field")
    }
    searchField.tap()
    let maximumExistingSearchTextLength = 128
    let emptySearchValues = ["", "Search", "검색"]
    if let existingText = searchField.value as? String, !emptySearchValues.contains(existingText) {
      guard existingText.count <= maximumExistingSearchTextLength else {
        XCTFail("missing or ambiguous exact system element: Settings search field")
        throw PermissionUIContractError.missingExactElement("Settings search field")
      }
      searchField.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: existingText.count))
    }
    searchField.typeText("Daon")
    let keyboardContinuePredicate = NSPredicate(format: "label == %@ OR label == %@", "Continue", "계속")
    var keyboardContinueButtons = settings.keyboards.buttons.matching(keyboardContinuePredicate).allElementsBoundByAccessibilityElement.filter { $0.isHittable }
    guard keyboardContinueButtons.count <= 1 else {
      XCTFail("missing or ambiguous exact system element: Settings keyboard continue button")
      throw PermissionUIContractError.missingExactElement("Settings keyboard continue button")
    }
    if keyboardContinueButtons.count == 1, let keyboardContinueButton = keyboardContinueButtons.popLast() {
      keyboardContinueButton.tap()
    } else if searchField.isHittable {
      var dictationButtons = settings.buttons.matching(identifier: "dictation").allElementsBoundByAccessibilityElement.filter { $0.isHittable }
      var globalContinueButtons = settings.buttons.matching(keyboardContinuePredicate).allElementsBoundByAccessibilityElement.filter { $0.isHittable }
      guard dictationButtons.count <= 1, globalContinueButtons.count <= 1 else {
        XCTFail("missing or ambiguous exact system element: Settings keyboard continue evidence")
        throw PermissionUIContractError.missingExactElement("Settings keyboard continue evidence")
      }
      if dictationButtons.count == 1, globalContinueButtons.count == 1, let globalContinueButton = globalContinueButtons.popLast() {
        globalContinueButton.tap()
      }
    }

    permissionXCTestStage(.settingsSearchResult)
    let exactDaonPredicate = NSPredicate(format: "label == %@", "Daon")
    let daonCellQuery = settings.cells.matching(exactDaonPredicate)
    let exactDaonQuery = settings.descendants(matching: .any).matching(exactDaonPredicate)
    let daonResultAppeared = XCTNSPredicateExpectation(
      predicate: NSPredicate { object, _ in
        guard let query = object as? XCUIElementQuery else { return false }
        if !query.allElementsBoundByAccessibilityElement.filter({ $0.isHittable }).isEmpty { return true }
        return !exactDaonQuery.allElementsBoundByAccessibilityElement.filter { $0.isHittable }.isEmpty
      },
      object: daonCellQuery
    )
    _ = XCTWaiter.wait(for: [daonResultAppeared], timeout: 10)
    var daonCells = daonCellQuery.allElementsBoundByAccessibilityElement.filter { $0.isHittable }
    var daonResult: XCUIElement?
    if daonCells.count == 1 {
      daonResult = daonCells.popLast()
    } else if daonCells.isEmpty {
      var exactDaonElements = exactDaonQuery.allElementsBoundByAccessibilityElement.filter { $0.isHittable }
      if exactDaonElements.count == 1 {
        daonResult = exactDaonElements.popLast()
      }
    }
    guard let daonResult else {
      emitSettingsSearchResultSummary(in: settings)
      XCTFail("missing or ambiguous exact system element: Settings search result")
      throw PermissionUIContractError.missingExactElement("Settings search result")
    }
    daonResult.tap()

    permissionXCTestStage(.settingsSearchAppSurface)
    if let directSwitch = try findExactNotificationSwitch(in: settings) {
      return directSwitch
    }
    do {
      let notificationsRow = try requireExactNotificationSettingsRow(in: settings, allowAbsent: true)
      XCTAssertTrue(notificationsRow.isHittable, "Notification settings row is not hittable")
      notificationsRow.tap()
      return try requireExactElement([
        settings.switches["Allow Notifications"],
        settings.switches["알림 허용"]
      ], description: "Allow Notifications switch")
    } catch PermissionUIContractError.notificationSettingsRowAbsent {
      XCTFail("missing or ambiguous exact system element: Daon notification settings surface")
      throw PermissionUIContractError.missingExactElement("Daon notification settings surface")
    }
  }

  private func setNotificationAuthorization(enabled target: Bool, in app: XCUIApplication, content: XCUIElement) throws {
    let settingsButton = app.buttons["알림 설정 열기"]
    for _ in 0..<8 where !settingsButton.isHittable { content.swipeUp() }
    XCTAssertTrue(settingsButton.waitForExistence(timeout: 10), "Production application settings button is unavailable")
    XCTAssertTrue(settingsButton.isHittable, "Production application settings button is not hittable")
    settingsButton.tap()

    let settings = XCUIApplication(bundleIdentifier: "com.apple.Preferences")
    permissionXCTestStage(.settingsForeground)
    XCTAssertTrue(settings.wait(for: .runningForeground, timeout: 10), "Settings app is not runningForeground")
    permissionXCTestStage(.settingsSwitchRead)
    let allowNotifications: XCUIElement
    if let directSwitch = try findExactNotificationSwitch(in: settings) {
      allowNotifications = directSwitch
    } else {
      do {
        permissionXCTestStage(.settingsNotificationRow)
        let notificationsRow = try requireExactNotificationSettingsRow(in: settings, allowAbsent: true)
        XCTAssertTrue(notificationsRow.isHittable, "Notification settings row is not hittable")
        permissionXCTestStage(.settingsNotificationRowTapPending)
        notificationsRow.tap()
        allowNotifications = try requireExactElement([
          settings.switches["Allow Notifications"],
          settings.switches["알림 허용"]
        ], description: "Allow Notifications switch")
      } catch PermissionUIContractError.notificationSettingsRowAbsent {
        if #available(iOS 26.0, *) {
          allowNotifications = try openDaonNotificationSettingsViaIOS26Search(in: settings)
        } else {
          XCTFail("missing or ambiguous exact system element: Notification settings row [COMPOSITE_ZERO]")
          throw PermissionUIContractError.notificationSettingsRowAbsent
        }
      }
    }
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
