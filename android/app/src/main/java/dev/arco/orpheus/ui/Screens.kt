package dev.arco.orpheus.ui

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.arco.orpheus.Line
import dev.arco.orpheus.Link
import dev.arco.orpheus.Phase
import dev.arco.orpheus.Settings
import dev.arco.orpheus.VoiceEnroll
import dev.arco.orpheus.label
import kotlin.math.roundToInt
import kotlin.math.sin

private val Pill = RoundedCornerShape(50)

/**
 * Main ↔ settings as a move in depth: settings come forward out of the sky,
 * the main screen sinks back behind them, and the other way round.
 */
@Composable
fun Screens(inSettings: Boolean, main: @Composable () -> Unit, settings: @Composable () -> Unit) {
    AnimatedContent(
        targetState = inSettings,
        transitionSpec = {
            if (targetState) {
                (fadeIn(tween(380)) + scaleIn(tween(460), initialScale = 1.08f)) togetherWith
                    (fadeOut(tween(300)) + scaleOut(tween(460), targetScale = 0.9f))
            } else {
                (fadeIn(tween(380)) + scaleIn(tween(460), initialScale = 0.9f)) togetherWith
                    (fadeOut(tween(300)) + scaleOut(tween(460), targetScale = 1.08f))
            }
        },
        label = "screens",
    ) { showSettings -> if (showSettings) settings() else main() }
}

@Composable
fun MainScreen(
    phase: Phase,
    link: Link,
    level: Float,
    lines: List<Line>,
    problem: String?,
    backgroundAllowed: Boolean,
    onToggle: () -> Unit,
    onTalk: () -> Unit,
    onSettings: () -> Unit,
    onAllowBackground: () -> Unit,
    personal: Boolean = false,
    onPersonal: () -> Unit = {},
    onClear: () -> Unit = {},
    /** Called by the buds' touch, not «Орфей» (Settings.trigger): what the screen asks for says so. */
    touch: Boolean = false,
) {
    val haptic = LocalHapticFeedback.current
    val firstPhase = remember { phase }
    var phaseChanged by remember { mutableStateOf(false) }
    LaunchedEffect(phase) {
        if (phase != firstPhase || phaseChanged) {
            phaseChanged = true
            haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove)
        }
    }
    val on = phase != Phase.Off

    Box(Modifier.fillMaxSize()) {
        Backdrop(phase, Modifier.fillMaxSize())
        Column(Modifier.fillMaxSize().safeDrawingPadding().padding(horizontal = 20.dp)) {
            TopBar(phase, link, onSettings)

            Box(Modifier.fillMaxWidth().weight(1.15f), contentAlignment = Alignment.Center) {
                Orb(phase, level, Modifier.fillMaxSize())
            }
            KineticText(
                if (touch && phase == Phase.Wake) "Зажми наушник" else phase.label(),
                style = TextStyle(fontFamily = LocalFonts.current.display, fontWeight = FontWeight.Medium, fontSize = 21.sp, letterSpacing = 0.5.sp),
                color = Palette.Ink,
                modifier = Modifier.align(Alignment.CenterHorizontally),
            )
            // the "Личное" switch, under the phase: glowing, it is also the sign the talk is private
            AnimatedVisibility(
                on || personal,
                Modifier.align(Alignment.CenterHorizontally).padding(top = 10.dp),
                enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically(),
            ) {
                PersonalChip(personal, enabled = link == Link.Online, onPersonal)
            }
            AnimatedVisibility(
                problem != null,
                Modifier.align(Alignment.CenterHorizontally).padding(top = 10.dp),
                enter = fadeIn() + expandVertically() + scaleIn(initialScale = 0.9f),
                exit = fadeOut() + shrinkVertically(),
            ) {
                Banner(problem.orEmpty(), Palette.Danger)
            }
            AnimatedVisibility(
                !backgroundAllowed && on,
                Modifier.align(Alignment.CenterHorizontally).padding(top = 10.dp),
                enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically(),
            ) {
                GlassChip("Разрешить работу в фоне без ограничений", Palette.Warn, onAllowBackground)
            }

            AnimatedVisibility(
                lines.isNotEmpty(),
                Modifier.align(Alignment.End).padding(top = 8.dp),
                enter = fadeIn(), exit = fadeOut(),
            ) {
                GlassChip("Очистить", Palette.Muted, onClear)
            }
            Conversation(phase, lines, Modifier.weight(1f).fillMaxWidth().padding(top = 6.dp), touch)
            Controls(phase, level, onToggle, onTalk)
        }
    }
}

@OptIn(ExperimentalTextApi::class)
@Composable
private fun TopBar(phase: Phase, link: Link, onSettings: () -> Unit) {
    val primary by animateColorAsState(phase.color(), tween(900), label = "title1")
    val accent by animateColorAsState(phase.accent(), tween(1200), label = "title2")
    Row(Modifier.fillMaxWidth().padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(
            "ОРФЕЙ",
            style = TextStyle(
                fontFamily = LocalFonts.current.display, fontWeight = FontWeight.Bold, fontSize = 22.sp, letterSpacing = 5.sp,
                brush = Brush.linearGradient(listOf(Palette.Ink, lerpColor(Palette.Ink, primary, 0.6f), accent)),
            ),
        )
        Spacer(Modifier.width(14.dp))
        LinkChip(link, phase != Phase.Off)
        Spacer(Modifier.weight(1f))
        GearButton(onSettings)
    }
}

