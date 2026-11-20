package com.effects.motion.effects

import com.effects.motion.platform.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/** Imported effects live as one .json file each in filesDir/imported_effects and are added to the catalog on read. */
object ImportedEffects {
    private fun folder(context: Context) = File(context.filesDir, "imported_effects")

    fun save(context: Context, json: JSONObject) {
        val dir = folder(context).apply { mkdirs() }
        val target = File(dir, json.getString("id") + ".json")
        val temp = File(dir, target.name + ".tmp")
        temp.writeText(json.toString())
        if (!temp.renameTo(target)) { target.writeText(json.toString()); temp.delete() }
    }

    fun delete(context: Context, effectId: String) {
        File(folder(context), effectId.removePrefix("fx.") + ".json").delete()
    }

    private val installing = AtomicBoolean(false)

    /** A library shipped in assets: the asset folder (index.json lists the files), the library it is stored in, the bit-depth pack, and the key of its install stamp. */
    private class Bundled(val folder: String, val source: String, val tier: ImportTier, val key: String)

    private val bundledLibraries = listOf(
        Bundled("alightmotion", EffectSources.AlightMotion, ImportTier.VIBRANT, "am"),
        Bundled("ae", EffectSources.AfterEffects, ImportTier.VIBRANT, "ae"),
    )

    /**
     * The libraries shipped in assets (assets/alightmotion and assets/ae; index.json lists the files) are converted once per app install/update,
     * in the background, and stored like an import. Files that cannot be converted are skipped. The effects show up the next time the library
     * is opened after the conversion has finished.
     */
    fun installBundledInBackground(context: Context) {
        val app = context.applicationContext
        val stamp = runCatching { app.packageManager.getPackageInfo(app.packageName, 0).lastUpdateTime }.getOrDefault(0L)
        val prefs = app.getSharedPreferences("bundled_library", Context.MODE_PRIVATE)
        if (bundledLibraries.all { prefs.getLong(it.key + "_stamp", -1L) == stamp } || !installing.compareAndSet(false, true)) return
        Thread({
            try {
                for (lib in bundledLibraries) {
                    if (prefs.getLong(lib.key + "_stamp", -1L) == stamp) continue
                    val index = runCatching { JSONArray(app.assets.open(lib.folder + "/index.json").bufferedReader().use { it.readText() }) }.getOrNull()
                    if (index == null) { prefs.edit().putLong(lib.key + "_stamp", stamp).apply(); continue }
                    prefs.getStringSet(lib.key + "_ids", emptySet()).orEmpty().forEach { delete(app, it) }
                    val ids = mutableSetOf<String>()
                    var failed = 0
                    for (i in 0 until index.length()) {
                        val name = index.getString(i)
                        val line = runCatching {
                            val bytes = app.assets.open(lib.folder + "/" + name).use { it.readBytes() }
                            EffectImporter.importBundled(app, lib.tier, name, bytes, lib.source)
                        }.getOrNull()
                        if (line?.ok == true) line.effectId?.let { ids += it } else failed++
                    }
                    prefs.edit().putStringSet(lib.key + "_ids", ids).putLong(lib.key + "_stamp", stamp).apply()
                    android.util.Log.i("Effects", "${lib.folder} library: ${ids.size} added, $failed not converted")
                }
            } catch (e: Throwable) {
                android.util.Log.w("Effects", "Bundled library install stopped: ${e.message}")
            } finally {
                installing.set(false)
            }
        }, "bundled-library").start()
    }

    fun load(context: Context): List<EffectDefinition> =
        folder(context).listFiles { f -> f.name.endsWith(".json") }.orEmpty().sortedBy { it.name }
            .mapNotNull { f -> runCatching { fromJson(JSONObject(f.readText())) }.getOrNull() }

    fun fromJson(json: JSONObject): EffectDefinition {
        val key = json.getString("id")
        val tier = ImportTier.named(json.optString("tier"))
        val source = json.optString("source", EffectSources.Native)
        val lang = json.optString("lang").takeIf { it.isNotBlank() }
        if (source == EffectSources.Native) return EffectDefinition.parse(json, tier.pack).copy(source = source, lang = lang)
        if (com.effects.motion.effects.graph.GraphJson.isGraph(json)) return EffectDefinition.parse(json, tier.pack, strictCategory = false).copy(source = source, lang = lang)
        val params = json.optJSONArray("params") ?: JSONArray()
        return EffectDefinition(
            id = "fx.$key", key = key, name = json.optString("name", key),
            category = json.optString("category", "other"), description = json.optString("description"),
            params = List(params.length()) { EffectParam.parse(params.getJSONObject(it)) },
            sksl = json.getString("sksl"), pack = tier.pack, source = source, lang = lang,
        )
    }
}
