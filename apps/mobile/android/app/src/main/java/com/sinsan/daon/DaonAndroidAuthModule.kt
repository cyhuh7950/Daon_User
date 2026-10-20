package com.sinsan.daon

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.verify.domain.DomainVerificationManager
import android.content.pm.verify.domain.DomainVerificationUserState
import android.net.Uri
import androidx.browser.customtabs.CustomTabsClient
import androidx.browser.customtabs.CustomTabsIntent
import android.os.Bundle
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.net.URLDecoder
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.concurrent.Executors
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

internal object AndroidAuthProtocol {
  private val random = SecureRandom()
  fun newVerifier(): String = ByteArray(32).also(random::nextBytes).let(::encode)
  fun challenge(verifier: String): String = encode(MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII)))
  private fun encode(bytes: ByteArray) = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
  fun validConfig(origin: String, client: String, redirect: String): Boolean = try {
    val base = URI(origin)
    val link = URI(redirect)
    fun safe(uri: URI): Boolean {
      val host = uri.host?.lowercase() ?: return false
      val ipLiteral = host.matches(Regex("[0-9.]+")) || host.contains(':')
      return uri.scheme == "https" && host.isNotBlank() && !ipLiteral && uri.port == -1 &&
        uri.userInfo == null && uri.fragment == null && host != "localhost" && !host.endsWith(".localhost")
    }
    safe(base) && safe(link) && base.path.isNullOrEmpty() && base.query == null &&
      !link.path.isNullOrBlank() && link.rawQuery == null && client.matches(Regex("[A-Za-z0-9._-]{3,128}"))
  } catch (_: Exception) { false }

  fun codeFromVerifiedLink(raw: String, redirect: String, state: String, verified: Boolean): String? {
    return try {
      if (!verified) return null
      val uri = URI(raw)
      val base = URI(redirect)
      if (uri.scheme != "https" || uri.scheme != base.scheme || uri.authority != base.authority ||
        uri.path != base.path || uri.fragment != null || uri.userInfo != null) return null
      val pairs = (uri.rawQuery ?: return null).split("&").map { it.split("=", limit = 2) }
      if (pairs.size != 2 || pairs.any { it.size != 2 }) return null
      val fields = pairs.associate { it[0] to URLDecoder.decode(it[1], "UTF-8") }
      if (fields.size != 2 || fields.keys != setOf("code", "state") || fields["state"] != state) return null
      fields["code"]?.takeIf { it.matches(Regex("[A-Za-z0-9._~-]{8,512}")) }
    } catch (_: Exception) { null }
  }
}

internal class AndroidAuthPending(val transactionId: String, val state: String, val verifier: String,
                                  val expiresAt: Instant) {
  private var consumed = false
  @Synchronized fun consume(raw: String, redirect: String, verified: Boolean, now: Instant): String? {
    if (consumed || !now.isBefore(expiresAt)) return null
    val code = AndroidAuthProtocol.codeFromVerifiedLink(raw, redirect, state, verified) ?: return null
    consumed = true
    return code
  }
}

internal object AndroidAuthVaultCodec {
  fun encrypt(value: String, key: SecretKey): String {
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.ENCRYPT_MODE, key)
    return Base64.getEncoder().encodeToString(cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8)))
  }
  fun decrypt(value: String, key: SecretKey): String {
    val bytes = Base64.getDecoder().decode(value)
    require(bytes.size > 28)
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, bytes.copyOfRange(0, 12)))
    return String(cipher.doFinal(bytes.copyOfRange(12, bytes.size)), Charsets.UTF_8)
  }
}

internal data class AndroidAuthSessionData(val userId: String, val tenantId: String, val workspaceId: String,
  val sessionId: String, val deviceId: String, val access: String, val refresh: String, val expiresAt: Instant) {
  fun publicProjection(): Map<String, String> = mapOf("user_id" to userId, "tenant_id" to tenantId,
    "workspace_id" to workspaceId, "session_id" to sessionId, "device_id" to deviceId,
    "status" to "authenticated")
}