/** Springs overshoot: colours only make sense between the two ends. */
private fun lerpColor(a: Color, b: Color, t: Float) = androidx.compose.ui.graphics.lerp(a, b, t.coerceIn(0f, 1f))

@Composable
private fun LinkChip(link: Link, enabled: Boolean) {
    val (text, color) = when {
        !enabled -> "не активен" to Palette.Muted
        link == Link.Online -> "на связи" to Palette.Ok
        link == Link.Connecting -> "подключаюсь" to Palette.Warn
        link == Link.NoServer -> "нет сервера" to Palette.Danger
        link == Link.Denied -> "нет доступа" to Palette.Danger
        else -> "нет связи" to Palette.Danger
    }
    val dot by animateColorAsState(color, tween(500), label = "dot")
    val pulsing = enabled && (link == Link.Online || link == Link.Connecting)
    val time by frameTime()
    Row(
        Modifier
            .clip(Pill)
            .background(Palette.Glass)
            .border(1.dp, Palette.GlassEdge, Pill)
            .animateContentSize(Motion.snappy())
            .padding(start = 10.dp, end = 12.dp, top = 5.dp, bottom = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Canvas(Modifier.size(10.dp)) {
            val speed = if (link == Link.Connecting) 1.6f else 0.8f
            if (pulsing) {
                val p = (time * speed) % 1f
                drawCircle(dot.copy(alpha = (1f - p) * 0.6f), radius = size.minDimension / 2 * (0.5f + p * 1.1f))
            }
            drawCircle(dot, radius = size.minDimension / 4)
        }
        Spacer(Modifier.width(6.dp))
        AnimatedContent(
            text,
            transitionSpec = { (fadeIn() + slideInVertically { it }) togetherWith (fadeOut() + slideOutVertically { -it }) },
            label = "link",
        ) { Text(it, color = Palette.Muted, fontSize = 12.sp) }
    }
}

/** The "Личное" section: a switch and, when on, the sign that the conversation is private. */
@Composable
private fun PersonalChip(on: Boolean, enabled: Boolean, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    val tint by animateColorAsState(if (on) PersonalColor else Palette.Muted, tween(500), label = "personal")
    val fill by animateColorAsState(if (on) PersonalColor.copy(alpha = 0.22f) else Palette.Glass, tween(500), label = "personalFill")
    val time by frameTime()
    Row(
        Modifier
            .pressScale(interaction)
            .clip(Pill)
            .background(fill)
            .border(1.dp, if (on) PersonalColor.copy(alpha = 0.7f) else Palette.GlassEdge, Pill)
            .clickable(interaction, indication = null, enabled = enabled || on, onClick = onClick)
            .animateContentSize(Motion.snappy())
            .padding(start = 10.dp, end = 12.dp, top = 5.dp, bottom = 5.dp)
            .graphicsLayer { alpha = if (enabled || on) 1f else 0.45f },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Canvas(Modifier.size(10.dp)) {
            if (on) {
                val p = (time * 0.6f) % 1f
                drawCircle(tint.copy(alpha = (1f - p) * 0.5f), radius = size.minDimension / 2 * (0.5f + p))
            }
            drawCircle(tint, radius = size.minDimension / 4)
        }
        Spacer(Modifier.width(6.dp))
        Text("личное", color = if (on) Palette.Ink else Palette.Muted, fontSize = 12.sp)
    }
}

private val PersonalColor = Color(0xFFE879F9)

@Composable
private fun GearButton(onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    val turn = remember { Animatable(0f) }
    var clicks by remember { mutableStateOf(0) }
    LaunchedEffect(clicks) { if (clicks > 0) turn.animateTo(clicks * 120f, Motion.bouncy()) }
    Box(
        Modifier
            .size(44.dp)
            .pressScale(interaction)
            .clip(CircleShape)
            .background(Palette.Glass)
            .border(1.dp, Palette.GlassEdge, CircleShape)
            .clickable(interaction, indication = null) { clicks++; onClick() },
        contentAlignment = Alignment.Center,
    ) {
        Icon(GearIcon, "Настройки", tint = Palette.Ink.copy(alpha = 0.8f), modifier = Modifier.size(22.dp).graphicsLayer { rotationZ = turn.value })
    }
}

@Composable
private fun Banner(text: String, color: Color) {
    Text(
        text, color = Palette.Ink, fontSize = 13.sp,
        modifier = Modifier
            .clip(RoundedCornerShape(14.dp))
            .background(color.copy(alpha = 0.14f))
            .border(1.dp, color.copy(alpha = 0.45f), RoundedCornerShape(14.dp))
            .padding(horizontal = 14.dp, vertical = 8.dp),
    )
}

@Composable
private fun GlassChip(text: String, color: Color, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    Text(
        text, color = color, fontSize = 13.sp,
        modifier = Modifier
            .pressScale(interaction)
            .clip(Pill)
            .background(color.copy(alpha = 0.10f))
            .border(1.dp, color.copy(alpha = 0.35f), Pill)
            .clickable(interaction, indication = null, onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 8.dp),
    )
}

// ---- conversation

@Composable
private fun Conversation(phase: Phase, lines: List<Line>, modifier: Modifier, touch: Boolean = false) {
    if (lines.isEmpty()) {
        Column(modifier, verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
            ShimmerText(
                when (phase) {
                    Phase.Off -> "Включи Орфея — и он будет рядом"
                    Phase.Paused -> "Пауза: я не слушаю"
                    else -> if (touch) "Зажми наушник — и спрашивай" else "Скажи «Орфей» — и спрашивай"
                },
                style = TextStyle(fontFamily = LocalFonts.current.body, fontSize = 16.sp),
                base = Palette.Muted, glint = Palette.Ink,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                when (phase) {
                    Phase.Off -> "он услышит тебя даже при выключенном экране"
                    Phase.Paused -> "нажми «Говорить» или «Слушать снова» в уведомлении"
                    else -> "или нажми «Говорить»"
                },
                color = Palette.Faint, fontSize = 13.sp,
            )
        }
        return
    }
    val state = rememberLazyListState()
    LaunchedEffect(lines.size, lines.lastOrNull()?.text?.length) {
        state.animateScrollToItem(lines.size - 1, scrollOffset = 10_000)
    }
    val live = phase == Phase.Thinking || phase == Phase.Speaking
    LazyColumn(
        modifier
            // the oldest lines fade out under the orb instead of being cut off
            .graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }
            .drawWithContent {
                drawContent()
                drawRect(
                    Brush.verticalGradient(0f to Color.Transparent, 0.18f to Color.Black, 1f to Color.Black),
                    blendMode = BlendMode.DstIn,
                )
            },
        state = state,
        contentPadding = PaddingValues(top = 28.dp, bottom = 8.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        itemsIndexed(lines, key = { _, line -> "${line.at}-${line.fromUser}" }) { i, line ->
            MessageRow(line, typing = live && !line.fromUser && i == lines.lastIndex, phase)
        }
    }
}

@Composable
private fun MessageRow(line: Line, typing: Boolean, phase: Phase) {
    val fresh = remember { System.currentTimeMillis() - line.at < 3_000 }
    val enter = rememberEntrance(line.at, 0, enabled = fresh)
    val side = if (line.fromUser) 1f else -1f
    val shape = RoundedCornerShape(
        topStart = 22.dp, topEnd = 22.dp,
        bottomStart = if (line.fromUser) 22.dp else 6.dp,
        bottomEnd = if (line.fromUser) 6.dp else 22.dp,
    )
    val tone by animateColorAsState(if (line.fromUser) Phase.Listening.color() else phase.color().takeIf { typing } ?: Phase.Speaking.color(), tween(600), label = "tone")
    Row(
        Modifier.fillMaxWidth().graphicsLayer {
            val p = enter.value
            alpha = p.coerceIn(0f, 1f)
            translationX = side * (1f - p) * 48.dp.toPx()
            translationY = (1f - p) * 18.dp.toPx()
            scaleX = 0.9f + 0.1f * p
            scaleY = 0.9f + 0.1f * p
            transformOrigin = TransformOrigin(if (line.fromUser) 1f else 0f, 1f)
        },
        horizontalArrangement = if (line.fromUser) Arrangement.End else Arrangement.Start,
    ) {
        Box(
            Modifier
                .widthIn(max = 300.dp)
                .clip(shape)
                .background(
                    if (line.fromUser) Brush.linearGradient(listOf(tone.copy(alpha = 0.22f), Phase.Wake.color().copy(alpha = 0.16f)))
                    else Brush.linearGradient(listOf(Palette.Glass, Color(0x0AFFFFFF))),
                )
                .border(1.dp, Brush.linearGradient(listOf(tone.copy(alpha = 0.55f), Palette.GlassEdge, Color.Transparent)), shape)
                .padding(horizontal = 15.dp, vertical = 11.dp),
        ) {
            RevealText(line.text, animate = fresh || typing, caret = typing, caretColor = tone)
        }
    }
}

/**
 * Text that appears word by word as it streams in. The words not shown yet are already laid out
 * (transparent), so the bubble never jumps; each new word fades in after the previous one.
 */
@Composable
private fun RevealText(text: String, animate: Boolean, caret: Boolean, caretColor: Color) {
    val words = remember(text) { Regex("\\S+\\s*").findAll(text).map { it.value }.toList() }
    val reduced = LocalReducedMotion.current
    val shown = remember { Animatable(if (animate && !reduced) 0f else words.size.toFloat()) }
    LaunchedEffect(words.size) {
        val target = words.size.toFloat()
        if (shown.value < target) shown.animateTo(target, tween(((target - shown.value) * 55).toInt().coerceAtMost(1500)))
    }
    val time by frameTime()
    val ink = Palette.Ink
    val body = MaterialTheme.typography.bodyLarge
    val annotated: AnnotatedString = buildAnnotatedString {
        val s = shown.value
        words.forEachIndexed { i, w ->
            val a = (s - i).coerceIn(0f, 1f)
            withStyle(SpanStyle(color = ink.copy(alpha = a))) { append(w) }
        }
        if (caret) {
            val blink = 0.35f + 0.65f * (0.5f + 0.5f * sin(time * 7f))
            withStyle(SpanStyle(color = caretColor.copy(alpha = blink))) { append(" ▍") }
        }
    }
    Text(annotated, style = body)
}

// ---- controls

/**
 * The power button and the talk button are one gesture: off, the power button is a wide pill.
 * Switching on, it shrinks into a ring and the talk button grows out of it with a live glow.
 */
@Composable
private fun Controls(phase: Phase, level: Float, onToggle: () -> Unit, onTalk: () -> Unit) {
    val on = phase != Phase.Off
    val spring by animateFloatAsState(if (on) 1f else 0f, Motion.snappy(), label = "controls")
    val p = spring.coerceIn(0f, 1f)
    val haptic = LocalHapticFeedback.current
    val time by frameTime()
    BoxWithConstraints(Modifier.fillMaxWidth().padding(top = 12.dp, bottom = 18.dp).height(62.dp)) {
        val ring = 62.dp
        val gap = 12.dp
        val powerWidth = lerpDp(maxWidth, ring, p)
        val talkWidth = (maxWidth - ring - gap) * p

        val powerInteraction = remember { MutableInteractionSource() }
        Box(
            Modifier
                .align(Alignment.CenterStart)
                .width(powerWidth).fillMaxHeight()
                .pressScale(powerInteraction)
                .clip(Pill)
                .background(
                    Brush.linearGradient(
                        listOf(
                            lerpColor(Phase.Wake.color(), Palette.Glass, p),
                            lerpColor(Phase.Wake.accent(), Palette.Glass, p),
                        ),
                    ),
                )
                .drawBehind {
                    if (p < 0.99f) {
                        // a band of light sweeping over the invitation to switch on
                        val x = ((time * 0.45f) % 1.4f - 0.2f) * size.width
                        drawRect(
                            Brush.linearGradient(
                                listOf(Color.Transparent, Color.White.copy(alpha = 0.22f * (1f - p)), Color.Transparent),
                                start = Offset(x - 120f, 0f), end = Offset(x + 120f, size.height),
                            ),
                        )
                    }
                }
                .border(1.dp, Palette.GlassEdge, Pill)
                .clickable(powerInteraction, indication = null) {
                    haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                    onToggle()
                },
            contentAlignment = Alignment.Center,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(PowerIcon, if (on) "Выключить" else "Включить", tint = Palette.Ink, modifier = Modifier.size(24.dp))
                if (powerWidth > 150.dp) {
                    Spacer(Modifier.width(10.dp))
                    Text(
                        "Включить Орфея", color = Palette.Ink, style = MaterialTheme.typography.labelLarge,
                        modifier = Modifier.graphicsLayer { alpha = (1f - p * 2.2f).coerceIn(0f, 1f) },
                    )
                }
            }
        }

        if (p > 0.01f) {
            val talkInteraction = remember { MutableInteractionSource() }
            val listening = phase == Phase.Listening || phase == Phase.FollowUp
            val glow by animateFloatAsState(if (listening) 0.35f + level * 0.65f else 0.15f, tween(120), label = "glow")
            val tone by animateColorAsState(phase.color(), tween(600), label = "talkTone")
            val toneAccent by animateColorAsState(phase.accent(), tween(800), label = "talkAccent")
            Box(
                Modifier
                    .align(Alignment.CenterEnd)
                    .width(talkWidth).fillMaxHeight()
                    .graphicsLayer { alpha = p.coerceIn(0f, 1f) }
                    .drawBehind {
                        val grow = 6.dp.toPx() * glow
                        drawRoundRect(
                            tone.copy(alpha = 0.35f * glow),
                            topLeft = Offset(-grow, -grow),
                            size = androidx.compose.ui.geometry.Size(size.width + 2 * grow, size.height + 2 * grow),
                            cornerRadius = CornerRadius(size.height),
                        )
                    }
                    .pressScale(talkInteraction)
                    .clip(Pill)
                    .background(Brush.linearGradient(listOf(tone, toneAccent)))
                    .clickable(talkInteraction, indication = null) {
                        haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                        onTalk()
                    },
                contentAlignment = Alignment.Center,
            ) {
                if (talkWidth > 110.dp) {
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.graphicsLayer { alpha = ((p - 0.45f) * 2f).coerceIn(0f, 1f) }) {
                        Icon(MicIcon, null, tint = Palette.Night, modifier = Modifier.size(24.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("Говорить", color = Palette.Night, style = MaterialTheme.typography.labelLarge)
                    }
                }
            }
        }
    }
}

private fun lerpDp(a: Dp, b: Dp, t: Float) = a + (b - a) * t

// ---- settings

@Composable
fun SettingsScreen(
    initial: Settings,
    onSave: (Settings) -> Unit,
    onBack: () -> Unit,
    enroll: VoiceEnroll = VoiceEnroll(),
    phase: Phase = Phase.Off,
    level: Float = 0f,
    onEnrollPhrase: () -> Unit = {},
    onEnrollReset: () -> Unit = {},
    isAssistant: Boolean = false,
    onAssistantSettings: () -> Unit = {},
) {
    var s by remember(initial) { mutableStateOf(initial) }
    val display = LocalFonts.current.display
    Box(Modifier.fillMaxSize()) {
        Backdrop(Phase.Wake, Modifier.fillMaxSize(), dim = 0.45f)
        Column(
            Modifier.fillMaxSize().safeDrawingPadding().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp),
        ) {
            Stagger(0) {
                Row(Modifier.padding(top = 10.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    RoundButton(BackIcon, "Назад", onBack)
                    Spacer(Modifier.width(14.dp))
                    Text("Настройки", color = Palette.Ink, style = TextStyle(fontFamily = display, fontWeight = FontWeight.Medium, fontSize = 24.sp))
                }
            }

            Stagger(1) {
                Card("Голос") {
                    Hint("Каким голосом отвечает Орфей")
                    Spacer(Modifier.height(12.dp))
                    Segmented(
                        listOf("male" to "Мужской", "female" to "Женский"),
                        s.voice, { s = s.copy(voice = it) },
                    )
                }
            }

            Stagger(2) {
                VoiceprintCard(enroll, phase, level, s.headsetMic && s.headsetStrict, onEnrollPhrase, onEnrollReset)
            }

            Stagger(3) {
                Card("Разговор") {
                    Toggle("Слушать продолжение", "Можно ответить без слова «Орфей»", s.followUp) { s = s.copy(followUp = it) }
                    AnimatedVisibility(s.followUp, enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
                        Column(Modifier.padding(top = 10.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text("Сколько ждать", color = Palette.Ink, modifier = Modifier.weight(1f))
                                RollingNumber("${s.followUpSeconds} с")
                            }
                            GlowSlider(s.followUpSeconds.toFloat(), { s = s.copy(followUpSeconds = it.roundToInt()) }, 3f..15f, steps = 11)
                        }
                    }
                    Spacer(Modifier.height(12.dp))
                    Toggle("Звуковые сигналы", "Начал слушать, принял, ошибка", s.earcons) { s = s.copy(earcons = it) }
                    Spacer(Modifier.height(12.dp))
                    Toggle("Микрофон наушников", "Когда подключены Buds, слушать через них. Музыка в наушниках тогда звучит как в звонке", s.headsetMic) { s = s.copy(headsetMic = it) }
                    if (s.headsetMic) {
                        Spacer(Modifier.height(12.dp))
                        Toggle("Строгий режим в наушниках", "В наушниках отвечать только на твой голос. Без них — всем", s.headsetStrict) { s = s.copy(headsetStrict = it) }
                    }
                }
            }

            Stagger(4) {
                Card("Как звать Орфея") {
                    Segmented(
                        listOf("touch" to "Касание", "word" to "Слово"),
                        s.trigger, { s = s.copy(trigger = it) },
                    )
                    Spacer(Modifier.height(10.dp))
                    if (s.trigger == "touch") {
                        Hint(
                            "Зажми наушник — и говори. Между разговорами микрофон выключен: батарея не тратится, " +
                                "музыка в наушниках не портится. " +
                                if (isAssistant) "Орфей выбран цифровым помощником. Если при касании телефон спросит, чем выполнить, — «Орфей», «Всегда»."
                                else "Выбери Орфея цифровым помощником, а в наушниках «Касание и удержание» — «Цифровой помощник».",
                        )
                        if (!isAssistant) {
                            Spacer(Modifier.height(10.dp))
                            GlassChip("Выбрать помощником", Phase.Wake.color(), onAssistantSettings)
                        }
                    } else {
                        Hint("Микрофон слушает всегда: тратит батарею, а в наушниках музыка звучит как в звонке")
                        Spacer(Modifier.height(10.dp))
                        val strictness = ((s.wakeConfidence - 0.5f) / 0.45f * 100).roundToInt()
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("Строгость слова", color = Palette.Ink, modifier = Modifier.weight(1f))
                            RollingNumber("$strictness%")
                        }
                        Hint("Выше — меньше ложных срабатываний, но чаще придётся повторять")
                        GlowSlider(s.wakeConfidence, { s = s.copy(wakeConfidence = it) }, 0.5f..0.95f)
                    }
                }
            }

            Stagger(5) {
                Card("Личное") {
                    val keyOk = s.personalKey.isBlank() || dev.arco.orpheus.KeyVault.isValid(s.personalKey)
                    GlassField(s.personalKey, { s = s.copy(personalKey = it) }, "Ключ «Личного»", "не задан", KeyboardType.Password, secret = true)
                    Hint(
                        when {
                            !keyOk -> "Не похоже на ключ: нужны 44 символа из копии на ноутбуке"
                            s.personalKey.isBlank() -> "Без ключа «Личное» закрыто. Копия ключа — на ноутбуке, в Keys/Privat"
                            else -> "Хранится в защищённом хранилище телефона и открывает личную память на сервере"
                        }
                    )
                }
            }

            Stagger(6) {
                Card("Сервер") {
                    GlassField(s.serverUrl, { s = s.copy(serverUrl = it) }, "Адрес", "встроенный", KeyboardType.Uri)
                    Spacer(Modifier.height(10.dp))
                    GlassField(s.token, { s = s.copy(token = it) }, "Токен", "встроенный", KeyboardType.Password, secret = true)
                }
            }

            Stagger(7) {
                SaveButton { onSave(s) }
            }
            Spacer(Modifier.height(28.dp))
        }
    }
}

