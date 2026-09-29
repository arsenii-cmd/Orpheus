package dev.arco.orpheus.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathMeasure
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.lerp
import dev.arco.orpheus.Phase
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin

private const val TAU = (2 * PI).toFloat()

/** What the orb looks like in a phase. Every field is animated with a spring, so phases morph. */
private data class Look(
    val size: Float, val wobble: Float, val glow: Float,
    val lyre: Float, val petals: Float, val orbit: Float, val halo: Float, val spin: Float,
)

private fun Phase.look() = when (this) {
    Phase.Off -> Look(0.52f, 0.012f, 0.14f, 0f, 0f, 0f, 0f, 0.05f)
    Phase.Wake -> Look(0.78f, 0.035f, 0.6f, 0f, 0f, 0f, 1f, 0.12f)
    Phase.Listening -> Look(0.84f, 0.05f, 0.85f, 0f, 1f, 0f, 0f, 0.2f)
    Phase.Thinking -> Look(0.70f, 0.065f, 0.75f, 0f, 0f, 1f, 0f, 0.9f)
    Phase.Speaking -> Look(0.80f, 0.06f, 0.95f, 1f, 0f, 0f, 0f, 0.25f)
    Phase.FollowUp -> Look(0.80f, 0.045f, 0.75f, 0f, 0.7f, 0f, 0.5f, 0.18f)
    Phase.Paused -> Look(0.58f, 0.015f, 0.2f, 0f, 0f, 0f, 0f, 0.05f)
}

/**
 * The orb in the middle of the screen, a living lyre of light:
 * - Off: a dim ember;
 * - Wake: breathes slowly inside a shimmering halo;
 * - Listening: spectrum petals grow out of it with the voice, and the body swells;
 * - Thinking: particles race around it on tilted orbits, passing behind and in front;
 * - Speaking: Orpheus' lyre appears around it, its strings vibrating, rings spreading out;
 * - FollowUp: petals and halo together, calmer.
 * Each phase change sends a shockwave out of the orb.
 */
@Composable
fun Orb(phase: Phase, level: Float, modifier: Modifier = Modifier) {
    val time by frameTime()
    val reduced = LocalReducedMotion.current
    val look = phase.look()
    val primary by animateColorAsState(phase.color(), tween(700), label = "primary")
    val secondary by animateColorAsState(phase.accent(), tween(900), label = "secondary")
    val orbSize by animateFloatAsState(look.size, Motion.bouncy(), label = "size")
    val wobble by animateFloatAsState(look.wobble, Motion.soft(), label = "wobble")
    val glowSpring by animateFloatAsState(look.glow, Motion.soft(), label = "glow")
    val lyreSpring by animateFloatAsState(look.lyre, Motion.soft(), label = "lyre")
    val petalsSpring by animateFloatAsState(look.petals, Motion.soft(), label = "petals")
    val orbitSpring by animateFloatAsState(look.orbit, Motion.soft(), label = "orbit")
    val haloSpring by animateFloatAsState(look.halo, Motion.soft(), label = "halo")
    val spinSpeed by animateFloatAsState(look.spin, Motion.soft(), label = "spin")
    // springs overshoot a little; these are amounts of light, so they stay within 0…1
    val glow = glowSpring.coerceIn(0f, 1f)
    val lyre = lyreSpring.coerceIn(0f, 1f)
    val petals = petalsSpring.coerceIn(0f, 1f)
    val orbit = orbitSpring.coerceIn(0f, 1f)
    val halo = haloSpring.coerceIn(0f, 1f)

    // The voice level, smoothed, and its recent history for the spectrum petals.
    val history = remember { FloatArray(48) }
    var voice by remember { mutableFloatStateOf(0f) }
    var angle by remember { mutableFloatStateOf(0f) }
    val currentLevel by rememberUpdatedState(if (phase == Phase.Listening || phase == Phase.FollowUp) level else 0f)
    val currentSpin by rememberUpdatedState(spinSpeed)
    LaunchedEffect(reduced) {
        if (reduced) return@LaunchedEffect
        var last = withFrameNanos { it }
        while (true) {
            withFrameNanos { now ->
                val dt = ((now - last) / 1e9f).coerceIn(0f, 0.1f)
                last = now
                val target = currentLevel.coerceIn(0f, 1f)
                voice += (target - voice) * (if (target > voice) 0.45f else 0.12f)
                System.arraycopy(history, 0, history, 1, history.size - 1)
                history[0] = voice
                angle = (angle + currentSpin * dt) % TAU
            }
        }
    }

    // A shockwave on each phase change (not on the first frame).
    var shockAt by remember { mutableFloatStateOf(-10f) }
    val firstPhase = remember { phase }
    LaunchedEffect(phase) {
        if (phase != firstPhase || shockAt > -10f) shockAt = time
    }

    Canvas(modifier) {
        val t = time
        val c = center
        val r = min(size.width, size.height) / 2f * 0.46f
        val body = r * orbSize
        val active = phase != Phase.Off

        // glow
        drawCircle(
            Brush.radialGradient(
                listOf(primary.copy(alpha = 0.55f * glow), secondary.copy(alpha = 0.18f * glow), Color.Transparent),
                c, r * 2.5f,
            ),
            r * 2.5f, c,
        )
        if (halo > 0.01f) halo(c, body, t, primary, secondary, halo)
        if (orbit > 0.01f) orbit(c, r, t, primary, secondary, orbit, behind = true)
        if (petals > 0.01f) petals(c, body, t, history, primary, secondary, petals)
        if (lyre > 0.01f) ripples(c, body, t, primary, lyre)

        liquidBody(c, body, t, angle, wobble, voice * petals, primary, secondary, active)

        if (orbit > 0.01f) orbit(c, r, t, primary, secondary, orbit, behind = false)
        if (lyre > 0.01f) lyre(c, r, t, primary, secondary, lyre)

        val age = t - shockAt
        if (age in 0f..1.3f) {
            val k = age / 1.3f
            val a = (1f - k).pow(2) * 0.75f
            val rr = body * (1f + k * 1.9f)
            drawCircle(primary.copy(alpha = a * 0.25f), rr, c, style = Stroke(width = 18f * density * (1f - k)))
            drawCircle(Color.White.copy(alpha = a), rr, c, style = Stroke(width = 1.6f * density))
        }
    }
}