internal object AndroidAuthFlow {
  fun status(hasSession: Boolean, hasPending: Boolean, hasCallback: Boolean): String = when {
    hasSession -> "authenticated"
    hasCallback && hasPending -> "callback_ready"
    hasPending -> "awaiting_browser"
    else -> "signed_out"
  }
  fun workspaceFailure(httpStatus: Int?): String =
    if (httpStatus == 401 || httpStatus == 403) "signed_out" else "workspace_unavailable"

  fun clearAfterRefreshFailure(httpStatus: Int?): Boolean = httpStatus == 401 || httpStatus == 403

  fun logoutConfirmed(data: Map<String, String>): Boolean {
    require(data["status"] == "logged_out" && data["replayed"] in setOf("true", "false"))
    return true
  }

  fun logoutWith(revoke: () -> Map<String, String>, clearVault: () -> Unit) {
    logoutConfirmed(revoke())
    clearVault()
  }

  fun logoutWithCurrent(resolve: () -> AndroidAuthSessionData?, hasVault: () -> Boolean,
                        revoke: (AndroidAuthSessionData) -> Map<String, String>, clearVault: () -> Unit): String {
    val session = resolve()
    if (session == null) {
      if (hasVault()) throw IllegalStateException("SESSION_UNAVAILABLE")
      clearVault()
      return "signed_out"
    }
    logoutWith({ revoke(session) }, clearVault)
    return "signed_out"
  }

  fun switchedSession(prior: AndroidAuthSessionData, tenantId: String, data: Map<String, String>): AndroidAuthSessionData {
    require(data["client_kind"] == "native" && data["user_id"] == prior.userId &&
      data["tenant_id"] == tenantId && !data["workspace_id"].isNullOrBlank())
    val sessionId = data["session_id"]?.takeIf { it.isNotBlank() } ?: error("SESSION_MISSING")
    val deviceId = data["device_id"]?.takeIf { it.isNotBlank() } ?: error("DEVICE_MISSING")
    val expiresAt = Instant.parse(data["expires_at"] ?: error("EXPIRY_MISSING"))
    if (tenantId == prior.tenantId) {
      require(sessionId == prior.sessionId && deviceId == prior.deviceId &&
        !data.containsKey("access_credential") && !data.containsKey("refresh_credential"))
      return prior.copy(workspaceId = data.getValue("workspace_id"), expiresAt = expiresAt)
    }
    require(sessionId != prior.sessionId && deviceId != prior.deviceId)
    val access = data["access_credential"]?.takeIf { it.isNotBlank() } ?: error("ACCESS_MISSING")
    val refresh = data["refresh_credential"]?.takeIf { it.isNotBlank() } ?: error("REFRESH_MISSING")
    return AndroidAuthSessionData(prior.userId, tenantId, data.getValue("workspace_id"),
      sessionId, deviceId, access, refresh, expiresAt)
  }
}

internal object AndroidBrowserPolicy {
  fun acceptProvider(defaultHandler: String?, browserPackages: List<String>, provider: String?, ownPackage: String): String? =
    provider?.takeIf { it != ownPackage && it == defaultHandler && browserPackages.contains(it) }
}

internal class AndroidAuthHttpFailure(val status: Int) : IllegalStateException("AUTH_HTTP_FAILURE")

private object AndroidAuthState {
  @Volatile var pending: AndroidAuthPending? = null
  @Volatile var callbackLink: String? = null
  @Volatile var session: AndroidAuthSessionData? = null
}

