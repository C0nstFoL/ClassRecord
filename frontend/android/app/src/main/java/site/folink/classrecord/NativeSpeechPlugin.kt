package site.folink.classrecord

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import com.k2fsa.sherpa.onnx.EndpointConfig
import com.k2fsa.sherpa.onnx.EndpointRule
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineRecognizer
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 内置离线语音识别插件（sherpa-onnx + 中英双语流式 zipformer 模型）：
 * 不依赖系统识别服务与网络，App 内 AudioRecord 采音 → 本地推理 →
 * 通过 partial / final 事件实时向 WebView 推送识别文本。
 * 服务器只接收识别文本，不接收音频。
 *
 * 端点检测：静音 1.2s 判定一句话结束 → 发出 final 并重置流，实现连续听写。
 */
@CapacitorPlugin(name = "NativeSpeech")
class NativeSpeechPlugin : Plugin() {

    companion object {
        private const val PERM = Manifest.permission.RECORD_AUDIO
        private const val SAMPLE_RATE = 16000
        // 模型在 assets 中的目录与文件名
        private const val MODEL_DIR = "sherpa-asr"
        private const val ENCODER = "encoder.int8.onnx"
        private const val DECODER = "decoder.onnx"
        private const val JOINER = "joiner.int8.onnx"
        private const val TOKENS = "tokens.txt"
    }

    private var requestLauncher: ActivityResultLauncher<String>? = null
    private val running = AtomicBoolean(false)
    private var recordThread: Thread? = null
    private var audioRecord: AudioRecord? = null

    @SuppressLint("MissingPermission") // start() 中已做权限检查
    @PluginMethod
    fun start(call: PluginCall) {
        if (running.get()) {
            call.resolve()
            return
        }
        if (!hasPermission()) {
            call.reject("缺少麦克风权限")
            return
        }
        try {
            val modelDir = ensureModelFiles()
            val recognizer = createRecognizer(modelDir)
            val record = createAudioRecord()
            running.set(true)
            recordThread = Thread {
                recognizeLoop(recognizer, record)
            }.apply {
                name = "sherpa-asr"
                start()
            }
            record.startRecording()
            call.resolve()
        } catch (e: Exception) {
            running.set(false)
            releaseResources()
            call.reject("启动离线语音识别失败：" + e.message)
        }
    }

    @PluginMethod
    fun stop(call: PluginCall) {
        running.set(false)
        call.resolve()
    }

    @PluginMethod
    fun checkPermission(call: PluginCall) {
        val ret = JSObject()
        ret.put("granted", hasPermission())
        // 模型已内置，本地识别始终可用
        ret.put("available", modelAssetsExist())
        call.resolve(ret)
    }

    @PluginMethod
    fun requestPermission(call: PluginCall) {
        val ret = JSObject()
        if (hasPermission()) {
            ret.put("granted", true)
            ret.put("triggered", false)
            call.resolve(ret)
            return
        }
        val launcher = requestLauncher
        if (launcher != null) {
            launcher.launch(PERM)
            ret.put("granted", false)
            ret.put("triggered", true)
        } else {
            ret.put("granted", false)
            ret.put("triggered", false)
        }
        call.resolve(ret)
    }

    override fun load() {
        super.load()
        requestLauncher = activity.registerForActivityResult(
            ActivityResultContracts.RequestPermission()
        ) { granted ->
            val ret = JSObject()
            ret.put("granted", granted)
            notifyListeners("permissionResult", ret)
        }
    }