/** A closed blob: a circle whose radius wanders with three sine "noises" and the voice. */
private fun DrawScope.liquidBody(
    c: Offset, radius: Float, t: Float, angle: Float, wobble: Float, voice: Float,
    primary: Color, secondary: Color, active: Boolean,
) {
    val n = 160
    val path = Path()
    for (i in 0..n) {
        val th = i.toFloat() / n * TAU
        val noise = sin(3 * th + t * 1.1f) * 0.5f + sin(5 * th - t * 1.6f + 1f) * 0.3f + sin(8 * th + t * 2.3f) * 0.2f
        val pulse = voice * (0.18f + 0.1f * sin(6 * th - t * 9f))
        val rr = radius * (1f + wobble * noise + pulse)
        val x = c.x + rr * cos(th + angle)
        val y = c.y + rr * sin(th + angle)
        if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
    }
    path.close()
    val light = Offset(c.x - radius * 0.3f + radius * 0.08f * sin(t * 0.7f), c.y - radius * 0.36f)
    drawPath(
        path,
        Brush.radialGradient(
            listOf(
                Color.White.copy(alpha = if (active) 0.95f else 0.35f),
                lerp(primary, Color.White, 0.25f),
                primary,
                secondary.copy(alpha = 0.9f),
                secondary.copy(alpha = 0.55f),
            ),
            light, radius * 1.9f,
        ),
    )
    // an iridescent sheen turning slowly inside
    rotate(t * 25f, c) {
        drawPath(
            path,
            Brush.sweepGradient(
                listOf(Color.Transparent, Color.White.copy(alpha = 0.16f), Color.Transparent, secondary.copy(alpha = 0.3f), Color.Transparent),
                c,
            ),
        )
    }
    drawPath(path, Color.White.copy(alpha = if (active) 0.35f else 0.12f), style = Stroke(width = 1.2f * density))
}