class DaonAndroidAuthCallbackActivity : Activity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    val raw = intent?.dataString
    val redirect = BuildConfig.ANDROID_AUTH_REDIRECT_URI
    val host = try { URI(redirect).host } catch (_: Exception) { null }
    val manager = getSystemService(DomainVerificationManager::class.java)
    val verified = try {
      host != null && manager.getDomainVerificationUserState(packageName)?.hostToStateMap?.get(host) ==
        DomainVerificationUserState.DOMAIN_STATE_VERIFIED
    } catch (_: Exception) { false }
    if (raw != null && verified && raw.startsWith(redirect + "?")) AndroidAuthState.callbackLink = raw
    val launch = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
    startActivity(launch)
    finish()
  }
}

class DaonAndroidAuthModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
  private val executor = Executors.newSingleThreadExecutor()
  private val origin get() = BuildConfig.ANDROID_AUTH_ORIGIN
  private val client get() = BuildConfig.ANDROID_AUTH_CLIENT_ID
  private val redirect get() = BuildConfig.ANDROID_AUTH_REDIRECT_URI
  private val prefs get() = context.getSharedPreferences("daon_android_auth_vault", Context.MODE_PRIVATE)
  override fun getName(): String = "DaonAndroidAuth"

  private fun key(): SecretKey {
    val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (store.getKey("daon_android_refresh_v1", null) as? SecretKey)?.let { return it }
    val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
    generator.init(KeyGenParameterSpec.Builder("daon_android_refresh_v1",
      KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
      .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
      .setKeySize(256).build())
    return generator.generateKey()
  }

  private fun clear() {
    AndroidAuthState.session = null
    AndroidAuthState.pending = null
    AndroidAuthState.callbackLink = null
    check(prefs.edit().clear().commit())
  }

  private fun request(path: String, body: JSONObject?, access: String? = null): JSONObject {
    val address = URL(origin + path)
    val connection = address.openConnection() as HttpURLConnection
    try {
      connection.instanceFollowRedirects = false
      connection.connectTimeout = 8000
      connection.readTimeout = 8000
      connection.requestMethod = if (body == null) "GET" else "POST"
      connection.setRequestProperty("Accept", "application/json")
      if (access != null) connection.setRequestProperty("Authorization", "Bearer $access")
      if (body != null) {
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json")
        connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
      }
      if (connection.responseCode !in 200..299) throw AndroidAuthHttpFailure(connection.responseCode)
      val bytes = connection.inputStream.use { it.readNBytes(131073) }
      if (bytes.size > 131072) throw IllegalStateException("AUTH_RESPONSE_TOO_LARGE")
      return JSONObject(String(bytes, Charsets.UTF_8)).getJSONObject("data")
    } finally { connection.disconnect() }
  }

  private fun projection(data: Map<String, String>, workspaces: List<Map<String, String>> = emptyList()): Any {
    val map = Arguments.createMap()
    data.forEach { (key, value) -> map.putString(key, value) }
    val list = Arguments.createArray()
    workspaces.forEach { workspace ->
      val item = Arguments.createMap()
      workspace.forEach { (key, value) -> item.putString(key, value) }
      list.pushMap(item)
    }
    map.putArray("workspaces", list)
    return map
  }

  private fun sessionFrom(data: JSONObject): AndroidAuthSessionData {
    require(data.getString("client_kind") == "native")
    return AndroidAuthSessionData(data.getString("user_id"), data.getString("tenant_id"),
      data.getString("workspace_id"), data.getString("session_id"), data.getString("device_id"),
      data.getString("access_credential"), data.getString("refresh_credential"),
      Instant.parse(data.getString("expires_at")))
  }

  private fun store(session: AndroidAuthSessionData) {
    val sealed = AndroidAuthVaultCodec.encrypt(session.refresh, key())
    check(prefs.edit().putString("refresh_ciphertext", sealed).commit())
    AndroidAuthState.session = session.copy(refresh = "")
  }

  private fun current(): AndroidAuthSessionData? {
    AndroidAuthState.session?.takeIf { Instant.now().isBefore(it.expiresAt.minusSeconds(15)) }?.let { return it }
    val sealed = prefs.getString("refresh_ciphertext", null) ?: return null
    val credential = AndroidAuthVaultCodec.decrypt(sealed, key())
    return try {
      sessionFrom(request("/api/v1/session/refresh", JSONObject().put("refresh_credential", credential))).also(::store)
    } catch (error: Exception) {
      if (AndroidAuthFlow.clearAfterRefreshFailure((error as? AndroidAuthHttpFailure)?.status)) {
        clear()
        null
      } else throw error
    }
  }

  private fun workspaces(session: AndroidAuthSessionData): List<Map<String, String>> {
    val data = request("/api/v1/session/tenants", null, session.access)
    require(data.getString("current_tenant_id") == session.tenantId)
    val items = data.getJSONArray("tenants")
    val available = (0 until items.length()).map { index ->
      val item = items.getJSONObject(index)
      val kind = item.getString("kind")
      require(kind == "personal" || kind == "organization")
      mapOf("tenant_id" to item.getString("tenant_id"), "workspace_id" to item.getString("workspace_id"),
        "display_name" to item.getString("display_name"), "kind" to kind)
    }
    require(available.any { it["tenant_id"] == session.tenantId && it["workspace_id"] == session.workspaceId })
    return available
  }

  private fun run(promise: Promise, operation: () -> Any) {
    executor.execute {
      try { promise.resolve(operation()) }
      catch (_: Exception) { promise.reject("ANDROID_AUTH_UNAVAILABLE", "Android authentication is unavailable") }
    }
  }

  @ReactMethod fun beginLogin(promise: Promise) = run(promise) {
    check(AndroidAuthProtocol.validConfig(origin, client, redirect))
    check(context.currentActivity != null)
    AndroidAuthState.pending = null
    AndroidAuthState.callbackLink = null
    val state = AndroidAuthProtocol.newVerifier()
    val verifier = AndroidAuthProtocol.newVerifier()
    val body = JSONObject().put("client_id", client).put("redirect_uri", redirect).put("state", state)
      .put("code_challenge", AndroidAuthProtocol.challenge(verifier)).put("code_challenge_method", "S256")
    val data = request("/api/v1/session/android/transactions", body)
    val transaction = data.getString("transaction_id")
    val authUrl = URI(data.getString("authorization_url"))
    val base = URI(origin)
    check(authUrl.scheme == "https" && authUrl.host == base.host && authUrl.port == -1 &&
      authUrl.path == "/auth/android/authorize" && authUrl.fragment == null &&
      authUrl.rawQuery == "transaction_id=$transaction")
    val expires = Instant.parse(data.getString("expires_at"))
    val now = Instant.now()
    check(expires.isAfter(now) && !expires.isAfter(now.plusSeconds(300)))
    val webIntent = Intent(Intent.ACTION_VIEW, Uri.parse(authUrl.toString()))
      .addCategory(Intent.CATEGORY_BROWSABLE)
    val defaultHandler = context.packageManager.resolveActivity(webIntent, 0)?.activityInfo?.packageName
    val browserIntent = Intent.makeMainSelectorActivity(Intent.ACTION_MAIN, Intent.CATEGORY_APP_BROWSER)
    val browserPackages = context.packageManager.queryIntentActivities(browserIntent, 0)
      .map { it.activityInfo.packageName }
    val provider = CustomTabsClient.getPackageName(context, null, false)
    val browserPackage = AndroidBrowserPolicy.acceptProvider(defaultHandler, browserPackages, provider, context.packageName)
      ?: throw IllegalStateException("BROWSER_UNAVAILABLE")
    val tabs = CustomTabsIntent.Builder().build()
    tabs.intent.setPackage(browserPackage)
    tabs.intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    AndroidAuthState.pending = AndroidAuthPending(transaction, state, verifier, expires)
    try { tabs.launchUrl(context, Uri.parse(authUrl.toString())) }
    catch (error: Exception) { AndroidAuthState.pending = null; throw error }
    projection(mapOf("status" to "awaiting_browser", "transaction_id" to transaction))
  }

  @ReactMethod fun consumeAuthorizationLink(url: String, promise: Promise) = run(promise) {
    check(url.isEmpty())
    check(AndroidAuthProtocol.validConfig(origin, client, redirect))
    val pending = AndroidAuthState.pending ?: throw IllegalStateException("NO_PENDING")
    val raw = AndroidAuthState.callbackLink ?: throw IllegalStateException("NO_CALLBACK")
    AndroidAuthState.callbackLink = null
    val code = pending.consume(raw, redirect, true, Instant.now()) ?: throw IllegalStateException("INVALID_CALLBACK")
    AndroidAuthState.pending = null
    val body = JSONObject().put("transaction_id", pending.transactionId).put("code", code)
      .put("code_verifier", pending.verifier).put("redirect_uri", redirect)
    val session = sessionFrom(request("/api/v1/session/android/exchange", body))
    store(session)
    try { projection(session.publicProjection(), workspaces(session)) }
    catch (error: Exception) {
      val status = AndroidAuthFlow.workspaceFailure((error as? AndroidAuthHttpFailure)?.status)
      if (status == "signed_out") clear()
      projection(mapOf("status" to status))
    }
  }

  @ReactMethod fun getSessionState(promise: Promise) = run(promise) {
    if (!AndroidAuthProtocol.validConfig(origin, client, redirect))
      projection(mapOf("status" to "unavailable"))
    else {
      val session = try { current() }
      catch (_: Exception) { return@run projection(mapOf("status" to "workspace_unavailable")) }
      if (session == null) projection(mapOf("status" to AndroidAuthFlow.status(false,
        AndroidAuthState.pending != null, AndroidAuthState.callbackLink != null)))
      else try { projection(session.publicProjection(), workspaces(session)) }
      catch (error: Exception) {
        val status = AndroidAuthFlow.workspaceFailure((error as? AndroidAuthHttpFailure)?.status)
        if (status == "signed_out") clear()
        projection(mapOf("status" to status))
      }
    }
  }

  @ReactMethod fun switchWorkspace(tenantId: String, promise: Promise) = run(promise) {
    if (!AndroidAuthProtocol.validConfig(origin, client, redirect)) projection(mapOf("status" to "unavailable"))
    else {
      val session = current()
      if (session == null) projection(mapOf("status" to "signed_out"))
      else {
        val response = try {
          request("/api/v1/session/tenant", JSONObject().put("tenant_id", tenantId), session.access)
        } catch (error: Exception) {
          val status = AndroidAuthFlow.workspaceFailure((error as? AndroidAuthHttpFailure)?.status)
          if (status == "signed_out") clear()
          return@run projection(mapOf("status" to status))
        }
        val next = try {
          val fields = listOf("user_id", "tenant_id", "workspace_id", "session_id", "device_id",
            "client_kind", "expires_at", "access_credential", "refresh_credential")
            .filter(response::has).associateWith(response::getString)
          AndroidAuthFlow.switchedSession(session, tenantId, fields).also {
            if (it.sessionId != session.sessionId) store(it)
          }
        } catch (_: Exception) {
          clear()
          return@run projection(mapOf("status" to "signed_out"))
        }
        try { projection(next.publicProjection(), workspaces(next)) }
        catch (error: Exception) {
          val status = AndroidAuthFlow.workspaceFailure((error as? AndroidAuthHttpFailure)?.status)
          if (status == "signed_out") clear()
          projection(mapOf("status" to status))
        }
      }
    }
  }

  @ReactMethod fun logout(promise: Promise) = run(promise) {
    val status = AndroidAuthFlow.logoutWithCurrent(::current,
      { prefs.contains("refresh_ciphertext") }, { session ->
      val response = request("/api/v1/session/native/logout", JSONObject(), session.access)
      mapOf("status" to response.getString("status"), "replayed" to response.getString("replayed"))
    }, ::clear)
    projection(mapOf("status" to status))
  }
}