/** One step of the settings' entrance: every block rises in a little after the previous one. */
@Composable
private fun Stagger(index: Int, content: @Composable () -> Unit) {
    val a = rememberEntrance(Unit, 60L + index * 70L)
    Box(Modifier.graphicsLayer {
        val p = a.value
        alpha = p.coerceIn(0f, 1f)
        translationY = (1f - p) * 36.dp.toPx()
        scaleX = 0.96f + 0.04f * p
        scaleY = 0.96f + 0.04f * p
    }) { content() }
}

@OptIn(ExperimentalTextApi::class)
@Composable
private fun Card(title: String, content: @Composable () -> Unit) {
    val shape = RoundedCornerShape(26.dp)
    Column(
        Modifier
            .padding(top = 14.dp)
            .fillMaxWidth()
            .clip(shape)
            .background(Brush.verticalGradient(listOf(Color(0x17FFFFFF), Color(0x08FFFFFF))))
            .border(1.dp, Brush.linearGradient(listOf(Palette.GlassEdge, Color(0x08FFFFFF), Palette.GlassEdge)), shape)
            .padding(18.dp),
    ) {
        Text(
            title.uppercase(),
            style = TextStyle(
                fontFamily = LocalFonts.current.display, fontWeight = FontWeight.Medium, fontSize = 11.sp, letterSpacing = 2.sp,
                brush = Brush.linearGradient(listOf(Phase.Wake.color(), Phase.Listening.color())),
            ),
        )
        Spacer(Modifier.height(12.dp))
        content()
    }
}

