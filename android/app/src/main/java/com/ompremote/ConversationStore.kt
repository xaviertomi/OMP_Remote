package com.ompremote

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * Small durable cache for server-owned conversations.
 *
 * The server remains authoritative: entries are replaced with conversation
 * detail responses whenever they are available. This cache only keeps the
 * last known dialogue on screen while the device is offline or an activity is
 * recreated.
 */
class ConversationStore(context: Context, private val namespace: String = "default") {
    data class Message(
        val prompt: String,
        val jobId: String?,
        val state: String,
        val stdout: String,
        val stderr: String,
        val error: String? = null,
    )

    data class Conversation(
        val id: String,
        val title: String = "",
        val updatedAt: String = "",
        val messages: List<Message> = emptyList(),
    )

    private val preferences = context.getSharedPreferences(
        "omp_remote_conversations_${namespace.take(64)}",
        Context.MODE_PRIVATE,
    )

    @Synchronized
    fun all(): List<Conversation> {
        val raw = preferences.getString("conversations", "[]") ?: "[]"
        val array = try {
            JSONArray(raw)
        } catch (_: Exception) {
            JSONArray()
        }
        val result = mutableListOf<Conversation>()
        for (index in 0 until array.length()) {
            val item = array.optJSONObject(index) ?: continue
            val id = item.optString("id").trim()
            if (id.isBlank()) continue
            result += decode(item, id)
        }
        return result
    }

    @Synchronized
    fun get(id: String): Conversation? = all().firstOrNull { it.id == id }

    @Synchronized
    fun upsert(conversation: Conversation) {
        require(conversation.id.isNotBlank()) { "conversation id is required" }
        val entries = mutableListOf(conversation)
        entries += all().filterNot { it.id == conversation.id }
        save(entries.take(MAX_CONVERSATIONS))
    }

    /**
     * Merge a list endpoint summary without discarding a cached detail.
     */
    @Synchronized
    fun upsertSummary(id: String, title: String = "", updatedAt: String = "") {
        require(id.isNotBlank()) { "conversation id is required" }
        val existing = get(id)
        upsert(
            Conversation(
                id = id,
                title = title.ifBlank { existing?.title.orEmpty() },
                updatedAt = updatedAt.ifBlank { existing?.updatedAt.orEmpty() },
                messages = existing?.messages.orEmpty(),
            ),
        )
    }

    @Synchronized
    fun clear() {
        preferences.edit().remove("conversations").apply()
    }


    private fun save(entries: List<Conversation>) {
        val array = JSONArray()
        entries.forEach { conversation ->
            array.put(encode(conversation))
        }
        preferences.edit().putString("conversations", array.toString()).apply()
    }

    private fun encode(conversation: Conversation): JSONObject = JSONObject().apply {
        put("id", conversation.id)
        put("title", conversation.title)
        put("updated_at", conversation.updatedAt)
        put("messages", JSONArray().apply {
            conversation.messages.forEach { message ->
                put(JSONObject().apply {
                    put("prompt", message.prompt)
                    put("job_id", message.jobId ?: JSONObject.NULL)
                    put("state", message.state)
                    put("stdout", message.stdout)
                    put("stderr", message.stderr)
                    put("error", message.error ?: JSONObject.NULL)
                })
            }
        })
    }

    private fun decode(item: JSONObject, id: String): Conversation {
        val messagesArray = item.optJSONArray("messages") ?: JSONArray()
        val messages = buildList {
            for (index in 0 until messagesArray.length()) {
                val message = messagesArray.optJSONObject(index) ?: continue
                add(
                    Message(
                        prompt = message.optString("prompt"),
                        jobId = message.optString("job_id").takeIf { it.isNotBlank() && it != "null" },
                        state = message.optString("state", "unknown"),
                        stdout = message.optString("stdout"),
                        stderr = message.optString("stderr"),
                        error = message.optString("error").takeIf { it.isNotBlank() && it != "null" },
                    ),
                )
            }
        }
        return Conversation(
            id = id,
            title = item.optString("title"),
            updatedAt = item.optString("updated_at"),
            messages = messages,
        )
    }

    companion object {
        private const val MAX_CONVERSATIONS = 100
    }
}