/** Wake: a thin ring breathing around the orb, two sparks of light running along it. */
private fun DrawScope.halo(c: Offset, radius: Float, t: Float, primary: Color, secondary: Color, amount: Float) {
    val rr = radius * (1.32f + 0.05f * sin(t * 1.6f))
    drawCircle(primary.copy(alpha = 0.35f * amount), rr, c, style = Stroke(width = 1.4f * density))
    rotate(t * 40f, c) {
        drawCircle(
            Brush.sweepGradient(
                listOf(Color.Transparent, Color.White.copy(alpha = 0.9f * amount), Color.Transparent, Color.Transparent,
                    secondary.copy(alpha = 0.8f * amount), Color.Transparent),
                c,
            ),
            rr, c, style = Stroke(width = 2.6f * density, cap = StrokeCap.Round),
        )
    }
    drawCircle(primary.copy(alpha = 0.08f * amount), rr * 1.18f, c, style = Stroke(width = 22f * density))
}

/** Listening: rays around the orb, their length the voice a moment ago, mirrored left and right. */
private fun DrawScope.petals(
    c: Offset, radius: Float, t: Float, history: FloatArray,
    primary: Color, secondary: Color, amount: Float,
) {
    val count = 72
    val inner = radius * 1.14f
    for (i in 0 until count) {
        val th = i.toFloat() / count * TAU - PI.toFloat() / 2
        val fromTop = min(i, count - i).toFloat() / (count / 2)          // 0 at the top, 1 at the bottom
        val v = history[(fromTop * (history.size - 1)).toInt()]
        val idle = 0.04f + 0.025f * sin(t * 2f + i * 0.6f)
        val len = radius * (idle + v * 0.85f) * amount
        val a = Offset(c.x + inner * cos(th), c.y + inner * sin(th))
        val b = Offset(c.x + (inner + len) * cos(th), c.y + (inner + len) * sin(th))
        val col = lerp(primary, secondary, (v * 1.4f).coerceIn(0f, 1f))
        drawLine(col.copy(alpha = 0.18f * amount), a, b, strokeWidth = 7f * density, cap = StrokeCap.Round)
        drawLine(col.copy(alpha = 0.9f * amount), a, b, strokeWidth = 2.2f * density, cap = StrokeCap.Round)
    }
}

/**
 * Thinking: three tilted orbits of comets. Seen edge-on, half of each orbit is behind the orb:
 * that half is drawn before the body ([behind] = true), the other after it.
 */
private fun DrawScope.orbit(
    c: Offset, r: Float, t: Float, primary: Color, secondary: Color, amount: Float, behind: Boolean,
) {
    val rings = listOf(
        Triple(1.35f, 20f, 1.7f), Triple(1.62f, -38f, -1.15f), Triple(1.9f, 64f, 0.8f),
    )
    val counts = intArrayOf(4, 6, 8)
    rings.forEachIndexed { ring, (scale, tiltDeg, speed) ->
        val tilt = tiltDeg / 180f * PI.toFloat()
        val rx = r * scale * (0.85f + 0.15f * amount)
        val ry = rx * 0.34f
        for (p in 0 until counts[ring]) {
            val head = t * speed + p * TAU / counts[ring] + ring
            val trail = 26
            for (k in 0 until trail) {
                val a = head - k * 0.032f * (if (speed > 0) 1 else -1)
                val depth = sin(a)                                    // > 0: in front
                if ((depth < 0) != behind) continue
                val ex = rx * cos(a)
                val ey = ry * sin(a)
                val x = c.x + ex * cos(tilt) - ey * sin(tilt)
                val y = c.y + ex * sin(tilt) + ey * cos(tilt)
                val fade = (1f - k.toFloat() / trail).pow(1.4f)
                val near = 0.55f + 0.45f * (depth + 1f) / 2f
                val alpha = amount * near * (if (behind) 0.45f else 1f)
                if (k == 0) {
                    drawCircle(primary.copy(alpha = 0.35f * alpha), radius = 7f * density * near, center = Offset(x, y))
                    drawCircle(Color.White.copy(alpha = alpha), radius = 2.6f * density * near, center = Offset(x, y))
                } else {
                    drawCircle(
                        lerp(primary, secondary, k.toFloat() / trail).copy(alpha = alpha * fade * 0.9f),
                        radius = density * 2.1f * fade * near + 0.3f,
                        center = Offset(x, y),
                    )
                }
            }
        }
        if (!behind) {
            drawOval(
                primary.copy(alpha = 0.07f * amount),
                topLeft = Offset(c.x - rx, c.y - ry), size = Size(rx * 2, ry * 2),
                style = Stroke(width = density),
            )
        }
    }
}

