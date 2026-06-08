# ── Kotlin metadata (needed for Moshi reflective adapters) ───────────────────
-keep class kotlin.Metadata { *; }
-keepattributes *Annotation*, Signature, InnerClasses, EnclosingMethod
-keepattributes RuntimeVisibleAnnotations, RuntimeVisibleParameterAnnotations, AnnotationDefault

# ── Moshi (reflection via KotlinJsonAdapterFactory) ──────────────────────────
-keep class com.squareup.moshi.** { *; }
-keep interface com.squareup.moshi.** { *; }
-dontwarn com.squareup.moshi.**
-keep @com.squareup.moshi.JsonClass class * { *; }
-keepclassmembers class * {
    @com.squareup.moshi.Json <fields>;
    @com.squareup.moshi.FromJson <methods>;
    @com.squareup.moshi.ToJson <methods>;
}

# App data models are (de)serialised reflectively — keep them and their
# constructors fully so R8 can't rename/remove fields Moshi reads by name.
-keep class org.ehealth.eritas.core.model.** { *; }
-keepclassmembers class org.ehealth.eritas.core.model.** {
    <init>(...);
    <fields>;
}

# ── Retrofit / OkHttp ────────────────────────────────────────────────────────
-keepattributes Exceptions
-keep,allowobfuscation,allowshrinking interface retrofit2.Call
-keep,allowobfuscation,allowshrinking class retrofit2.Response
-keepclasseswithmembers interface * { @retrofit2.http.* <methods>; }
-dontwarn retrofit2.**
-dontwarn okhttp3.**
-dontwarn okio.**

# ── MapLibre Native (ships consumer rules; silence remaining warnings) ───────
-dontwarn org.maplibre.**

# ── androidx.security:security-crypto → Tink → compile-only annotations ──────
# Tink references errorprone / javax annotations that aren't on the runtime
# classpath; they're safe to ignore (R8 fails the build without this).
-dontwarn com.google.errorprone.annotations.**
-dontwarn javax.annotation.**
-dontwarn javax.annotation.concurrent.**
-keep class com.google.crypto.tink.** { *; }