@Composable
private fun Hint(text: String) = Text(text, color = Palette.Muted, fontSize = 13.sp)

@Composable
private fun RoundButton(icon: androidx.compose.ui.graphics.vector.ImageVector, description: String, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    Box(
        Modifier
            .size(44.dp)
            .pressScale(interaction)
            .clip(CircleShape)
            .background(Palette.Glass)
            .border(1.dp, Palette.GlassEdge, CircleShape)
            .clickable(interaction, indication = null, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { Icon(icon, description, tint = Palette.Ink, modifier = Modifier.size(22.dp)) }
}

/** A number that rolls to its new value, like a mechanical counter. */
@Composable
private fun RollingNumber(text: String) {
    AnimatedContent(
        text,
        transitionSpec = { (fadeIn(tween(160)) + slideInVertically { it / 2 }) togetherWith (fadeOut(tween(120)) + slideOutVertically { -it / 2 }) },
        label = "number",
    ) {
        Text(it, color = Phase.Listening.color(), style = TextStyle(fontFamily = LocalFonts.current.display, fontWeight = FontWeight.Medium, fontSize = 15.sp))
    }
}

@Composable
private fun Toggle(title: String, hint: String, value: Boolean, onChange: (Boolean) -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    Row(
        Modifier.fillMaxWidth().clickable(interaction, indication = null) { onChange(!value) },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, color = Palette.Ink)
            Hint(hint)
        }
        Spacer(Modifier.width(12.dp))
        GlowSwitch(value, interaction)
    }
}

