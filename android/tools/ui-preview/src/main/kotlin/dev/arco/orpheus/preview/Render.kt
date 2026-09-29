package dev.arco.orpheus.preview

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.Snapshot
import androidx.compose.ui.ImageComposeScene
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.platform.Font
import androidx.compose.ui.unit.Density
import dev.arco.orpheus.Line
import dev.arco.orpheus.Link
import dev.arco.orpheus.Phase
import dev.arco.orpheus.Settings
import dev.arco.orpheus.VoiceEnroll
import dev.arco.orpheus.ui.Fonts
import dev.arco.orpheus.ui.MainScreen
import dev.arco.orpheus.ui.OrpheusTheme
import dev.arco.orpheus.ui.Screens
import dev.arco.orpheus.ui.SettingsScreen
import org.jetbrains.skia.EncodedImageFormat
import java.io.File
import kotlin.math.abs
import kotlin.math.sin

/**
 * Plays a scripted session through the real ui code and writes every frame to <out>/f00000.png…
 * Off → switched on → listening → thinking → a streamed reply → follow-up → settings and back.
 * Make a video of it with: ffmpeg -framerate 30 -i out/f%05d.png -pix_fmt yuv420p preview.mp4
 */
fun main(args: Array<String>) {
    val out = File(args[0]).apply { deleteRecursively(); mkdirs() }
    val fps = 30
    val seconds = (System.getenv("PREVIEW_SECONDS") ?: "24").toFloat()
    val fontDir = File("../../app/src/main/res/font")
    val fonts = Fonts(
        display = FontFamily(
            Font(File(fontDir, "unbounded_500.ttf"), FontWeight.Medium),
            Font(File(fontDir, "unbounded_700.ttf"), FontWeight.Bold),
        ),
        body = FontFamily(
            Font(File(fontDir, "manrope_400.ttf"), FontWeight.Normal),
            Font(File(fontDir, "manrope_600.ttf"), FontWeight.SemiBold),
        ),
    )

    var phase by mutableStateOf(Phase.Off)
    var link by mutableStateOf(Link.Off)
    var level by mutableFloatStateOf(0f)
    var lines by mutableStateOf(listOf<Line>())
    var inSettings by mutableStateOf(false)
    var settings by mutableStateOf(Settings())
    var problem by mutableStateOf<String?>(null)
    var enroll by mutableStateOf(VoiceEnroll())
    val scene = System.getenv("PREVIEW_SCENE") ?: "session"

    val reply = "Завтра воскресенье, 27 сентября. В 18:00 у тебя занятие по английскому, " +
        "а утром ничего не запланировано. Напомнить за час?"
    val replyWords = Regex("\\S+\\s*").findAll(reply).map { it.value }.toList()

    // PREVIEW_SCENE=enroll: settings → «Мой голос», five phrases read (one too short), the voice recorded
    val enrollScript = buildList<Pair<Float, () -> Unit>> {
        add(0.0f to { inSettings = true; phase = Phase.Wake; link = Link.Online })
        var t = 1.4f
        var count = 0
        for (i in 0 until 6) {
            val short = i == 2
            add(t to { enroll = enroll.copy(recording = true, error = null); phase = Phase.Listening })
            add(t + (if (short) 0.6f else 1.8f) to { phase = Phase.Thinking })
            add(t + (if (short) 0.9f else 2.2f) to {
                if (short) enroll = enroll.copy(recording = false, error = "слишком коротко, прочитай фразу целиком")
                else { count++; enroll = enroll.copy(count = count, recording = false, mode = "log") }
                phase = Phase.Wake
            })
            t += if (short) 1.8f else 3.0f
        }
    }
    val sessionScript = listOf<Pair<Float, () -> Unit>>(
        2.2f to { phase = Phase.Wake; link = Link.Connecting },
        3.4f to { link = Link.Online },
        5.0f to { phase = Phase.Listening },
        7.6f to { lines = lines + Line(true, "Орфей, что у меня завтра?") },
        7.8f to { phase = Phase.Thinking },
        10.2f to { phase = Phase.Speaking },
        15.4f to { phase = Phase.FollowUp },
        17.4f to { phase = Phase.Wake },
        18.2f to { inSettings = true },
        20.2f to { settings = settings.copy(voice = "female") },
        21.4f to { settings = settings.copy(followUp = false) },
        22.6f to { inSettings = false },
    ) + replyWords.mapIndexed { i, _ ->
        (10.4f + i * 0.22f) to {
            val text = replyWords.take(i + 1).joinToString("")
            lines = if (i == 0) lines + Line(false, text) else lines.dropLast(1) + lines.last().copy(text = text)
        }
    }

    // the size is in pixels: at density 2 that is a 400 × 860 dp phone
    val script = if (scene == "enroll") enrollScript else sessionScript
    val composeScene = ImageComposeScene(800, 1720, Density(2f)) {
        OrpheusTheme(fonts) {
            Screens(
                inSettings,
                main = { MainScreen(phase, link, level, lines, problem, true, {}, {}, {}, {}) },
                settings = { SettingsScreen(settings, {}, {}, enroll, phase, level) },
            )
        }
    }
    val pending = script.sortedBy { it.first }.toMutableList()
    val frames = (seconds * fps).toInt()
    for (f in 0 until frames) {
        val t = f.toFloat() / fps
        while (pending.isNotEmpty() && pending.first().first <= t) pending.removeAt(0).second()
        // a voice: syllables while speaking into the phone, silence around it
        level = if (scene == "enroll" && phase == Phase.Listening ||
            phase == Phase.Listening && t in 5.4f..7.5f || phase == Phase.FollowUp && t in 16.0f..16.8f)
            (0.2f + 0.8f * abs(sin(t * 7.3f) * sin(t * 2.2f + 0.4f))).coerceIn(0f, 1f) else 0f
        Snapshot.sendApplyNotifications()
        val image = composeScene.render(f * 1_000_000_000L / fps)
        File(out, "f%05d.png".format(f)).writeBytes(image.encodeToData(EncodedImageFormat.PNG)!!.bytes)
    }
    composeScene.close()
    println("$frames frames in $out")
}
