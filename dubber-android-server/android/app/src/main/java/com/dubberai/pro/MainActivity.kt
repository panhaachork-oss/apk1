package com.dubberai.pro

import android.content.Context
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.FileOutputStream

// ------------------------------------------------------------------ state
class DubberViewModel : ViewModel() {
    var serverUrl by mutableStateOf("")
    var geminiKey by mutableStateOf("")
    var job by mutableStateOf<JobOut?>(null)
    var message by mutableStateOf("")
    var busy by mutableStateOf(false)
    var segments by mutableStateOf<List<Segment>>(emptyList())

    private fun api() = createApi(serverUrl.trim())

    /** Poll until the job leaves a working state. */
    fun poll(id: String) = viewModelScope.launch {
        while (true) {
            try {
                val j = api().getJob(id)
                job = j; segments = j.segments; message = j.progress
                if (j.status in listOf("ready", "done", "error")) {
                    if (j.status == "error") message = "Error: ${j.error}"
                    break
                }
            } catch (e: Exception) { message = "Poll error: ${e.message}"; break }
            delay(3000)
        }
        busy = false
    }

    fun upload(context: Context, uri: Uri) = viewModelScope.launch {
        busy = true; message = "Uploading video…"
        try {
            val file = uriToFile(context, uri)
            val body = file.readBytes().toRequestBody("video/mp4".toMediaTypeOrNull())
            val part = MultipartBody.Part.createFormData("file", file.name, body)
            val j = api().transcribe(part, "gemini-3.6-flash", "km", true, geminiKey)
            job = j; poll(j.job_id)
        } catch (e: Exception) { busy = false; message = "Upload failed: ${e.message}" }
    }

    fun runStep(action: suspend DubberApi.(String) -> JobOut) = viewModelScope.launch {
        val id = job?.job_id ?: return@launch
        busy = true
        try {
            // Save edits first so the server exports exactly what is on screen.
            api().setSegments(id, SegmentsReq(segments))
            val j = api().action(id); job = j; poll(j.job_id)
        } catch (e: Exception) { busy = false; message = "Failed: ${e.message}" }
    }

    fun download(context: Context, type: String) = viewModelScope.launch {
        val id = job?.job_id ?: return@launch
        busy = true; message = "Downloading $type…"
        try {
            val body = api().download(id, type)
            val out = File(context.getExternalFilesDir(null),
                if (type == "video") "dubbed.mp4" else "dubbed.srt")
            FileOutputStream(out).use { o -> body.byteStream().copyTo(o) }
            message = "Saved to: ${out.absolutePath}"
        } catch (e: Exception) { message = "Download failed: ${e.message}" }
        busy = false
    }
}

fun uriToFile(context: Context, uri: Uri): File {
    var name = "input.mp4"
    context.contentResolver.query(uri, null, null, null, null)?.use { c ->
        if (c.moveToFirst()) {
            val i = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (i >= 0) name = c.getString(i) ?: name
        }
    }
    val out = File(context.cacheDir, name)
    context.contentResolver.openInputStream(uri)?.use { input ->
        FileOutputStream(out).use { input.copyTo(it) }
    }
    return out
}

// -------------------------------------------------------------------- UI
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { DubberScreen() } }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DubberScreen(vm: DubberViewModel = viewModel()) {
    val context = LocalContext.current
    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) vm.upload(context, uri)
    }
    // Restore saved settings (Phase 1: plain prefs; see README security note).
    val prefs = remember { context.getSharedPreferences("dubber", Context.MODE_PRIVATE) }
    LaunchedEffect(Unit) {
        vm.serverUrl = prefs.getString("server", "") ?: ""
        vm.geminiKey = prefs.getString("gemini", "") ?: ""
    }
    Scaffold(topBar = { TopAppBar(title = { Text("DUBBER AI PRO — Mobile") }) }) { pad ->
        LazyColumn(Modifier.padding(pad).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)) {
            item {
                OutlinedTextField(vm.serverUrl, { vm.serverUrl = it },
                    label = { Text("Server URL, e.g. https://your-server:8000") },
                    modifier = Modifier.fillMaxWidth())
                OutlinedTextField(vm.geminiKey, { vm.geminiKey = it },
                    label = { Text("Gemini API key (optional if set on server)") },
                    modifier = Modifier.fillMaxWidth())
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = {
                        prefs.edit().putString("server", vm.serverUrl)
                            .putString("gemini", vm.geminiKey).apply()
                        picker.launch("video/*")
                    }, enabled = !vm.busy) { Text("📹 Pick video + Transcribe") }
                }
                Text(vm.message, style = MaterialTheme.typography.bodyMedium)
                vm.job?.let { Text("Job: ${it.job_id} • ${it.status}") }
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { vm.runStep { id -> translate(id, TranslateReq("km"), vm.geminiKey) } },
                        enabled = vm.job != null && !vm.busy) { Text("Translate") }
                    Button(onClick = { vm.runStep { id -> tts(id, TtsReq()) } },
                        enabled = vm.job != null && !vm.busy) { Text("Generate voices") }
                    Button(onClick = { vm.runStep { id -> export(id, ExportReq()) } },
                        enabled = vm.job != null && !vm.busy) { Text("Export video") }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { vm.download(context, "video") },
                        enabled = vm.job?.status == "done") { Text("⬇ Video") }
                    Button(onClick = { vm.download(context, "srt") },
                        enabled = vm.job != null) { Text("⬇ SRT") }
                }
                Text("Segments — tap a row's text to edit it, then press a step button (edits are saved first).")
            }
            items(vm.segments) { seg ->
                var text by remember(seg.index) { mutableStateOf(seg.translated.ifEmpty { seg.original }) }
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(10.dp)) {
                        Text("${seg.index + 1}. ${"%.1f".format(seg.start)}s → ${"%.1f".format(seg.end)}s  •  ${seg.original}",
                            style = MaterialTheme.typography.bodySmall)
                        OutlinedTextField(text, {
                            text = it
                            vm.segments = vm.segments.map { s ->
                                if (s.index == seg.index) s.copy(translated = it) else s
                            }
                        }, label = { Text("Translated / dubbed text") },
                            modifier = Modifier.fillMaxWidth())
                    }
                }
            }
        }
    }
}