/** A switch whose knob springs across and lights the track up. */
@Composable
private fun GlowSwitch(on: Boolean, interaction: MutableInteractionSource) {
    val knob by animateDpAsState(if (on) 24.dp else 2.dp, Motion.bouncy(), label = "knob")
    val track by animateColorAsState(if (on) Phase.Listening.color() else Color(0x22FFFFFF), tween(250), label = "track")
    val glow by animateFloatAsState(if (on) 1f else 0f, tween(300), label = "switchGlow")
    Box(
        Modifier
            .size(52.dp, 30.dp)
            .pressScale(interaction, 0.9f)
            .drawBehind {
                if (glow > 0f) drawRoundRect(track.copy(alpha = 0.35f * glow), Offset(-6f, -6f),
                    androidx.compose.ui.geometry.Size(size.width + 12f, size.height + 12f), CornerRadius(size.height))
            }
            .clip(Pill)
            .background(Brush.linearGradient(listOf(track, lerpColor(track, Phase.Wake.color(), 0.5f * glow))))
            .border(1.dp, Palette.GlassEdge, Pill),
    ) {
        Box(Modifier.offset(x = knob, y = 2.dp).size(26.dp).clip(CircleShape).background(Palette.Ink))
    }
}

@Composable
private fun GlowSlider(value: Float, onChange: (Float) -> Unit, range: ClosedFloatingPointRange<Float>, steps: Int = 0) {
    Slider(
        value, onChange, valueRange = range, steps = steps,
        colors = SliderDefaults.colors(
            thumbColor = Palette.Ink,
            activeTrackColor = Phase.Listening.color(),
            inactiveTrackColor = Color(0x22FFFFFF),
            activeTickColor = Color(0x55000000),
            inactiveTickColor = Color(0x33FFFFFF),
        ),
    )
}

