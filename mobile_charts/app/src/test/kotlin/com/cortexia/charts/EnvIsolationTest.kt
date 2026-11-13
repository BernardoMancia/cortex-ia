package com.cortexia.charts

import com.cortexia.charts.data.ChartsPrefs
import com.cortexia.charts.data.EnvTarget
import com.cortexia.charts.data.InMemoryKeyValueStore
import com.cortexia.charts.data.SecretCipher
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class EnvIsolationTest {

    private class JvmAesGcmCipher : SecretCipher {
        private val key: SecretKey = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        private val rng = SecureRandom()

        override fun encrypt(plain: String): String {
            val iv = ByteArray(12).also { rng.nextBytes(it) }
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(128, iv))
            val out = iv + cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
            return Base64.getEncoder().encodeToString(out)
        }

        override fun decrypt(encoded: String): String? = try {
            val raw = Base64.getDecoder().decode(encoded)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, raw, 0, 12))
            String(cipher.doFinal(raw, 12, raw.size - 12), Charsets.UTF_8)
        } catch (_: Exception) {
            null
        }
    }

    @Test
    fun productionAndHomologationCredentialsAndWalletsNeverCollide() {
        val store = InMemoryKeyValueStore()
        val prefs = ChartsPrefs(store = store, cipher = JvmAesGcmCipher())

        prefs.setUsername(EnvTarget.PRODUCTION, "luke_prod")
        prefs.setPassword(EnvTarget.PRODUCTION, "ProdSecret!1")
        prefs.setSessionToken(EnvTarget.PRODUCTION, "tok_prod_8003")
        prefs.setCsrfToken(EnvTarget.PRODUCTION, "csrf_prod_abc")
        prefs.setSelectedWalletId(EnvTarget.PRODUCTION, 10)

        prefs.setUsername(EnvTarget.HOMOLOGATION, "luke_homolog")
        prefs.setPassword(EnvTarget.HOMOLOGATION, "HomologSecret!2")
        prefs.setSessionToken(EnvTarget.HOMOLOGATION, "tok_homolog_9003")
        prefs.setCsrfToken(EnvTarget.HOMOLOGATION, "csrf_homolog_xyz")
        prefs.setSelectedWalletId(EnvTarget.HOMOLOGATION, 77)

        val rawProdToken = store.getString("prod_token_enc")
        val rawHomologToken = store.getString("homolog_token_enc")
        assertNotEquals("tok_prod_8003", rawProdToken)
        assertNotEquals("tok_homolog_9003", rawHomologToken)
        assertNotEquals(rawProdToken, rawHomologToken)

        assertEquals("luke_prod", prefs.username(EnvTarget.PRODUCTION))
        assertEquals("ProdSecret!1", prefs.password(EnvTarget.PRODUCTION))
        assertEquals("tok_prod_8003", prefs.sessionToken(EnvTarget.PRODUCTION))
        assertEquals("csrf_prod_abc", prefs.csrfToken(EnvTarget.PRODUCTION))
        assertEquals(10, prefs.selectedWalletId(EnvTarget.PRODUCTION))

        assertEquals("luke_homolog", prefs.username(EnvTarget.HOMOLOGATION))
        assertEquals("HomologSecret!2", prefs.password(EnvTarget.HOMOLOGATION))
        assertEquals("tok_homolog_9003", prefs.sessionToken(EnvTarget.HOMOLOGATION))
        assertEquals("csrf_homolog_xyz", prefs.csrfToken(EnvTarget.HOMOLOGATION))
        assertEquals(77, prefs.selectedWalletId(EnvTarget.HOMOLOGATION))

        prefs.logoutEnv(EnvTarget.HOMOLOGATION)

        assertFalse(prefs.isLoggedIn(EnvTarget.HOMOLOGATION))
        assertNull(prefs.sessionToken(EnvTarget.HOMOLOGATION))
        assertNull(prefs.password(EnvTarget.HOMOLOGATION))
        assertNull(prefs.csrfToken(EnvTarget.HOMOLOGATION))

        assertTrue(prefs.isLoggedIn(EnvTarget.PRODUCTION))
        assertEquals("tok_prod_8003", prefs.sessionToken(EnvTarget.PRODUCTION))
        assertEquals("ProdSecret!1", prefs.password(EnvTarget.PRODUCTION))
        assertEquals("csrf_prod_abc", prefs.csrfToken(EnvTarget.PRODUCTION))
        assertEquals(10, prefs.selectedWalletId(EnvTarget.PRODUCTION))
    }

    @Test
    fun environmentServerValidationBlocksCrossEnvironmentPayloads() {
        assertTrue(EnvTarget.PRODUCTION.matchesServerEnvironment("production"))
        assertTrue(EnvTarget.PRODUCTION.matchesServerEnvironment("PRODUCTION"))
        assertFalse(EnvTarget.PRODUCTION.matchesServerEnvironment("homologation"))
        assertFalse(EnvTarget.PRODUCTION.matchesServerEnvironment(null))

        assertTrue(EnvTarget.HOMOLOGATION.matchesServerEnvironment("homologation"))
        assertFalse(EnvTarget.HOMOLOGATION.matchesServerEnvironment("production"))
    }
}
