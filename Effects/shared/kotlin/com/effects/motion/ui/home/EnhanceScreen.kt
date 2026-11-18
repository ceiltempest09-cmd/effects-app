package com.effects.motion.ui.home

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.clipRect
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.effects.motion.R
import com.effects.motion.ai.EnhanceChoice
import com.effects.motion.ai.EnhancePreview
import com.effects.motion.ai.EnhanceTool
import com.effects.motion.export.ExportKind
import com.effects.motion.export.formatBytes
import com.effects.motion.platform.LocalContext
import com.effects.motion.platform.rememberMediaPicker
import com.effects.motion.tools.PickedMedia
import com.effects.motion.tools.readPicked
import com.effects.motion.ui.theme.Am
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
internal fun EnhanceScreen(onClose: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val work = rememberToolWork()
    var picked by remember { mutableStateOf<PickedMedia?>(null) }
    var unreadable by remember { mutableStateOf(false) }
    var scaleIndex by rememberSaveable { mutableIntStateOf(1) }
    var smooth by rememberSaveable { mutableFloatStateOf(0.5f) }
    var applied by rememberSaveable { mutableFloatStateOf(0.5f) }
    val choice = EnhanceChoice(EnhanceChoice.Scales[scaleIndex], smooth)
    val appliedChoice = EnhanceChoice(choice.scale, applied)

    val models by produceState<Boolean?>(null) {
        value = try {
            withContext(Dispatchers.IO) { EnhanceTool.prepare(context) }
            true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            false
        }
    }
    val sample by produceState<com.effects.motion.ai.EnhanceSample?>(null, picked) {
        value = null
        value = picked?.let { runCatching { EnhanceTool.sample(context, it) }.getOrNull() }
    }
    var preview by remember { mutableStateOf<EnhancePreview?>(null) }
    var previewing by remember { mutableStateOf(false) }
    var previewFailed by remember { mutableStateOf(false) }
    LaunchedEffect(sample, appliedChoice.scale, appliedChoice.strength, models, work.running) {
        val frame = sample
        if (frame == null) { preview = null; return@LaunchedEffect }
        if (models != true || work.running) return@LaunchedEffect
        previewing = true
        previewFailed = false
        delay(250)
        try {
            val made = EnhanceTool.preview(context, frame, appliedChoice)
            preview = made
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            previewFailed = true
        } finally {
            previewing = false
        }
    }

    val picker = rememberMediaPicker { uri ->
        if (uri != null) scope.launch {
            work.cancel()
            work.reset()
            val found = readPicked(context, uri)
            preview = null
            picked = found
            unreadable = found == null
        }
    }

    val media = picked
    val source = media?.source
    val turned = media != null && media.rotation % 180 != 0
    val width = if (source == null) 0 else if (turned) source.height else source.width
    val height = if (source == null) 0 else if (turned) source.width else source.height
    val fits = source == null || !source.video || choice.fitsVideo(width, height)
    val target = when {
        source == null -> null
        source.video -> width * choice.scale to height * choice.scale
        else -> EnhanceTool.photoSize(width, height, choice.scale)
    }
    val hint = when {
        media == null -> stringResource(R.string.enhance_choose_first)
        models == null -> stringResource(R.string.enhance_preparing)
        models == false -> stringResource(R.string.ai_model_broken)
        !fits -> stringResource(R.string.ai_enhance_cap, "${choice.scale}x", EnhanceChoice.videoShortSide(choice.scale).toString())
        target != null -> stringResource(R.string.enhance_size, width, height, target.first, target.second)
        else -> null
    }
    val back = { if (work.running) work.cancel() else onClose() }
    ToolSheet(stringResource(R.string.tools_enhance), back, bottom = {
        ToolBar(
            work, hint, !fits || models == false,
            (work.step as? ToolStep.Done)?.let { stringResource(R.string.enhance_done, formatBytes(it.bytes)) }.orEmpty(),
            R.string.enhance_working, stringResource(R.string.enhance_start), HomeIcons.Enhance, media != null && fits && models == true,
        ) {
            val chosen = media ?: return@ToolBar
            val run = choice.copy(smooth = choice.strength)
            work.start { progress -> if (chosen.source.video) EnhanceTool.video(context, chosen, run, progress) else EnhanceTool.photo(context, chosen, run, progress) }
        }
    }, side = {
        Label(stringResource(R.string.compress_size))
        Chips(EnhanceChoice.Scales.map { "$it×" }, scaleIndex, !work.running) { i -> work.change { scaleIndex = i } }
        Label(stringResource(R.string.enhance_denoise, (choice.strength * 100).toInt()))
        Slider(
            smooth, { value -> if (!work.running) { smooth = value; work.reset() } },
            Modifier.fillMaxWidth(), enabled = !work.running, steps = EnhanceChoice.STEPS - 1,
            onValueChangeFinished = { applied = smooth },
            colors = SliderDefaults.colors(thumbColor = Am.Accent, activeTrackColor = Am.Navy, inactiveTrackColor = Am.HomeLine),
        )
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(stringResource(R.string.enhance_keep_detail), color = Am.HomeDim, fontSize = 13.sp)
            Text(stringResource(R.string.enhance_smoother), color = Am.HomeDim, fontSize = 13.sp)
        }
        Spacer(Modifier.height(24.dp))
    }) {
        Spacer(Modifier.height(20.dp))
        PickCard(media, unreadable, !work.running) { picker.launch() }
        val done = work.step as? ToolStep.Done
        if (done != null) ToolResult(done.result, if (done.result.video) ExportKind.Video else ExportKind.Frame)
        else if (media != null) {
            SectionTitle(stringResource(R.string.enhance_preview))
            val shown = preview
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                if (shown != null) BeforeAfter(shown, Modifier.fillMaxWidth())
                else Box(Modifier.fillMaxWidth().aspectRatio(1f).clip(RoundedCornerShape(12.dp)).background(Am.Card), contentAlignment = Alignment.Center) {
                    if (previewFailed) Text(stringResource(R.string.enhance_preview_failed), Modifier.padding(24.dp), color = Am.HomeDim, fontSize = 14.sp)
                    else CircularProgressIndicator(color = Am.Accent, strokeWidth = 3.dp)
                }
                if (shown != null && previewing) CircularProgressIndicator(Modifier.align(Alignment.TopEnd).padding(12.dp), color = Am.Accent, strokeWidth = 3.dp)
            }
            Spacer(Modifier.height(8.dp))
            BarText(stringResource(R.string.enhance_preview_note), Am.HomeDim)
        }
    }
}

