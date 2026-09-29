package dev.arco.orpheus.ui

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.geometry.Offset
import androidx.compose.animation.core.animateFloatAsState
import kotlinx.coroutines.launch

/**
 * Seconds since this composable appeared, advanced every frame. Read it only inside draw
 * lambdas (Canvas, drawBehind, graphicsLayer {}): then the frame redraws without recomposing.
 * With reduced motion the clock stands still.
 */
@Composable
fun frameTime(): State<Float> {
    val time = remember { mutableFloatStateOf(0f) }
    val reduced = LocalReducedMotion.current
    LaunchedEffect(reduced) {
        if (reduced) return@LaunchedEffect
        val start = withFrameNanos { it }
        while (true) {
            withFrameNanos { now -> time.floatValue = (now - start) / 1e9f }
        }
    }
    return time
}

/** Waits [ms] on the frame clock (not the wall clock), so staggers stay in step with the animations. */
suspend fun frameDelay(ms: Long) {
    val start = withFrameNanos { it }
    while (withFrameNanos { it } - start < ms * 1_000_000) Unit
}

fun lerp(a: Float, b: Float, t: Float) = a + (b - a) * t

/** 0 → 1 → 0 over [0, 1]: a soft bump for things that flare and fade. */
fun bump(t: Float) = if (t <= 0f || t >= 1f) 0f else 4f * t * (1f - t)

/** Springs used everywhere, so everything in the app moves with the same weight. */
object Motion {
    fun <T> soft() = spring<T>(dampingRatio = 0.78f, stiffness = 140f)
    fun <T> snappy() = spring<T>(dampingRatio = 0.62f, stiffness = Spring.StiffnessMediumLow)
    fun <T> bouncy() = spring<T>(dampingRatio = 0.5f, stiffness = 260f)
}

/**
 * A label that changes letter by letter: the old one lifts away, the new one rises in
 * with a small stagger between letters, like kinetic type in a title sequence.
 */
@Composable
fun KineticText(text: String, style: TextStyle, color: Color, modifier: Modifier = Modifier) {
    AnimatedContent(
        targetState = text,
        transitionSpec = {
            (fadeIn(tween(1)) togetherWith
                (fadeOut(tween(220)) + slideOutVertically(tween(260)) { -it / 2 }))
        },
        contentAlignment = Alignment.Center,
        modifier = modifier,
        label = "kinetic",
    ) { value ->
        Row(verticalAlignment = Alignment.CenterVertically) {
            value.forEachIndexed { i, ch ->
                val progress = remember(value) { Animatable(0f) }
                LaunchedEffect(value) {
                    frameDelay(90L + i * 22L)
                    progress.animateTo(1f, Motion.snappy())
                }
                Text(
                    ch.toString(), style = style, color = color,
                    modifier = Modifier.graphicsLayer {
                        val p = progress.value
                        alpha = p.coerceIn(0f, 1f)
                        translationY = (1f - p) * 22f * density
                        rotationX = (1f - p) * 70f
                        scaleX = 0.85f + 0.15f * p
                        scaleY = 0.85f + 0.15f * p
                    },
                )
            }
        }
    }
}

/** Text with a band of light sweeping across it, for the hint that invites the first word. */
@OptIn(ExperimentalTextApi::class)
@Composable
fun ShimmerText(text: String, style: TextStyle, base: Color, glint: Color, modifier: Modifier = Modifier) {
    val time by frameTime()
    val reduced = LocalReducedMotion.current
    Text(
        text,
        modifier = modifier,
        style = if (reduced) style.copy(color = base) else style.copy(
            brush = run {
                val phase = (time * 0.35f) % 1f
                val x = lerp(-400f, 1400f, phase)
                Brush.linearGradient(
                    listOf(base, base, glint, base, base),
                    start = Offset(x - 300f, 0f), end = Offset(x + 300f, 60f),
                )
            },
        ),
    )
}

/** Squashes a little under the finger and springs back: every button in the app does it. */
fun Modifier.pressScale(interaction: MutableInteractionSource, pressed: Float = 0.93f): Modifier = composed {
    val isPressed by interaction.collectIsPressedAsState()
    val scale by animateFloatAsState(if (isPressed) pressed else 1f, Motion.bouncy(), label = "press")
    graphicsLayer { scaleX = scale; scaleY = scale }
}

/** 0 → 1 once, [delayMs] after the first composition: one step of a staggered entrance. */
@Composable
fun rememberEntrance(key: Any?, delayMs: Long, enabled: Boolean = true): Animatable<Float, *> {
    val reduced = LocalReducedMotion.current
    val a = remember(key) { Animatable(if (enabled && !reduced) 0f else 1f) }
    LaunchedEffect(key) {
        if (a.value < 1f) launch {
            frameDelay(delayMs)
            a.animateTo(1f, Motion.soft())
        }
    }
    return a
}
