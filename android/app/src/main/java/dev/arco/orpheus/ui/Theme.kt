package dev.arco.orpheus.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import dev.arco.orpheus.Phase

/** The night the whole app lives in, and the light each phase gives off. */
object Palette {
    val Night = Color(0xFF04050B)
    val Deep = Color(0xFF0A0C1B)
    val Ink = Color(0xFFF1F2FF)
    val Muted = Color(0xFF8C91B0)
    val Faint = Color(0xFF3B405E)
    val Glass = Color(0x14FFFFFF)
    val GlassEdge = Color(0x26FFFFFF)
    val Danger = Color(0xFFFF7A6B)
    val Ok = Color(0xFF4DF0A0)
    val Warn = Color(0xFFFFC857)
}

/** Colours per phase, like a smart speaker's light ring: the main tone and the one it bleeds into. */
fun Phase.color(): Color = when (this) {
    Phase.Off -> Color(0xFF3A3F58)
    Phase.Wake -> Color(0xFF6F7CFF)
    Phase.Listening -> Color(0xFF2EE6FF)
    Phase.Thinking -> Color(0xFFB46CFF)
    Phase.Speaking -> Color(0xFFFFC15A)
    Phase.FollowUp -> Color(0xFF3CF0C5)
    Phase.Paused -> Color(0xFF7A6A4A)  // a dim amber: on, but not listening
}

fun Phase.accent(): Color = when (this) {
    Phase.Off -> Color(0xFF232739)
    Phase.Wake -> Color(0xFFA06BFF)
    Phase.Listening -> Color(0xFF4A7DFF)
    Phase.Thinking -> Color(0xFFFF5FCF)
    Phase.Speaking -> Color(0xFFFF6B4A)
    Phase.FollowUp -> Color(0xFF2EA8FF)
    Phase.Paused -> Color(0xFF3A3326)
}

/** Fonts come from the platform (Android: res/font), so the ui code stays plain Compose. */
class Fonts(val display: FontFamily = FontFamily.Default, val body: FontFamily = FontFamily.Default)

val LocalFonts = staticCompositionLocalOf { Fonts() }

/** True when the system asks for no animations: colours and state stay, continuous motion stops. */
val LocalReducedMotion = staticCompositionLocalOf { false }

@Composable
fun OrpheusTheme(fonts: Fonts = Fonts(), reducedMotion: Boolean = false, content: @Composable () -> Unit) {
    val body = fonts.body
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Color(0xFF8E98FF),
            secondary = Color(0xFF2EE6FF),
            background = Palette.Night,
            surface = Palette.Deep,
            surfaceVariant = Color(0xFF151935),
            onSurface = Palette.Ink,
            onBackground = Palette.Ink,
        ),
        typography = Typography(
            bodyLarge = TextStyle(fontFamily = body, fontSize = 16.sp, lineHeight = 23.sp),
            bodyMedium = TextStyle(fontFamily = body, fontSize = 14.sp, lineHeight = 20.sp),
            bodySmall = TextStyle(fontFamily = body, fontSize = 12.sp, lineHeight = 16.sp),
            labelLarge = TextStyle(fontFamily = body, fontWeight = FontWeight.SemiBold, fontSize = 15.sp),
            titleMedium = TextStyle(fontFamily = body, fontWeight = FontWeight.SemiBold, fontSize = 16.sp),
        ),
    ) {
        CompositionLocalProvider(LocalFonts provides fonts, LocalReducedMotion provides reducedMotion, content = content)
    }
}
