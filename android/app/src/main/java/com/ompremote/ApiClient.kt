package com.ompremote

import android.util.Base64
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import javax.net.ssl.HttpsURLConnection

class ApiClient {
    companion object {
        const val MAX_UPLOAD_BYTES = 10L * 1024L * 1024L
    }

    data class Result(val code: Int, val body: String, val bytes: ByteArray = body.toByteArray())
    private val idPattern = Regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    fun health(serverUrl: String): Result = request(serverUrl, "/api/v1/health", "GET", null, null)

    fun listJobs(serverUrl: String, token: String): Result =
        request(serverUrl, "/api/v1/jobs", "GET", token, null)
    fun getJob(serverUrl: String, token: String, jobId: String): Result =
        request(serverUrl, "/api/v1/jobs/${requireId(jobId)}", "GET", token, null)
    fun listConversations(serverUrl: String, token: String): Result =
        request(serverUrl, "/api/v1/conversations", "GET", token, null)

    fun createConversation(serverUrl: String, token: String, prompt: String): Result {
        val body = JSONObject().apply {
            put("prompt", prompt)
        }.toString()
        return request(serverUrl, "/api/v1/conversations", "POST", token, body)
    }

    fun getConversation(serverUrl: String, token: String, conversationId: String): Result =
        request(serverUrl, "/api/v1/conversations/${requireId(conversationId)}", "GET", token, null)

    fun addConversationMessage(
        serverUrl: String,
        token: String,
        conversationId: String,
        prompt: String,
    ): Result {
        val body = JSONObject().apply {
            put("prompt", prompt)
        }.toString()
        return request(
            serverUrl,
            "/api/v1/conversations/${requireId(conversationId)}/messages",
            "POST",
            token,
            body,
        )
    }

    fun createJob(serverUrl: String, token: String, prompt: String): Result {
        val body = JSONObject().apply {
            put("runner_id", "omp")
            put("prompt", prompt)
        }.toString()
        return request(serverUrl, "/api/v1/jobs", "POST", token, body)
    }
    fun cancelJob(serverUrl: String, token: String, jobId: String): Result =
        request(serverUrl, "/api/v1/jobs/${requireId(jobId)}/cancel", "POST", token, "{}")
    fun uploadInput(serverUrl: String, token: String, jobId: String, name: String, content: ByteArray): Result {
        require(content.size.toLong() <= MAX_UPLOAD_BYTES) { "file exceeds 10 MiB client limit" }
        val body = JSONObject().apply {
            put("name", name)
            put("content_base64", Base64.encodeToString(content, Base64.NO_WRAP))
        }.toString()
        return request(serverUrl, "/api/v1/jobs/${requireId(jobId)}/files", "POST", token, body)
    }
    fun logs(serverUrl: String, token: String, jobId: String): Result =
        request(serverUrl, "/api/v1/jobs/${requireId(jobId)}/logs", "GET", token, null)

    fun files(serverUrl: String, token: String, jobId: String): Result =
        request(serverUrl, "/api/v1/jobs/${requireId(jobId)}/files", "GET", token, null)

    fun downloadFile(serverUrl: String, token: String, jobId: String, fileId: String): Result =
        request(serverUrl, "/api/v1/jobs/${requireId(jobId)}/files/${requireId(fileId)}", "GET", token, null, binary = true)

    fun emergencyStatus(serverUrl: String, emergencyToken: String): Result =
        request(serverUrl, "/emergency/status", "GET", emergencyToken, null)

    fun emergencyKill(serverUrl: String, emergencyToken: String): Result =
        request(serverUrl, "/emergency/kill", "POST", emergencyToken, "{}")
    fun checkUpdate(serverUrl: String, token: String): Result =
        request(serverUrl, "/api/v1/app/update", "GET", token, null)

    fun downloadUpdate(serverUrl: String, token: String): Result =
        request(
            serverUrl,
            "/api/v1/app/update/download",
            "GET",
            token,
            null,
            binary = true,
        )

    private fun request(
        serverUrl: String,
        path: String,
        method: String,
        token: String?,
        body: String?,
        binary: Boolean = false,
    ): Result {
        val base = serverUrl.trimEnd('/')
        val url = URL("$base$path")
        if (url.protocol != "https") throw IOException("HTTPS is required")
        val connection = (url.openConnection() as HttpsURLConnection).apply {
            requestMethod = method
            connectTimeout = 10_000
            readTimeout = 30_000
            useCaches = false
            doInput = true
            if (!token.isNullOrBlank()) setRequestProperty("Authorization", "Bearer $token")
            if (body != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
            }
        }
        return try {
            if (body != null) connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val bytes = stream?.use { it.readBytes() } ?: ByteArray(0)
            Result(code, if (binary) "" else bytes.toString(Charsets.UTF_8), bytes)
        } finally {
            connection.disconnect()
        }
    }

    private fun requireId(value: String): String {
        require(idPattern.matches(value)) { "invalid server identifier" }
        return value
    }
}