/** Two options with a pill that slides between them. */
@Composable
private fun Segmented(options: List<Pair<String, String>>, selected: String, onSelect: (String) -> Unit) {
    val index = options.indexOfFirst { it.first == selected }.coerceAtLeast(0)
    val position by animateFloatAsState(index.toFloat(), Motion.bouncy(), label = "segment")
    BoxWithConstraints(
        Modifier.fillMaxWidth().height(48.dp).clip(Pill).background(Color(0x14FFFFFF)).border(1.dp, Palette.GlassEdge, Pill).padding(4.dp),
    ) {
        val cell = maxWidth / options.size
        Box(
            Modifier
                .offset(x = cell * position)
                .width(cell).fillMaxHeight()
                .clip(Pill)
                .background(Brush.linearGradient(listOf(Phase.Wake.color(), Phase.Listening.color()))),
        )
        Row(Modifier.fillMaxSize()) {
            options.forEachIndexed { i, (key, label) ->
                val interaction = remember { MutableInteractionSource() }
                val ink by animateColorAsState(if (i == index) Palette.Night else Palette.Ink, tween(250), label = "segInk")
                Box(
                    Modifier.weight(1f).fillMaxHeight().pressScale(interaction).clickable(interaction, indication = null) { onSelect(key) },
                    contentAlignment = Alignment.Center,
                ) { Text(label, color = ink, style = MaterialTheme.typography.labelLarge) }
            }
        }
    }
}

