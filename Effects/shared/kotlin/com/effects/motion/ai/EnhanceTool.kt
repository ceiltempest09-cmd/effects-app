package com.effects.motion.ai

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Matrix
import com.effects.motion.R
import com.effects.motion.export.ThermalMonitor
import com.effects.motion.i18n.AppText
import com.effects.motion.media.FrameReader
import com.effects.motion.media.decodePicture
import com.effects.motion.tools.Compressed
import com.effects.motion.tools.PickedMedia
import com.effects.motion.tools.compressFolder
import com.effects.motion.tools.outputName
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.File
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToLong

/**
 * What the Tools > Enhancer screen asks for.
 * [scale] is the output size factor (1, 2 or 4). [smooth] is the denoise strength from 0 to 1:
 * 1 = realesr-general-x4v3 only (smoothest), 0 = realesr-general-wdn-x4v3 only (keeps the most detail and grain).
 * Values in between blend the weights of both networks, which is how Real-ESRGAN itself does it.
 */
internal data class EnhanceChoice(val scale: Int = 2, val smooth: Float = 0.5f) {
    /** Snapped so that dragging the slider never opens a network for an invisible difference. */
    val strength: Float get() = (smooth * STEPS).toInt().coerceIn(0, STEPS) / STEPS.toFloat()

    fun fitsVideo(width: Int, height: Int): Boolean = min(width, height) in 1..videoShortSide(scale)

    companion object {
        const val STEPS = 20
        val Scales = listOf(1, 2, 4)

        /** Same limits the old effects-library Enhance used, so run times stay realistic. */
        fun videoShortSide(scale: Int): Int = when (scale) { 4 -> 360; 2 -> 720; else -> 1080 }
    }
}

internal class EnhanceSample(val pixels: IntArray, val side: Int)

internal class EnhancePreview(val before: Bitmap, val after: Bitmap)

internal object EnhanceTool {
    /** Source pixels cut from the middle of the picture for the live preview. Small on purpose: the network costs the same at every scale. */
    const val PREVIEW_SIDE = 160

    /** Largest photo we write: 12 megapixels is about 100 MB of working memory with the output bitmap. */
    private const val MAX_PHOTO_PIXELS = 12_000_000L

    private val models = listOf(AiModel.Detail, AiModel.Grain)

    fun modelsReady(context: Context): Boolean = models.all { it.installed(context) }

    /** The two networks ship inside the app; this only copies them to app storage once and checks them. */
    suspend fun prepare(context: Context) {
        for (model in models) if (!model.installed(context)) model.download(context) {}
    }

    private suspend fun open(context: Context, strength: Float): AiNet {
        val blend = if (strength < 1f) AiModel.Grain else null
        AiDevice.probeGpu(context, AiModel.Detail)
        return AiNet.open(context, AiModel.Detail, blend, strength)
    }

    /** Longest side to read a photo at so the enhanced result stays within [MAX_PHOTO_PIXELS]. */
    fun photoSide(width: Int, height: Int, scale: Int): Int {
        val out = width.toLong() * scale * height * scale
        val k = if (out <= MAX_PHOTO_PIXELS) 1.0 else kotlin.math.sqrt(MAX_PHOTO_PIXELS.toDouble() / out)
        return max(16, (max(width, height) * k).toInt())
    }

    fun photoSize(width: Int, height: Int, scale: Int): Pair<Int, Int> {
        val side = photoSide(width, height, scale)
        val k = side.toDouble() / max(width, height)
        return max(1, (width * k).toInt()) * scale to max(1, (height * k).toInt()) * scale
    }

    // ---- preview -------------------------------------------------------------------------------------------------

    suspend fun sample(context: Context, picked: PickedMedia): EnhanceSample? = withContext(Dispatchers.IO) {
        val raw = (if (picked.source.video) videoFrame(context, picked) else decodePicture(context.contentResolver, picked.uri, 1280)) ?: return@withContext null
        val upright = turned(raw, picked.rotation)
        val side = min(PREVIEW_SIDE, min(upright.width, upright.height))
        if (side < 16) return@withContext null
        val crop = Bitmap.createBitmap(upright, (upright.width - side) / 2, (upright.height - side) / 2, side, side)
        val pixels = IntArray(side * side).also { crop.getPixels(it, 0, side, 0, 0, side, side) }
        if (crop !== upright) crop.recycle()
        upright.recycle()
        EnhanceSample(pixels, side)
    }

    suspend fun preview(context: Context, sample: EnhanceSample, choice: EnhanceChoice): EnhancePreview = withContext(Dispatchers.Default) {
        val side = sample.side
        val size = side * choice.scale
        val net = open(context, choice.strength)
        val enhanced = net.use { it.upscale(sample.pixels, side, side, choice.scale) }
        val after = Bitmap.createBitmap(enhanced, size, size, Bitmap.Config.ARGB_8888)
        val small = Bitmap.createBitmap(sample.pixels, side, side, Bitmap.Config.ARGB_8888)
        val before = if (choice.scale == 1) small else Bitmap.createScaledBitmap(small, size, size, true).also { small.recycle() }
        EnhancePreview(before, after)
    }

    private suspend fun videoFrame(context: Context, picked: PickedMedia): Bitmap? = FrameReader(context, picked.uri.toString()).use { reader ->
        val stamps = reader.timestamps
        if (stamps.isEmpty()) return@use null
        var frame: Bitmap? = null
        reader.read(listOf(stamps[stamps.size / 2])) { _, yuv ->
            frame = Bitmap.createBitmap(yuv.argb(yuv.width, yuv.height, reader.colorStandard, reader.fullRange), yuv.width, yuv.height, Bitmap.Config.ARGB_8888)
        }
        frame
    }

