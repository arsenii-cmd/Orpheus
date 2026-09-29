package dev.arco.orpheus.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import dev.arco.orpheus.Phase
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin
import kotlin.random.Random

private class Star(val x: Float, val y: Float, val size: Float, val speed: Float, val twinkle: Float, val depth: Float)

/**
 * The sky behind everything: a deep night, an aurora tinted by what Orpheus is doing,
 * and stars drifting upwards at three depths. It brightens while Orpheus is awake.
 */
@Composable
fun Backdrop(phase: Phase, modifier: Modifier = Modifier, dim: Float = 0f) {
    val time by frameTime()
    val primary by animateColorAsState(phase.color(), tween(1400), label = "sky1")
    val secondary by animateColorAsState(phase.accent(), tween(1800), label = "sky2")
    val energy by animateFloatAsState(
        when (phase) {
            Phase.Off -> 0.25f
            Phase.Wake -> 0.6f
            Phase.Thinking, Phase.Speaking -> 1f
            else -> 0.85f
        } * (1f - dim),
        tween(1600), label = "energy",
    )
    val stars = remember {
        val r = Random(7)
        List(110) {
            val depth = r.nextFloat()
            Star(r.nextFloat(), r.nextFloat(), 0.6f + depth * 1.8f, 0.004f + depth * 0.012f, r.nextFloat() * 6.28f, depth)
        }
    }

    Canvas(modifier) {
        val t = time
        drawRect(Brush.verticalGradient(listOf(Palette.Night, Palette.Deep, Palette.Night)))
        aurora(t, primary, secondary, energy)
        for (s in stars) {
            val y = ((s.y - t * s.speed) % 1f + 1f) % 1f
            val x = s.x + sin(t * 0.05f + s.twinkle) * 0.004f * (1f + s.depth)
            val tw = 0.35f + 0.65f * (0.5f + 0.5f * sin(t * (0.6f + s.depth) + s.twinkle))
            drawCircle(
                Color.White.copy(alpha = (0.15f + 0.55f * s.depth) * tw * (0.5f + 0.5f * energy)),
                radius = s.size * density * 0.6f,
                center = Offset(x * size.width, y * size.height),
            )
        }
        // a vignette pulls the eye to the middle
        drawRect(
            Brush.radialGradient(
                listOf(Color.Transparent, Palette.Night.copy(alpha = 0.75f)),
                center = Offset(size.width / 2, size.height * 0.38f), radius = size.maxDimension * 0.8f,
            ),
        )
    }
}

/** Four soft blobs wandering on slow Lissajous paths. */
private fun DrawScope.aurora(t: Float, a: Color, b: Color, energy: Float) {
    val w = size.width
    val h = size.height
    val blobs = listOf(
        Triple(0.30f, 0.22f, a), Triple(0.75f, 0.30f, b),
        Triple(0.20f, 0.70f, b), Triple(0.80f, 0.78f, a),
    )
    blobs.forEachIndexed { i, (bx, by, color) ->
        val phase = i * PI.toFloat() / 2
        val cx = w * (bx + 0.12f * sin(t * 0.07f + phase))
        val cy = h * (by + 0.08f * cos(t * 0.05f + phase * 1.3f))
        val r = w * (0.75f + 0.12f * sin(t * 0.11f + phase))
        drawCircle(
            Brush.radialGradient(listOf(color.copy(alpha = 0.22f * energy), Color.Transparent), Offset(cx, cy), r),
            radius = r, center = Offset(cx, cy),
        )
    }
}
