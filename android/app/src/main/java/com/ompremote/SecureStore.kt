package com.ompremote

import android.content.Context
import android.util.Base64
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class SecureStore(context: Context) {
    private val preferences = context.getSharedPreferences("omp_remote_secure", Context.MODE_PRIVATE)

    fun putNormalToken(token: String) = put("normal_token", "omp_remote_normal_key", token)
    fun getNormalToken(): String? = get("normal_token", "omp_remote_normal_key")
    fun putEmergencyToken(token: String) = put("emergency_token", "omp_remote_emergency_key", token)
    fun getEmergencyToken(): String? = get("emergency_token", "omp_remote_emergency_key")
    fun putServerUrl(url: String) = preferences.edit().putString("server_url", url).apply()
    fun getServerUrl(): String? = preferences.getString("server_url", null)
    fun clearNormalToken() = preferences.edit().remove("normal_token").apply()
    fun clearEmergencyToken() = preferences.edit().remove("emergency_token").apply()

    private fun put(pref: String, alias: String, value: String) {
        val key = key(alias)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key)
        val iv = cipher.iv
        val ciphertext = cipher.doFinal(value.toByteArray(StandardCharsets.UTF_8))
        val combined = ByteArray(iv.size + ciphertext.size)
        System.arraycopy(iv, 0, combined, 0, iv.size)
        System.arraycopy(ciphertext, 0, combined, iv.size, ciphertext.size)
        preferences.edit().putString(pref, Base64.encodeToString(combined, Base64.NO_WRAP)).apply()
    }

    private fun get(pref: String, alias: String): String? {
        val encoded = preferences.getString(pref, null) ?: return null
        return try {
            val combined = Base64.decode(encoded, Base64.NO_WRAP)
            val iv = combined.copyOfRange(0, 12)
            val ciphertext = combined.copyOfRange(12, combined.size)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(alias), GCMParameterSpec(128, iv))
            String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8)
        } catch (_: Exception) {
            null
        }
    }

    private fun key(alias: String): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = store.getKey(alias, null)
        if (existing is SecretKey) return existing
        val generator = KeyGenerator.getInstance("AES", "AndroidKeyStore")
        generator.init(android.security.keystore.KeyGenParameterSpec.Builder(
            alias,
            android.security.keystore.KeyProperties.PURPOSE_ENCRYPT or
                android.security.keystore.KeyProperties.PURPOSE_DECRYPT,
        ).setBlockModes(android.security.keystore.KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(android.security.keystore.KeyProperties.ENCRYPTION_PADDING_NONE)
            .build())
        return generator.generateKey()
    }
}