    private fun turned(bitmap: Bitmap, degrees: Int): Bitmap {
        val turn = ((degrees % 360) + 360) % 360 / 90 * 90
        if (turn == 0) return bitmap
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, Matrix().apply { postRotate(turn.toFloat()) }, true).also { bitmap.recycle() }
    }

    // ---- photo ---------------------------------------------------------------------------------------------------

    suspend fun photo(context: Context, picked: PickedMedia, choice: EnhanceChoice, progress: (Float) -> Unit): Compressed = withContext(Dispatchers.IO) {
        val out = File(compressFolder(context).apply { mkdirs() }, outputName(picked, "jpg", "enhanced"))
        var source: Bitmap? = null
        var result: Bitmap? = null
        var finished = false
        try {
            progress(0.02f)
            val limit = photoSide(picked.source.width, picked.source.height, choice.scale)
            val decoded = decodePicture(context.contentResolver, picked.uri, limit, keepOutOfMemory = true) ?: error(AppText.get(R.string.compress_unreadable))
            val flat = if (!decoded.hasAlpha()) decoded else Bitmap.createBitmap(decoded.width, decoded.height, Bitmap.Config.ARGB_8888).also {
                Canvas(it).apply { drawColor(android.graphics.Color.WHITE); drawBitmap(decoded, 0f, 0f, null) }
                decoded.recycle()
            }
            val upright = turned(flat, picked.rotation)
            source = upright
            val width = upright.width
            val height = upright.height
            val pixels = IntArray(width * height).also { upright.getPixels(it, 0, width, 0, 0, width, height) }
            upright.recycle()
            source = null
            progress(0.08f)
            val enhanced = open(context, choice.strength).use { it.upscale(pixels, width, height, choice.scale) }
            progress(0.92f)
            val big = Bitmap.createBitmap(enhanced, width * choice.scale, height * choice.scale, Bitmap.Config.ARGB_8888)
            result = big
            currentCoroutineContext().ensureActive()
            out.outputStream().use { check(big.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, it)) { AppText.get(R.string.compress_unreadable) } }
            progress(1f)
            finished = true
            Compressed(out, "image/jpeg", false)
        } finally {
            source?.recycle()
            result?.recycle()
            if (!finished) out.delete()
        }
    }

    // ---- video ---------------------------------------------------------------------------------------------------

    suspend fun video(context: Context, picked: PickedMedia, choice: EnhanceChoice, progress: (Float) -> Unit): Compressed = withContext(Dispatchers.IO) {
        val info = picked.source
        check(choice.fitsVideo(info.width, info.height)) { AppText.get(R.string.ai_enhance_cap, "${choice.scale}x", EnhanceChoice.videoShortSide(choice.scale).toString()) }
        val seconds = info.durationUs / 1_000_000.0
        check(seconds <= MAX_MATTE_SECONDS) { AppText.get(R.string.ai_clip_too_long, (MAX_MATTE_SECONDS / 60).toString()) }
        val fps = info.fps.takeIf { it > 0f } ?: 30f
        val out = File(compressFolder(context).apply { mkdirs() }, outputName(picked, "mp4", "enhanced"))
        val needed = (videoBitrate(info.width * choice.scale, info.height * choice.scale, fps) / 8.0 * seconds).roundToLong() * 2 + 50_000_000L
        check(out.parentFile!!.usableSpace >= needed) { AppText.get(R.string.ai_not_enough_storage, (needed / 1_000_000).toString()) }
        val uri = picked.uri.toString()
        val sound = keepSound(context, uri, info.durationUs)
        val thermal = ThermalMonitor.of(context)
        thermal.start()
        val net = try { open(context, choice.strength) } catch (e: Throwable) { thermal.stop(); sound?.close(); throw e }
        var encoder: FrameEncoder? = null
        var frames = 0
        var success = false
        try {
            FrameReader(context, uri).use { reader ->
                val stamps = matteStamps(reader.timestamps, 0, Int.MAX_VALUE)
                check(stamps.isNotEmpty()) { AppText.get(R.string.ai_no_video_frames) }
                var size = 0 to 0
                val started = System.nanoTime()
                for (batch in stamps.chunked(enhanceBatch(info.width, info.height))) {
                    val decoded = ArrayList<Pair<Long, IntArray>>(batch.size)
                    reader.read(batch) { pts, yuv ->
                        size = yuv.width to yuv.height
                        decoded += pts to yuv.argb(yuv.width, yuv.height, reader.colorStandard, reader.fullRange)
                    }
                    for ((pts, argb) in decoded) {
                        cool(thermal, { fraction, _ -> progress(fraction) }, frames.toFloat() / stamps.size)
                        val big = net.upscale(argb, size.first, size.second, choice.scale)
                        val sink = encoder ?: FrameEncoder.open(
                            context, out, size.first * choice.scale, size.second * choice.scale, fps, picked.rotation, sound?.uri,
                        ).also {
                            encoder = it
                            if (pts > 0) it.write(0L, big)
                        }
                        sink.write(pts, big)
                        frames++
                        progress(0.01f + 0.97f * frames / stamps.size)
                    }
                }
                progress(0.99f)
                checkNotNull(encoder) { AppText.get(R.string.ai_no_video_frames) }.finish(soundEndUs(info.durationUs, info.durationUs))
            }
            encoder?.close()
            encoder = null
            progress(1f)
            success = true
            Compressed(out, "video/mp4", true, frames)
        } finally {
            net.close()
            releaseJob(encoder, thermal::stop) { sound?.close(); if (!success) out.delete() }
        }
    }

    private const val JPEG_QUALITY = 95
}
