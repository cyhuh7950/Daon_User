package com.sinsan.daon

import org.junit.Assert.*
import org.junit.Test
import java.time.Instant
import javax.crypto.spec.SecretKeySpec

class DaonAndroidAuthModuleTest {
  @Test fun nativeLogoutRequiresConfirmedServerRevocation() {
    assertEquals(true, AndroidAuthFlow.logoutConfirmed(mapOf("status" to "logged_out", "replayed" to "false")))
    assertEquals(true, AndroidAuthFlow.logoutConfirmed(mapOf("status" to "logged_out", "replayed" to "true")))
    assertThrows(Exception::class.java) {
      AndroidAuthFlow.logoutConfirmed(mapOf("status" to "pending", "replayed" to "false"))
    }
    assertThrows(Exception::class.java) {
      AndroidAuthFlow.logoutConfirmed(mapOf("status" to "logged_out"))
    }
  }
  @Test fun nativeLogoutPreservesVaultUnlessServerConfirms() {
    var cleared = false
    assertThrows(Exception::class.java) {
      AndroidAuthFlow.logoutWith({ throw IllegalStateException("NETWORK_UNAVAILABLE") }, { cleared = true })
    }
    assertFalse(cleared)
    assertThrows(Exception::class.java) {
      AndroidAuthFlow.logoutWith({ mapOf("status" to "pending", "replayed" to "false") }, { cleared = true })
    }
    assertFalse(cleared)
    AndroidAuthFlow.logoutWith({ mapOf("status" to "logged_out", "replayed" to "true") }, { cleared = true })
    assertTrue(cleared)
  }
  @Test fun nativeLogoutRestoresSessionBeforeRevokingAndOnlyThenClearsVault() {
    val restored = AndroidAuthSessionData("user-1", "tenant-1", "workspace-1", "session-1", "device-1",
      "synthetic-access", "", Instant.parse("2026-10-13T01:00:00Z"))
    val order = mutableListOf<String>()
    val status = AndroidAuthFlow.logoutWithCurrent(
      { order += "restore"; restored }, { true },
      { session ->
        assertEquals("synthetic-access", session.access)
        order += "revoke"
        mapOf("status" to "logged_out", "replayed" to "false")
      }, { order += "clear" })
    assertEquals("signed_out", status)
    assertEquals(listOf("restore", "revoke", "clear"), order)
  }
  @Test fun nativeLogoutAfterRefreshUnauthorizedIsSignedOutWithoutServerRevoke() {
    var revoked = false
    var cleared = false
    val status = AndroidAuthFlow.logoutWithCurrent(
      { cleared = true; null }, { false },
      { revoked = true; mapOf("status" to "logged_out", "replayed" to "false") },
      { cleared = true })
    assertEquals("signed_out", status)
    assertFalse(revoked)
    assertTrue(cleared)
  }
  @Test fun nativeLogoutKeepsVaultWhenSessionRestoreOrServerRevokeFails() {
    var cleared = false
    assertThrows(Exception::class.java) {
      AndroidAuthFlow.logoutWithCurrent(
        { throw IllegalStateException("NETWORK_UNAVAILABLE") }, { true },
        { mapOf("status" to "logged_out", "replayed" to "false") }, { cleared = true })
    }
    assertFalse(cleared)
    val restored = AndroidAuthSessionData("user-1", "tenant-1", "workspace-1", "session-1", "device-1",
      "synthetic-access", "", Instant.parse("2026-10-13T01:00:00Z"))
    assertThrows(Exception::class.java) {
      AndroidAuthFlow.logoutWithCurrent(
        { restored }, { true }, { throw IllegalStateException("REVOKE_FAILED") }, { cleared = true })
    }
    assertFalse(cleared)
  }
  @Test fun transientRefreshFailureMustNotEraseServerBackedSession() {
    assertFalse(AndroidAuthFlow.clearAfterRefreshFailure(null))
    assertFalse(AndroidAuthFlow.clearAfterRefreshFailure(503))
    assertTrue(AndroidAuthFlow.clearAfterRefreshFailure(401))
    assertTrue(AndroidAuthFlow.clearAfterRefreshFailure(403))
  }
  @Test fun tenantSwitchRequiresRotatedCredentialsAndPreservesSameTenantSession() {
    val prior = AndroidAuthSessionData("user-1", "personal-1", "workspace-1", "session-1", "device-1",
      "old-access", "", Instant.parse("2026-10-12T01:00:00Z"))
    val rotated = mapOf("user_id" to "user-1", "tenant_id" to "organization-1",
      "workspace_id" to "workspace-2", "session_id" to "session-2", "device_id" to "device-2",
      "client_kind" to "native", "access_credential" to "new-access",
      "refresh_credential" to "new-refresh", "expires_at" to "2026-10-12T02:00:00Z")
    val next = AndroidAuthFlow.switchedSession(prior, "organization-1", rotated)
    assertEquals("new-access", next.access)
    assertEquals("new-refresh", next.refresh)
    assertEquals("session-2", next.sessionId)
    assertThrows(Exception::class.java) {
      AndroidAuthFlow.switchedSession(prior, "organization-1", rotated - "refresh_credential")
    }
    assertThrows(Exception::class.java) {
      AndroidAuthFlow.switchedSession(prior, "organization-1", rotated + ("user_id" to "other-user"))
    }
    val same = mapOf("user_id" to "user-1", "tenant_id" to "personal-1",
      "workspace_id" to "workspace-1", "session_id" to "session-1", "device_id" to "device-1",
      "client_kind" to "native", "expires_at" to "2026-10-12T01:00:00Z")
    assertEquals(prior, AndroidAuthFlow.switchedSession(prior, "personal-1", same))
    assertThrows(Exception::class.java) {
      AndroidAuthFlow.switchedSession(prior, "organization-1", same)
    }
  }
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
    assertEquals("workspace_unavailable", AndroidAuthFlow.workspaceFailure(404))
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