/** Speaking: rings spreading out of the orb, one every half second. */
private fun DrawScope.ripples(c: Offset, radius: Float, t: Float, color: Color, amount: Float) {
    for (k in 0..2) {
        val p = ((t * 0.7f) + k / 3f) % 1f
        drawCircle(
            color.copy(alpha = (1f - p).pow(1.5f) * 0.45f * amount),
            radius * (1.05f + p * 1.1f), c, style = Stroke(width = (3f - 2f * p) * density),
        )
    }
}

/**
 * Speaking: Orpheus' lyre drawn in light around the orb. The arms grow in along their length,
 * seven strings hang between the yoke and the base and vibrate as standing waves.
 */
private fun DrawScope.lyre(c: Offset, r: Float, t: Float, primary: Color, secondary: Color, amount: Float) {
    val gold = lerp(primary, Color.White, 0.2f)
    val stroke = 2.4f * density
    val baseY = c.y + r * 1.22f
    val yokeY = c.y - r * 1.12f
    for (side in listOf(-1f, 1f)) {
        val arm = Path().apply {
            moveTo(c.x + side * r * 0.42f, baseY)
            cubicTo(
                c.x + side * r * 1.55f, c.y + r * 0.9f,
                c.x + side * r * 1.05f, c.y - r * 0.6f,
                c.x + side * r * 1.2f, yokeY - r * 0.28f,
            )
            cubicTo(
                c.x + side * r * 1.25f, yokeY - r * 0.42f,
                c.x + side * r * 1.52f, yokeY - r * 0.36f,
                c.x + side * r * 1.46f, yokeY - r * 0.18f,
            )
        }
        val grown = trim(arm, amount)
        drawPath(grown, primary.copy(alpha = 0.22f * amount), style = Stroke(width = stroke * 5, cap = StrokeCap.Round))
        drawPath(grown, gold.copy(alpha = amount), style = Stroke(width = stroke, cap = StrokeCap.Round))
    }
    val yokeHalf = r * 1.12f * amount
    drawLine(gold.copy(alpha = amount), Offset(c.x - yokeHalf, yokeY), Offset(c.x + yokeHalf, yokeY), stroke, StrokeCap.Round)
    val baseHalf = r * 0.5f * amount
    drawLine(gold.copy(alpha = amount), Offset(c.x - baseHalf, baseY), Offset(c.x + baseHalf, baseY), stroke * 1.4f, StrokeCap.Round)

    // A speaking voice has syllables: an envelope from two slow sines, never quite still.
    val envelope = 0.35f + 0.65f * abs(sin(t * 5.3f) * sin(t * 1.7f + 0.5f))
    val strings = 7
    for (s in 0 until strings) {
        val u = (s - (strings - 1) / 2f) / ((strings - 1) / 2f)       // -1 … 1
        val top = Offset(c.x + u * r * 0.95f, yokeY)
        val bottom = Offset(c.x + u * r * 0.42f, baseY)
        val freq = 18f + s * 3.1f
        val amp = r * 0.07f * envelope * amount * (1f - 0.35f * abs(u))
        val path = Path()
        val steps = 28
        for (i in 0..steps) {
            val k = i.toFloat() / steps
            val bend = sin(k * PI.toFloat()) * sin(t * freq + s) * amp
            val x = lerp(top.x, bottom.x, k) + bend
            val y = lerp(top.y, bottom.y, k)
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        val alpha = amount * (0.55f + 0.45f * envelope)
        drawPath(path, secondary.copy(alpha = 0.18f * alpha), style = Stroke(width = 6f * density, cap = StrokeCap.Round))
        drawPath(path, Color.White.copy(alpha = 0.85f * alpha), style = Stroke(width = 1.1f * density, cap = StrokeCap.Round))
    }
}

/** The first [fraction] of a path's length, for lines that draw themselves in. */
private fun trim(path: Path, fraction: Float): Path {
    if (fraction >= 0.999f) return path
    val measure = PathMeasure()
    measure.setPath(path, false)
    val out = Path()
    measure.getSegment(0f, measure.length * fraction.coerceIn(0f, 1f), out, true)
    return out
}
