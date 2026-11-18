#!/usr/bin/env python3
"""Rewrites the signing part of Effects/app/build.gradle.kts so every build type uses ONE permanent key.
Usage: python3 tools/apply_signing.py Effects/app/build.gradle.kts   (safe to run twice)"""
import sys

p = sys.argv[1]
s = open(p).read()
if 'PERMANENT_SIGNING' in s:
    print('already patched'); sys.exit(0)

HEAD = '''// PERMANENT_SIGNING: ONE signing identity for debug and release. Key comes from env vars (CI) or git-ignored signing.properties.
// There is NO fallback to a generated/default debug key: a missing key stops the build.
val signingProps = rootProject.file("signing.properties").takeIf { it.isFile }
    ?.let { f -> Properties().apply { f.inputStream().use { load(it) } } }
fun signingValue(env: String, prop: String): String? =
    System.getenv(env)?.takeIf { it.isNotBlank() } ?: signingProps?.getProperty(prop)?.takeIf { it.isNotBlank() }
val permanentStore: File? = signingValue("EFFECTS_KEYSTORE_PATH", "storeFile")?.let { File(it).let { f -> if (f.isAbsolute) f else rootProject.file(it) } }
val permanentStorePassword: String? = signingValue("EFFECTS_KEYSTORE_PASSWORD", "storePassword")
val permanentKeyAlias: String? = signingValue("EFFECTS_KEY_ALIAS", "keyAlias")
val permanentKeyPassword: String? = signingValue("EFFECTS_KEY_PASSWORD", "keyPassword") ?: permanentStorePassword

tasks.configureEach {
    if (name.startsWith("validateSigning")) {
        doFirst {
            if (permanentStore?.isFile != true || permanentStorePassword == null || permanentKeyAlias == null) {
                throw GradleException("Permanent signing key not configured (EFFECTS_KEYSTORE_PATH / _PASSWORD / EFFECTS_KEY_ALIAS / EFFECTS_KEY_PASSWORD or signing.properties). Refusing to sign with any other key.")
            }
        }
    }
}

'''
SIGN = '''    val permanentSigning = signingConfigs.create("permanent") {
        storeFile = permanentStore ?: rootProject.file("MISSING-PERMANENT-KEYSTORE")
        storePassword = permanentStorePassword ?: ""
        keyAlias = permanentKeyAlias ?: "effects"
        keyPassword = permanentKeyPassword ?: ""
        enableV1Signing = false
        enableV2Signing = true
        enableV3Signing = true
    }

'''
def need(x):
    if x not in s:
        sys.exit('ERROR: build.gradle.kts has an unexpected layout, missing: ' + x.strip()[:60])
    return s.index(x)

need('android {\n'); need('    buildTypes {\n')
# old signing region: from its first marker up to buildTypes
starts = [i for i in (s.find('    // The installed app is signed'), s.find('    signingConfigs.getByName("debug")')) if i >= 0]
if not starts:
    sys.exit('ERROR: old signing block not found')
a, b = min(starts), s.index('    buildTypes {\n')
if a > b:
    sys.exit('ERROR: unexpected order in build.gradle.kts')
s = s[:a] + SIGN + s[b:]
s = s.replace('android {\n', HEAD + 'android {\n', 1)
s = s.replace('    buildTypes {\n', '    buildTypes {\n        debug {\n            signingConfig = permanentSigning\n        }\n', 1)
s = s.replace('signingConfig = signingConfigs.findByName("release")', 'signingConfig = permanentSigning')
if 'ci-debug' in s or 'findByName("release")' in s or s.count('{') != s.count('}'):
    sys.exit('ERROR: patch result looks wrong')
open(p, 'w').write(s)
print('signing patched')
