package com.sinsan.daon

import android.content.Intent
import android.os.Bundle
import com.facebook.react.ReactActivity
import com.facebook.react.ReactActivityDelegate
import com.facebook.react.defaults.DefaultNewArchitectureEntryPoint.fabricEnabled
import com.facebook.react.defaults.DefaultReactActivityDelegate

class MainActivity : ReactActivity() {
  override fun getMainComponentName(): String = "Daon"

  override fun createReactActivityDelegate(): ReactActivityDelegate =
      DefaultReactActivityDelegate(this, mainComponentName, fabricEnabled)

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    DaonAndroidHostModule.recordLifecycleState(applicationContext, "created")
    DaonAndroidHostModule.recordPendingDeepLink(applicationContext, intent?.dataString)
  }

  override fun onNewIntent(intent: Intent) {
    super.onNewIntent(intent)
    setIntent(intent)
    DaonAndroidHostModule.recordPendingDeepLink(applicationContext, intent.dataString)
  }

  override fun onResume() {
    super.onResume()
    DaonAndroidHostModule.recordLifecycleState(applicationContext, "foreground")
  }

  override fun onPause() {
    DaonAndroidHostModule.recordLifecycleState(applicationContext, "background")
    super.onPause()
  }
}
