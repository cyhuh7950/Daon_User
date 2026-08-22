package com.sinsan.daon

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod

class DaonAndroidHostModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
  override fun getName(): String = "DaonAndroidHost"

  @ReactMethod
  fun saveNavigationRoute(route: String, promise: Promise) {
    if (!ALLOWED_NATIVE_ROUTES.contains(route)) {
      promise.reject("NATIVE_ROUTE_NOT_ALLOWED", "Only approved native routes may be persisted")
      return
    }
    preferences(context).edit().putString(KEY_ROUTE, route).apply()
    promise.resolve(true)
  }

  @ReactMethod
  fun restoreNavigationRoute(promise: Promise) {
    val route = preferences(context).getString(KEY_ROUTE, null)
    promise.resolve(if (route != null && ALLOWED_NATIVE_ROUTES.contains(route)) route else null)
  }

  @ReactMethod
  fun getLifecycleState(promise: Promise) {
    promise.resolve(preferences(context).getString(KEY_LIFECYCLE, "unknown"))
  }

  @ReactMethod
  fun consumePendingDeepLink(promise: Promise) {
    val prefs = preferences(context)
    val value = prefs.getString(KEY_PENDING_DEEP_LINK, null)
    prefs.edit().remove(KEY_PENDING_DEEP_LINK).apply()
    promise.resolve(value)
  }

  @ReactMethod
  fun checkPermission(kind: String, promise: Promise) {
    val permission = permissionFor(kind)
    if (permission == null) {
      promise.reject("ANDROID_PERMISSION_KIND_UNKNOWN", "Unsupported Android permission kind")
      return
    }
    if (kind == "notification" && Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
      promise.resolve("GRANTED")
      return
    }
    val activity = context.currentActivity
    val granted = ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED
    val requested = preferences(context).getBoolean("permission_requested_$kind", false)
    val rationale = activity != null && ActivityCompat.shouldShowRequestPermissionRationale(activity, permission)
    promise.resolve(when {
      granted -> "GRANTED"
      requested && !rationale -> "PERMANENTLY_DENIED"
      requested -> "DENIED_CAN_ASK_AGAIN"
      else -> "NOT_REQUESTED"
    })
  }

  @ReactMethod
  fun requestPermission(kind: String, promise: Promise) {
    val permission = permissionFor(kind)
    val activity = context.currentActivity
    if (permission == null) {
      promise.reject("ANDROID_PERMISSION_KIND_UNKNOWN", "Unsupported Android permission kind")
      return
    }
    if (kind == "notification" && Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
      promise.resolve("GRANTED")
      return
    }
    if (activity == null) {
      promise.reject("ANDROID_ACTIVITY_UNAVAILABLE", "Permission request requires a foreground Activity")
      return
    }
    preferences(context).edit().putBoolean("permission_requested_$kind", true).apply()
    ActivityCompat.requestPermissions(activity, arrayOf(permission), requestCodeFor(kind))
    promise.resolve("REQUESTED")
  }

  @ReactMethod
  fun openApplicationSettings(promise: Promise) {
    val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
      .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    context.startActivity(intent)
    promise.resolve(true)
  }

  private fun permissionFor(kind: String): String? = when (kind) {
    "camera" -> Manifest.permission.CAMERA
    "microphone" -> Manifest.permission.RECORD_AUDIO
    "notification" -> Manifest.permission.POST_NOTIFICATIONS
    else -> null
  }

  private fun requestCodeFor(kind: String): Int = when (kind) {
    "camera" -> 4101
    "microphone" -> 4102
    else -> 4103
  }

  companion object {
    val ALLOWED_NATIVE_ROUTES = setOf("Home", "WorkspaceList", "WorkspaceDetail", "Inbox", "RunHistory", "Notifications", "ModelConnections", "AccountSettings")
    private const val PREFERENCES = "daon_android_navigation_state"
    private const val KEY_ROUTE = "native_route_key"
    private const val KEY_LIFECYCLE = "lifecycle_state"
    private const val KEY_PENDING_DEEP_LINK = "pending_deep_link"

    private fun preferences(context: Context) = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    fun recordLifecycleState(context: Context, state: String) {
      preferences(context).edit().putString(KEY_LIFECYCLE, state).apply()
    }

    fun recordPendingDeepLink(context: Context, value: String?) {
      if (value != null) preferences(context).edit().putString(KEY_PENDING_DEEP_LINK, value).apply()
    }
  }
}
