package dev.arco.orpheus

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Keeps the key of "Личное" wrapped by a key that lives in the Android Keystore and never leaves
 * it: what the settings store on disk is useless without this very phone. The plain key exists
 * only in memory, to be handed to the server in "hello" over the encrypted connection.
 */
object KeyVault {
    private const val ALIAS = "orpheus_personal_wrap"

    private fun wrappingKey(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(
                KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build()
            )
        }.generateKey()
    }

    fun wrap(plain: String): String {
        if (plain.isBlank()) return ""
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, wrappingKey()) }
        val sealed = cipher.iv + cipher.doFinal(plain.toByteArray())
        return Base64.encodeToString(sealed, Base64.NO_WRAP)
    }

    /** Empty when there is nothing stored or it cannot be opened (e.g. the app was restored elsewhere). */
    fun unwrap(wrapped: String): String {
        if (wrapped.isBlank()) return ""
        return try {
            val sealed = Base64.decode(wrapped, Base64.NO_WRAP)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
                init(Cipher.DECRYPT_MODE, wrappingKey(), GCMParameterSpec(128, sealed, 0, 12))
            }
            String(cipher.doFinal(sealed, 12, sealed.size - 12))
        } catch (e: Exception) {
            ""
        }
    }

    /** A key of "Личное" is 32 bytes in base64 (see orpheus/secure.py). */
    fun isValid(key: String) = try {
        Base64.decode(key.trim(), Base64.NO_WRAP).size == 32
    } catch (e: IllegalArgumentException) {
        false
    }
}