@Composable
private fun GlassField(
    value: String, onChange: (String) -> Unit, label: String, placeholder: String,
    keyboard: KeyboardType, secret: Boolean = false,
) {
    OutlinedTextField(
        value, onChange,
        label = { Text(label) }, placeholder = { Text(placeholder) },
        singleLine = true, modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(18.dp),
        keyboardOptions = KeyboardOptions(keyboardType = keyboard),
        visualTransformation = if (secret) PasswordVisualTransformation() else androidx.compose.ui.text.input.VisualTransformation.None,
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = Phase.Listening.color(),
            unfocusedBorderColor = Palette.GlassEdge,
            focusedLabelColor = Phase.Listening.color(),
            unfocusedLabelColor = Palette.Muted,
            focusedTextColor = Palette.Ink,
            unfocusedTextColor = Palette.Ink,
            cursorColor = Phase.Listening.color(),
            focusedContainerColor = Color(0x0FFFFFFF),
            unfocusedContainerColor = Color(0x08FFFFFF),
            focusedPlaceholderColor = Palette.Faint,
            unfocusedPlaceholderColor = Palette.Faint,
        ),
    )
}

@Composable
private fun SaveButton(onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    val time by frameTime()
    val haptic = LocalHapticFeedback.current
    Box(
        Modifier
            .padding(top = 22.dp)
            .fillMaxWidth().height(56.dp)
            .pressScale(interaction)
            .clip(Pill)
            .background(Brush.linearGradient(listOf(Phase.Wake.color(), Phase.Thinking.color(), Phase.Listening.color())))
            .drawBehind {
                val x = ((time * 0.4f) % 1.5f - 0.25f) * size.width
                drawRect(
                    Brush.linearGradient(
                        listOf(Color.Transparent, Color.White.copy(alpha = 0.3f), Color.Transparent),
                        start = Offset(x - 140f, 0f), end = Offset(x + 140f, size.height),
                    ),
                )
            }
            .clickable(interaction, indication = null) {
                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                onClick()
            },
        contentAlignment = Alignment.Center,
    ) {
        Text("Сохранить", color = Palette.Night, style = MaterialTheme.typography.labelLarge.copy(fontSize = 16.sp))
    }
}

// ---- the owner's voice

/** Read aloud to record the voice: different sounds, a question and a statement, a few seconds each. */
private val ENROLL_PHRASES = listOf(
    "Орфей, какие у меня планы на завтра?",
    "Запиши заметку: купить хлеб, молоко и сыр.",
    "Какая погода будет в выходные в Москве?",
    "Напомни мне в понедельник позвонить маме.",
    "Сколько будет двадцать восемь умножить на четыре?",
)

