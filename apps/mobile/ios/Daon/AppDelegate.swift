import UIKit
import React
import React_RCTAppDelegate
import ReactAppDependencyProvider

@main
class AppDelegate: UIResponder, UIApplicationDelegate {
  var window: UIWindow?
  var reactNativeDelegate: ReactNativeDelegate?
  var reactNativeFactory: RCTReactNativeFactory?

  func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
  ) -> Bool {
    let delegate = ReactNativeDelegate()
    let factory = RCTReactNativeFactory(delegate: delegate)
    delegate.dependencyProvider = RCTAppDependencyProvider()
    reactNativeDelegate = delegate
    reactNativeFactory = factory
    window = UIWindow(frame: UIScreen.main.bounds)
    DaonIOSHost.recordLifecycleState("created")
    if let url = launchOptions?[.url] as? URL {
      DaonIOSHost.recordPendingDeepLink(url.absoluteString)
    }
    factory.startReactNative(withModuleName: "Daon", in: window, launchOptions: launchOptions)
    return true
  }

  func application(
    _ app: UIApplication,
    open url: URL,
    options: [UIApplication.OpenURLOptionsKey: Any] = [:]
  ) -> Bool {
    DaonIOSHost.recordPendingDeepLink(url.absoluteString)
    return RCTLinkingManager.application(app, open: url, options: options)
  }

  func applicationDidEnterBackground(_ application: UIApplication) {
    DaonIOSHost.recordLifecycleState("background")
  }

  func applicationWillEnterForeground(_ application: UIApplication) {
    DaonIOSHost.recordLifecycleState("foreground")
  }

  func applicationDidBecomeActive(_ application: UIApplication) {
    DaonIOSHost.recordLifecycleState("active")
  }
}

class ReactNativeDelegate: RCTDefaultReactNativeFactoryDelegate {
  override func sourceURL(for bridge: RCTBridge) -> URL? { bundleURL() }

  override func bundleURL() -> URL? {
#if DEBUG
    RCTBundleURLProvider.sharedSettings().jsBundleURL(forBundleRoot: "index")
#else
    Bundle.main.url(forResource: "main", withExtension: "jsbundle")
#endif
  }
}
