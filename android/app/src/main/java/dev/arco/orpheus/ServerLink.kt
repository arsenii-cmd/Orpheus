package dev.arco.orpheus

import android.os.Build
import android.os.Handler
import android.os.Looper
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.util.concurrent.TimeUnit

/**
 * One WebSocket to the Orpheus server, kept open while the assistant is on and reopened with
 * backoff when it drops. Messages come back on the main thread.
 */
class ServerLink(
    private val onMessage: (ServerMessage) -> Unit,
    private val onAudio: (ByteArray) -> Unit,
    private val onDropped: () -> Unit,
) {
    private val client = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private val main = Handler(Looper.getMainLooper())
    private var socket: WebSocket? = null
    private var url = ""
    private var token = ""
    private var wanted = false
    private var attempt = 0

    val online get() = Bus.link.value == Link.Online

    private var personalKey = ""

    fun connect(url: String, token: String, personalKey: String = "") {
        val changed = url != this.url || token != this.token || personalKey != this.personalKey
        this.url = url
        this.token = token
        this.personalKey = personalKey
        wanted = true
        if (url.isBlank()) {
            close()
            wanted = true
            Bus.link.value = Link.NoServer
            return
        }
        if (changed || socket == null) {
            socket?.close(1000, null)
            open()
        }
    }

    fun close() {
        wanted = false
        main.removeCallbacksAndMessages(null)
        socket?.close(1000, null)
        socket = null
        Bus.link.value = Link.Off
    }

    fun send(text: String) = socket?.send(text) ?: false

    fun send(audio: ByteArray) = socket?.send(audio.toByteString()) ?: false

    private fun open() {
        Bus.link.value = Link.Connecting
        val request = try {
            Request.Builder().url(url).apply {
                if (token.isNotBlank()) header("Authorization", "Bearer $token")
            }.build()
        } catch (e: IllegalArgumentException) {
            Bus.link.value = Link.NoServer
            Bus.problem.value = "Неверный адрес сервера: $url"
            return
        }
        socket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) = main.post {
                if (webSocket != socket) return@post
                attempt = 0
                Bus.link.value = Link.Online
                if (Bus.problem.value?.startsWith("Сервер не пустил") == true) Bus.problem.value = null
                webSocket.send(Protocol.hello("${Build.MANUFACTURER} ${Build.MODEL}", personalKey))
            }.let {}

            override fun onMessage(webSocket: WebSocket, text: String) = main.post {
                if (webSocket == socket) onMessage(Protocol.parse(text))
            }.let {}

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                val data = bytes.toByteArray()
                main.post { if (webSocket == socket) onAudio(data) }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = dropped(webSocket)

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                // The proxy does not say 401 to a wrong token: it serves its ordinary site instead
                // (200 text/html on "/", 404 elsewhere); the laptop itself says 401/403.
                // 502/504 mean the laptop is unreachable behind the proxy: just retry.
                val denied = response?.code in setOf(200, 401, 403, 404)
                response?.close()
                dropped(webSocket, denied)
            }
        })
    }

    private fun dropped(webSocket: WebSocket, denied: Boolean = false) {
        main.post {
            if (webSocket != socket) return@post
            socket = null
            onDropped()
            if (!wanted) return@post
            if (denied) {
                Bus.link.value = Link.Denied
                Bus.problem.value = "Сервер не пустил: нет доступа (проверь токен)"
            } else {
                Bus.link.value = Link.Connecting
            }
            // mobile networks drop often: retry soon, then settle at 10 s (a wrong token: every 30 s)
            val delay = if (denied) 30_000L else RETRY_MS[attempt.coerceAtMost(RETRY_MS.lastIndex)]
            attempt++
            main.postDelayed({ if (wanted && socket == null) open() }, delay)
        }
    }

    private companion object {
        val RETRY_MS = longArrayOf(1_000, 2_000, 5_000, 10_000)
    }
}
