package com.sinsan.daon

import org.junit.Assert.*
import org.junit.Test
import java.time.Instant
import javax.crypto.spec.SecretKeySpec

class DaonAndroidAuthModuleTest {
  @Test fun browserLaunchRequiresSelectedOsBrowserCustomTabsProvider() {
    assertEquals("os.browser", AndroidBrowserPolicy.acceptProvider(
      "os.browser", listOf("os.browser"), "os.browser", "com.sinsan.daon"))
    assertNull(AndroidBrowserPolicy.acceptProvider("arbitrary.handler", listOf("os.browser"), "os.browser", "com.sinsan.daon"))
    assertNull(AndroidBrowserPolicy.acceptProvider("os.browser", listOf("os.browser"), "arbitrary.handler", "com.sinsan.daon"))
    assertNull(AndroidBrowserPolicy.acceptProvider("os.browser", emptyList(), "os.browser", "com.sinsan.daon"))
    assertNull(AndroidBrowserPolicy.acceptProvider("com.sinsan.daon", listOf("com.sinsan.daon"), "com.sinsan.daon", "com.sinsan.daon"))
  }
  @Test fun callbackStatusAllowsNativeOnlyConsumptionThenSession() {
    assertEquals("signed_out", AndroidAuthFlow.status(false, false, false))
    assertEquals("awaiting_browser", AndroidAuthFlow.status(false, true, false))
    assertEquals("callback_ready", AndroidAuthFlow.status(false, true, true))
    val pending = AndroidAuthPending("and-transaction", "synthetic-state-123456", "v".repeat(43),
      Instant.parse("2026-10-12T00:05:00Z"))
    assertEquals("synthetic-code", pending.consume(
      "https://link.example.test/android/callback?code=synthetic-code&state=synthetic-state-123456",
      "https://link.example.test/android/callback", true, Instant.parse("2026-10-12T00:04:59Z")))
    assertEquals("authenticated", AndroidAuthFlow.status(true, false, false))
  }
  @Test fun transientWorkspaceFailureRetainsVaultButUnauthorizedClearsIt() {
    assertEquals("workspace_unavailable", AndroidAuthFlow.workspaceFailure(null))
    assertEquals("workspace_unavailable", AndroidAuthFlow.workspaceFailure(503))
    assertEquals("signed_out", AndroidAuthFlow.workspaceFailure(401))
    assertEquals("signed_out", AndroidAuthFlow.workspaceFailure(403))
  }
  @Test fun pkceS256AndUnpredictableVerifier() {
    val verifier = AndroidAuthProtocol.newVerifier()
    assertTrue(verifier.matches(Regex("[A-Za-z0-9_-]{43,128}")))
    assertNotEquals(verifier, AndroidAuthProtocol.newVerifier())
    assertEquals("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
      AndroidAuthProtocol.challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"))
  }

  @Test fun configuredHttpsOnlyAndNoImplicitLocalFallback() {
    assertTrue(AndroidAuthProtocol.validConfig("https://api.example.test", "synthetic-android-client",
      "https://link.example.test/android/callback"))
    assertFalse(AndroidAuthProtocol.validConfig("", "synthetic-android-client", "https://link.example.test/android/callback"))
    assertFalse(AndroidAuthProtocol.validConfig("http://localhost:8000", "synthetic-android-client",
      "https://link.example.test/android/callback"))
    assertFalse(AndroidAuthProtocol.validConfig("https://localhost", "synthetic-android-client",
      "https://link.example.test/android/callback"))
    assertFalse(AndroidAuthProtocol.validConfig("https://127.0.0.1", "synthetic-android-client",
      "https://link.example.test/android/callback"))
    assertFalse(AndroidAuthProtocol.validConfig("https://10.0.0.1", "synthetic-android-client",
      "https://link.example.test/android/callback"))
    assertFalse(AndroidAuthProtocol.validConfig("https://api.example.test", "synthetic-android-client",
      "https://192.168.1.2/android/callback"))
    assertFalse(AndroidAuthProtocol.validConfig("https://api.example.test", "", "https://link.example.test/android/callback"))
    assertFalse(AndroidAuthProtocol.validConfig("https://api.example.test", "synthetic-android-client", "sinsan-daon://app/auth"))
  }

  @Test fun verifiedExactAppLinkStateAndOneCodeOnly() {
    val redirect = "https://link.example.test/android/callback"
    val state = "synthetic-state-123456"
    val valid = "$redirect?code=synthetic-code&state=$state"
    assertEquals("synthetic-code", AndroidAuthProtocol.codeFromVerifiedLink(valid, redirect, state, true))
    assertNull(AndroidAuthProtocol.codeFromVerifiedLink(valid, redirect, state, false))
    assertNull(AndroidAuthProtocol.codeFromVerifiedLink(valid, redirect, "wrong-state-123456", true))
    assertNull(AndroidAuthProtocol.codeFromVerifiedLink("$valid&code=second", redirect, state, true))
    assertNull(AndroidAuthProtocol.codeFromVerifiedLink("http://link.example.test/android/callback?code=x&state=$state", redirect, state, true))
    assertNull(AndroidAuthProtocol.codeFromVerifiedLink("sinsan-daon://app/auth?code=x&state=$state", redirect, state, true))
  }

  @Test fun pendingAuthorizationExpiresAndConsumesOnlyOnce() {
    val pending = AndroidAuthPending("and-transaction", "synthetic-state-123456", "v".repeat(43),
      Instant.parse("2026-10-12T00:05:00Z"))
    val link = "https://link.example.test/android/callback?code=synthetic-code&state=synthetic-state-123456"
    assertEquals("synthetic-code", pending.consume(link, "https://link.example.test/android/callback", true,
      Instant.parse("2026-10-12T00:04:59Z")))
    assertNull(pending.consume(link, "https://link.example.test/android/callback", true,
      Instant.parse("2026-10-12T00:04:59Z")))
    val expired = AndroidAuthPending("and-transaction", "synthetic-state-123456", "v".repeat(43),
      Instant.parse("2026-10-12T00:05:00Z"))
    assertNull(expired.consume(link, "https://link.example.test/android/callback", true,
      Instant.parse("2026-10-12T00:05:00Z")))
  }

  @Test fun vaultCiphertextIsAuthenticatedAndPublicProjectionHasNoCredential() {
    val key = SecretKeySpec(ByteArray(32) { it.toByte() }, "AES")
    val token = "synthetic-refresh-credential-012345678901234567890"
    val ciphertext = AndroidAuthVaultCodec.encrypt(token, key)
    assertFalse(ciphertext.contains(token))
    assertEquals(token, AndroidAuthVaultCodec.decrypt(ciphertext, key))
    assertThrows(Exception::class.java) {
      AndroidAuthVaultCodec.decrypt(ciphertext.dropLast(4) + "AAAA", key)
    }
    val projection = AndroidAuthSessionData("user-1", "tenant-1", "workspace-1", "session-1", "device-1",
      "synthetic-access-credential-012345678901234567890", token,
      Instant.parse("2026-10-12T01:00:00Z")).publicProjection()
    assertEquals("workspace-1", projection["workspace_id"])
    assertFalse(projection.values.contains(token))
    assertFalse(projection.keys.any { it.contains("credential") || it.contains("code") || it.contains("verifier") })
  }
}
