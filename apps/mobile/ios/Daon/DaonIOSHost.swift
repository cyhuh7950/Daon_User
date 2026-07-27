import AVFoundation
import React
import UIKit
import UserNotifications

@objc(DaonIOSHost)
final class DaonIOSHost: NSObject {
  private static let allowedNativeRoutes: Set<String> = [
    "Home", "WorkspaceList", "WorkspaceDetail", "Inbox", "RunHistory",
    "Notifications", "ModelConnections", "AccountSettings"
  ]
  private static let routeKey = "native_route_key"
  private static let lifecycleKey = "lifecycle_state"
  private static let pendingLock = NSLock()
  private static var pendingDeepLink: String?

  @objc
  static func requiresMainQueueSetup() -> Bool { true }

  @objc(saveNavigationRoute:resolver:rejecter:)
  func saveNavigationRoute(
    _ route: String,
    resolver resolve: RCTPromiseResolveBlock,
    rejecter reject: RCTPromiseRejectBlock
  ) {
    guard Self.allowedNativeRoutes.contains(route) else {
      reject("NATIVE_ROUTE_NOT_ALLOWED", "Only approved native routes may be persisted", nil)
      return
    }
    UserDefaults.standard.set(route, forKey: Self.routeKey)
    NSLog("DAON_ROUTE_SAVED=%@", route)
    resolve(true)
  }

  @objc(restoreNavigationRoute:rejecter:)
  func restoreNavigationRoute(
    _ resolve: RCTPromiseResolveBlock,
    rejecter reject: RCTPromiseRejectBlock
  ) {
    let route = UserDefaults.standard.string(forKey: Self.routeKey)
    resolve(route.flatMap { Self.allowedNativeRoutes.contains($0) ? $0 : nil })
  }

  @objc(getLifecycleState:rejecter:)
  func getLifecycleState(
    _ resolve: RCTPromiseResolveBlock,
    rejecter reject: RCTPromiseRejectBlock
  ) {
    resolve(UserDefaults.standard.string(forKey: Self.lifecycleKey) ?? "unknown")
  }

  @objc(consumePendingDeepLink:rejecter:)
  func consumePendingDeepLink(
    _ resolve: RCTPromiseResolveBlock,
    rejecter reject: RCTPromiseRejectBlock
  ) {
    Self.pendingLock.lock()
    let value = Self.pendingDeepLink
    Self.pendingDeepLink = nil
    Self.pendingLock.unlock()
    resolve(value)
  }

  @objc(checkPermission:resolver:rejecter:)
  func checkPermission(
    _ kind: String,
    resolver resolve: @escaping RCTPromiseResolveBlock,
    rejecter reject: @escaping RCTPromiseRejectBlock
  ) {
    switch kind {
    case "camera": resolve(Self.capturePermissionState(.video))
    case "microphone": resolve(Self.capturePermissionState(.audio))
    case "notification":
      UNUserNotificationCenter.current().getNotificationSettings { settings in
        resolve(Self.notificationPermissionState(settings.authorizationStatus))
      }
    default: reject("IOS_PERMISSION_KIND_UNKNOWN", "Unsupported iOS permission kind", nil)
    }
  }

  @objc(requestPermission:resolver:rejecter:)
  func requestPermission(
    _ kind: String,
    resolver resolve: @escaping RCTPromiseResolveBlock,
    rejecter reject: @escaping RCTPromiseRejectBlock
  ) {
    switch kind {
    case "camera": Self.requestCapturePermission(.video, resolve: resolve)
    case "microphone": Self.requestCapturePermission(.audio, resolve: resolve)
    case "notification":
      UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { granted, error in
        if let error { reject("IOS_NOTIFICATION_PERMISSION_FAILED", error.localizedDescription, error) }
        else { resolve(granted ? "GRANTED" : "DENIED") }
      }
    default: reject("IOS_PERMISSION_KIND_UNKNOWN", "Unsupported iOS permission kind", nil)
    }
  }

  @objc(openApplicationSettings:rejecter:)
  func openApplicationSettings(
    _ resolve: @escaping RCTPromiseResolveBlock,
    rejecter reject: @escaping RCTPromiseRejectBlock
  ) {
    DispatchQueue.main.async {
      guard let url = URL(string: UIApplication.openSettingsURLString) else {
        reject("IOS_SETTINGS_URL_UNAVAILABLE", "Application settings URL is unavailable", nil)
        return
      }
      UIApplication.shared.open(url, options: [:]) { resolve($0) }
    }
  }

  @objc(openNotificationSettings:rejecter:)
  func openNotificationSettings(
    _ resolve: @escaping RCTPromiseResolveBlock,
    rejecter reject: @escaping RCTPromiseRejectBlock
  ) {
    DispatchQueue.main.async {
      let settingsURLString: String
      if #available(iOS 16.0, *) {
        settingsURLString = UIApplication.openNotificationSettingsURLString
      } else {
        settingsURLString = UIApplication.openSettingsURLString
      }
      guard let url = URL(string: settingsURLString) else {
        reject("IOS_NOTIFICATION_SETTINGS_URL_UNAVAILABLE", "Notification settings URL is unavailable", nil)
        return
      }
      UIApplication.shared.open(url, options: [:]) { resolve($0) }
    }
  }

  static func recordLifecycleState(_ state: String) {
    guard ["created", "background", "foreground", "active"].contains(state) else { return }
    UserDefaults.standard.set(state, forKey: lifecycleKey)
    NSLog("DAON_LIFECYCLE_STATE=%@", state)
  }

  static func recordPendingDeepLink(_ value: String) {
    pendingLock.lock()
    pendingDeepLink = value
    pendingLock.unlock()
    NSLog("DAON_PENDING_DEEP_LINK_RECEIVED")
  }

  private static func capturePermissionState(_ mediaType: AVMediaType) -> String {
    switch AVCaptureDevice.authorizationStatus(for: mediaType) {
    case .authorized: "GRANTED"
    case .notDetermined: "NOT_REQUESTED"
    case .denied: "DENIED"
    case .restricted: "RESTRICTED"
    @unknown default: "RESTRICTED"
    }
  }

  private static func requestCapturePermission(_ mediaType: AVMediaType, resolve: @escaping RCTPromiseResolveBlock) {
    AVCaptureDevice.requestAccess(for: mediaType) { granted in resolve(granted ? "GRANTED" : "DENIED") }
  }

  private static func notificationPermissionState(_ status: UNAuthorizationStatus) -> String {
    switch status {
    case .authorized, .provisional, .ephemeral: "GRANTED"
    case .notDetermined: "NOT_REQUESTED"
    case .denied: "DENIED"
    @unknown default: "RESTRICTED"
    }
  }
}