    private fun hasPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, PERM) == PackageManager.PERMISSION_GRANTED

    private fun modelAssetsExist(): Boolean = try {
        context.assets.list(MODEL_DIR)?.contains(ENCODER) == true
    } catch (e: Exception) {
        false
    }

    /** 首次运行时把内置模型从 assets 拷贝到 filesDir（ONNX 运行时需要文件路径） */
    private fun ensureModelFiles(): File {
        val dir = File(context.filesDir, MODEL_DIR)
        dir.mkdirs()
        for (name in arrayOf(ENCODER, DECODER, JOINER, TOKENS)) {
            val f = File(dir, name)
            if (!f.exists() || f.length() == 0L) {
                context.assets.open("$MODEL_DIR/$name").use { input ->
                    f.outputStream().use { output -> input.copyTo(output) }
                }
            }
        }
        return dir
    }

    private fun createRecognizer(modelDir: File): OnlineRecognizer {
        val config = OnlineRecognizerConfig(
            featConfig = FeatureConfig(),
            modelConfig = OnlineModelConfig(
                transducer = OnlineTransducerModelConfig(
                    encoder = File(modelDir, ENCODER).absolutePath,
                    decoder = File(modelDir, DECODER).absolutePath,
                    joiner = File(modelDir, JOINER).absolutePath,
                ),
                tokens = File(modelDir, TOKENS).absolutePath,
                numThreads = 2,
                debug = false,
            ),
            endpointConfig = EndpointConfig(
                rule1 = EndpointRule(mustContainNonSilence = false, minTrailingSilence = 2.4f, minUtteranceLength = 0f),
                rule2 = EndpointRule(mustContainNonSilence = true, minTrailingSilence = 1.2f, minUtteranceLength = 0f),
                rule3 = EndpointRule(mustContainNonSilence = false, minTrailingSilence = 0f, minUtteranceLength = 20f),
            ),
            enableEndpoint = true,
        )
        return OnlineRecognizer(null, config)
    }

    private fun createAudioRecord(): AudioRecord {
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        // 4 秒缓冲：解码线程偶发 GC 停顿时音频不丢失（1 秒缓冲一旦卡顿就会丢字）
        val bufSize = maxOf(minBuf, SAMPLE_RATE * 4)
        val record = AudioRecord(
            MediaRecorder.AudioSource.MIC, SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufSize
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            throw IllegalStateException("AudioRecord 初始化失败")
        }
        audioRecord = record
        return record
    }

    /** 采音 + 流式识别主循环，结束时冲刷最后一段结果 */
    private fun recognizeLoop(recognizer: OnlineRecognizer, record: AudioRecord) {
        var stream: OnlineStream? = null
        val chunk = ShortArray(SAMPLE_RATE / 10) // 100ms
        // 复用同一 float 数组，避免每 100ms 分配新数组引发 GC 停顿导致识别卡顿
        val floatBuf = FloatArray(SAMPLE_RATE / 10)
        var lastPartial = ""
        try {
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_AUDIO)
            stream = recognizer.createStream()
            while (running.get()) {
                val n = record.read(chunk, 0, chunk.size)
                if (n <= 0) continue
                for (i in 0 until n) floatBuf[i] = chunk[i] / 32768f
                // 整块时复用数组避免 GC；仅当读到不足一块（罕见）时才临时分配
                if (n == floatBuf.size) {
                    stream.acceptWaveform(floatBuf, SAMPLE_RATE)
                } else {
                    stream.acceptWaveform(floatBuf.copyOf(n), SAMPLE_RATE)
                }
                while (recognizer.isReady(stream)) recognizer.decode(stream)

                val text = recognizer.getResult(stream).text.trim()
                if (recognizer.isEndpoint(stream)) {
                    if (text.isNotEmpty()) {
                        lastPartial = ""
                        emit("final", text)
                    }
                    recognizer.reset(stream)
                } else if (text.isNotEmpty() && text != lastPartial) {
                    lastPartial = text
                    emit("partial", text)
                }
            }
            // 停止时冲刷剩余音频，把最后一句推出来
            stream.inputFinished()
            while (recognizer.isReady(stream)) recognizer.decode(stream)
            val tail = recognizer.getResult(stream).text.trim()
            if (tail.isNotEmpty()) emit("final", tail)
        } catch (e: Exception) {
            if (running.get()) emit("error", "识别线程异常：" + e.message)
        } finally {
            try {
                if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) record.stop()
            } catch (e: Exception) {
            }
            stream?.release()
            recognizer.release()
            releaseResources()
        }
    }

    private fun releaseResources() {
        try {
            audioRecord?.release()
        } catch (e: Exception) {
        }
        audioRecord = null
        recordThread = null
    }

    private fun emit(event: String, text: String) {
        val data = JSObject()
        data.put("text", text ?: "")
        notifyListeners(event, data)
    }
}