/** Two pictures of the same area stacked; the line in the middle can be dragged (or tapped) to reveal more or less of the original. */
@Composable
internal fun BeforeAfter(preview: EnhancePreview, modifier: Modifier = Modifier) {
    val before = remember(preview) { preview.before.asImageBitmap() }
    val after = remember(preview) { preview.after.asImageBitmap() }
    var split by rememberSaveable { mutableFloatStateOf(0.5f) }
    Box(modifier.aspectRatio(after.width.toFloat() / after.height).clip(RoundedCornerShape(12.dp)).background(Am.Scrim)) {
        Canvas(
            Modifier.matchParentSize()
                .pointerInput(Unit) { detectTapGestures { split = (it.x / size.width).coerceIn(0f, 1f) } }
                .pointerInput(Unit) {
                    detectDragGestures(
                        onDragStart = { split = (it.x / size.width).coerceIn(0f, 1f) },
                        onDrag = { change, _ -> change.consume(); split = (change.position.x / size.width).coerceIn(0f, 1f) },
                    )
                },
        ) {
            val full = IntSize(size.width.toInt(), size.height.toInt())
            drawImage(after, IntOffset.Zero, IntSize(after.width, after.height), IntOffset.Zero, full, filterQuality = FilterQuality.Medium)
            val x = size.width * split
            clipRect(left = 0f, top = 0f, right = x, bottom = size.height) {
                drawImage(before, IntOffset.Zero, IntSize(before.width, before.height), IntOffset.Zero, full, filterQuality = FilterQuality.Medium)
            }
            drawLine(Color.White, Offset(x, 0f), Offset(x, size.height), strokeWidth = 2.dp.toPx())
            val r = 18.dp.toPx()
            drawCircle(Color.White, r, Offset(x, size.height / 2))
            val a = 5.dp.toPx()
            for (dir in listOf(-1f, 1f)) drawPath(
                Path().apply {
                    moveTo(x + dir * 3.dp.toPx(), size.height / 2 - a)
                    lineTo(x + dir * (3.dp.toPx() + a), size.height / 2)
                    lineTo(x + dir * 3.dp.toPx(), size.height / 2 + a)
                    close()
                },
                Color(0xFF222222),
            )
        }
        CompareTag(stringResource(R.string.enhance_before), Modifier.align(Alignment.TopStart))
        CompareTag(stringResource(R.string.enhance_after), Modifier.align(Alignment.TopEnd))
    }
}

@Composable
private fun CompareTag(text: String, modifier: Modifier) =
    Text(text, modifier.padding(10.dp).clip(RoundedCornerShape(8.dp)).background(Color.Black.copy(alpha = 0.55f)).padding(horizontal = 8.dp, vertical = 3.dp),
        color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)