@Composable
private fun VoiceprintCard(v: VoiceEnroll, phase: Phase, level: Float, budsStrict: Boolean, onPhrase: () -> Unit, onReset: () -> Unit) {
    val on = phase != Phase.Off
    val listening = v.recording && (phase == Phase.Listening || phase == Phase.FollowUp)
    Card("Мой голос") {
        Hint(
            when {
                !on -> "Включи Орфея, чтобы записать голос"
                v.ready && v.mode == "strict" -> "Записан: Орфей отвечает только тебе"
                v.ready && v.mode == "off" -> "Записан, но проверка голоса на сервере выключена"
                v.ready && budsStrict -> "Записан: в наушниках Орфей отвечает только тебе, без них — всем"
                v.ready -> "Записан. Сервер пока только сверяет голос и отвечает всем"
                else -> "Прочитай вслух ${v.needed} фраз в наушниках — и Орфей будет узнавать тебя по голосу, а не телевизор"
            },
        )
        Spacer(Modifier.height(14.dp))
        EnrollProgress(v.count, v.needed, listening, level)
        AnimatedVisibility(!v.ready || v.recording, enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
            AnimatedContent(
                ENROLL_PHRASES[v.count % ENROLL_PHRASES.size],
                transitionSpec = { (fadeIn(tween(300)) + slideInVertically { it / 2 }) togetherWith (fadeOut(tween(200)) + slideOutVertically { -it / 2 }) },
                label = "phrase",
            ) { phrase ->
                Text(
                    "«$phrase»",
                    color = Palette.Ink,
                    style = TextStyle(fontFamily = LocalFonts.current.display, fontWeight = FontWeight.Medium, fontSize = 15.sp, lineHeight = 22.sp),
                    modifier = Modifier.padding(top = 14.dp),
                )
            }
        }
        AnimatedVisibility(v.error != null, enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
            Text(v.error.orEmpty(), color = Palette.Danger, fontSize = 13.sp, modifier = Modifier.padding(top = 8.dp))
        }
        Spacer(Modifier.height(14.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            RecordButton(
                when {
                    listening -> "Слушаю…"
                    v.recording -> "Запоминаю…"
                    v.ready -> "Добавить фразу"
                    else -> "Фраза ${v.count + 1} из ${v.needed}"
                },
                enabled = on && !v.recording, listening = listening, level = level,
                modifier = Modifier.weight(1f), onClick = onPhrase,
            )
            if (v.count > 0 && !v.recording) {
                Spacer(Modifier.width(10.dp))
                GlassChip("Сбросить", Palette.Muted, onReset)
            }
        }
    }
}

/** One segment per phrase: filled ones glow, the one being recorded breathes with the voice. */
@Composable
private fun EnrollProgress(count: Int, needed: Int, listening: Boolean, level: Float) {
    val time by frameTime()
    Row(Modifier.fillMaxWidth().height(8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        for (i in 0 until needed) {
            val fill by animateFloatAsState(if (i < count) 1f else 0f, Motion.bouncy(), label = "segment$i")
            val current = listening && i == count
            Box(
                Modifier
                    .weight(1f).fillMaxHeight()
                    .clip(Pill)
                    .background(Color(0x1FFFFFFF))
                    .drawBehind {
                        val f = fill.coerceIn(0f, 1f)
                        if (f > 0f) {
                            drawRoundRect(
                                Brush.horizontalGradient(listOf(Phase.Wake.color(), Phase.Listening.color())),
                                size = size.copy(width = size.width * f), cornerRadius = CornerRadius(size.height),
                            )
                        }
                        if (current) {
                            val breath = (0.35f + 0.65f * level.coerceIn(0f, 1f)) * (0.75f + 0.25f * sin(time * 6f))
                            drawRoundRect(
                                Phase.Listening.color().copy(alpha = breath.coerceIn(0f, 1f)),
                                size = size.copy(width = size.width * (0.2f + 0.8f * level.coerceIn(0f, 1f))),
                                cornerRadius = CornerRadius(size.height),
                            )
                        }
                    },
            )
        }
    }
}

@Composable
private fun RecordButton(text: String, enabled: Boolean, listening: Boolean, level: Float, modifier: Modifier, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    val glow by animateFloatAsState(if (listening) 0.35f + 0.65f * level.coerceIn(0f, 1f) else 0f, tween(120), label = "recGlow")
    val dim by animateFloatAsState(if (enabled || listening) 1f else 0.45f, tween(250), label = "recDim")
    Box(
        modifier
            .height(48.dp)
            .graphicsLayer { alpha = dim }
            .drawBehind {
                if (glow > 0f) {
                    val grow = 6.dp.toPx() * glow
                    drawRoundRect(
                        Phase.Listening.color().copy(alpha = 0.4f * glow),
                        topLeft = Offset(-grow, -grow),
                        size = androidx.compose.ui.geometry.Size(size.width + 2 * grow, size.height + 2 * grow),
                        cornerRadius = CornerRadius(size.height),
                    )
                }
            }
            .pressScale(interaction)
            .clip(Pill)
            .background(Brush.linearGradient(listOf(Phase.Listening.color(), Phase.Wake.color())))
            .clickable(interaction, indication = null, enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(MicIcon, null, tint = Palette.Night, modifier = Modifier.size(20.dp))
            Spacer(Modifier.width(8.dp))
            AnimatedContent(text, transitionSpec = { fadeIn() togetherWith fadeOut() }, label = "recText") {
                Text(it, color = Palette.Night, style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}
