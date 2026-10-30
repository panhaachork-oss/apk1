package com.dubberai.pro

import okhttp3.MultipartBody
import okhttp3.ResponseBody
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.*

// Segment model — identical fields to the server / desktop table.
data class Segment(
    val index: Int = 0,
    val start: Double = 0.0,
    val end: Double = 0.0,
    val original: String = "",
    val translated: String = "",
    val voice: String = "",
    val pitch: String = "+0Hz",
    val rate: String = "+0%"
)

data class JobOut(
    val job_id: String = "",
    val status: String = "",
    val progress: String = "",
    val video_name: String = "",
    val duration: Double = 0.0,
    val segments: List<Segment> = emptyList(),
    val error: String = ""
)

data class TranslateReq(val target: String = "km", val engine: String = "google")
data class TtsReq(val voice: String = "km-KH-SreymomNeural", val pitch: String = "+0Hz", val rate: String = "+0%")
data class ExportReq(val background_volume: Int = 30, val tts_volume: Int = 100)
data class SegmentsReq(val segments: List<Segment>)

interface DubberApi {
    @Multipart
    @POST("jobs/transcribe")
    suspend fun transcribe(
        @Part video: MultipartBody.Part,
        @Query("model") model: String,
        @Query("target") target: String,
        @Query("auto_translate") autoTranslate: Boolean,
        @Header("X-Gemini-Key") geminiKey: String
    ): JobOut

    @GET("jobs/{id}")
    suspend fun getJob(@Path("id") id: String): JobOut

    @POST("jobs/{id}/segments")
    suspend fun setSegments(@Path("id") id: String, @Body req: SegmentsReq): JobOut

    @POST("jobs/{id}/translate")
    suspend fun translate(@Path("id") id: String, @Body req: TranslateReq,
                          @Header("X-Gemini-Key") geminiKey: String = ""): JobOut

    @POST("jobs/{id}/tts")
    suspend fun tts(@Path("id") id: String, @Body req: TtsReq): JobOut

    @POST("jobs/{id}/export")
    suspend fun export(@Path("id") id: String, @Body req: ExportReq): JobOut

    @GET("jobs/{id}/download")
    @Streaming
    suspend fun download(@Path("id") id: String, @Query("type") type: String): ResponseBody
}

fun createApi(baseUrl: String): DubberApi {
    val url = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"
    return Retrofit.Builder()
        .baseUrl(url)
        .addConverterFactory(GsonConverterFactory.create())
        .build()
        .create(DubberApi::class.java)
}
