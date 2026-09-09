import Foundation
import AVFoundation
import Capacitor
import SherpaOnnx

/**
 * 内置离线语音识别插件（iOS 版，与安卓 NativeSpeechPlugin 接口一致）：
 * sherpa-onnx SPM 依赖（k2-fsa/sherpa-onnx）+ 内置中英双语流式 zipformer 模型，
 * AVAudioEngine 16kHz 单声道采音 → 本地推理 → partial / final 事件推送 WebView。
 * 服务器只接收识别文本，不接收音频。
 *
 * 端点检测：静音 1.2s 判定一句话结束 → 发出 final 并重置流，连续听写。
 */
@objc(NativeSpeechPlugin)
public class NativeSpeechPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "NativeSpeechPlugin"
    public let jsName = "NativeSpeech"
    public let pluginMethods = [
        CAPPluginMethod(name: "checkPermission", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "requestPermission", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "start", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stop", returnType: CAPPluginReturnPromise),
    ]

    private let sampleRate = 16000
    private let engine = AVAudioEngine()
    private let queue = DispatchQueue(label: "sherpa-asr", qos: .userInteractive)
    private let running = AtomicFlag()
    private var recognizer: SherpaOnnxRecognizer?
    private var lastPartial = ""

    // MARK: - 插件方法

    @objc func checkPermission(_ call: CAPPluginCall) {
        let granted = AVAudioSession.sharedInstance().recordPermission == .granted
        call.resolve([
            "granted": granted,
            // 模型内置在 App bundle 中，本地识别始终可用
            "available": Self.modelAssetURL("encoder.int8.onnx") != nil,
        ])
    }

    @objc func requestPermission(_ call: CAPPluginCall) {
        if AVAudioSession.sharedInstance().recordPermission == .granted {
            call.resolve(["granted": true, "triggered": false])
            return
        }
        AVAudioSession.sharedInstance().requestRecordPermission { [weak self] granted in
            DispatchQueue.main.async {
                self?.notifyListeners("permissionResult", data: ["granted": granted])
            }
        }
        call.resolve(["granted": false, "triggered": true])
    }

    @objc func start(_ call: CAPPluginCall) {
        if running.get() {
            call.resolve()
            return
        }
        guard AVAudioSession.sharedInstance().recordPermission == .granted else {
            call.reject("缺少麦克风权限")
            return
        }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .default, options: [.allowBluetooth, .defaultToSpeaker])
            try session.setActive(true)

            let rec = try Self.createRecognizer()
            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            // 16kHz 单声道：与模型输入一致
            guard format.sampleRate > 0 else {
                call.reject("音频输入不可用")
                return
            }
            let tapFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(sampleRate), channels: 1, interleaved: false)!
            input.installTap(onBus: 0, bufferSize: 1600, format: tapFormat) { [weak self] buffer, _ in
                guard let self, self.running.get() else { return }
                guard let channel = buffer.floatChannelData?[0] else { return }
                let frames = Int(buffer.frameLength)
                let samples = Array(UnsafeBufferPointer(start: channel, count: frames))
                self.queue.async { self.process(samples: samples) }
            }
            engine.prepare()

            recognizer = rec
            lastPartial = ""
            running.set()
            try engine.start()
            call.resolve()
        } catch {
            running.clear()
            call.reject("启动离线语音识别失败：\(error.localizedDescription)")
        }
    }

    @objc func stop(_ call: CAPPluginCall) {
        if running.get() {
            running.clear()
            // 冲刷剩余音频，把最后一句推出来，再停止引擎
            queue.async { [weak self] in
                guard let self else { return }
                self.flushRemaining()
                self.engine.inputNode.removeTap(onBus: 0)
                self.engine.stop()
                self.recognizer = nil
            }
        }
        call.resolve()
    }

    // MARK: - 识别循环（queue 上执行）

    private func process(samples: [Float]) {
        guard let rec = recognizer else { return }
        rec.acceptWaveform(samples: samples, sampleRate: sampleRate)
        while rec.isReady() { rec.decode() }

        let text = rec.getResult().text.trimmingCharacters(in: .whitespacesAndNewlines)
        if rec.isEndpoint() {
            if !text.isEmpty, text != lastPartial {
                emit("final", text)
            }
            lastPartial = ""
            rec.reset()
        } else if !text.isEmpty, text != lastPartial {
            lastPartial = text
            emit("partial", text)
        }
    }

    /// 停止时由 stop() 触发：冲刷剩余音频，把最后一句推出来
    private func flushRemaining() {
        guard let rec = recognizer, running.get() == false else { return }
        rec.inputFinished()
        while rec.isReady() { rec.decode() }
        let tail = rec.getResult().text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !tail.isEmpty { emit("final", tail) }
    }

    private func emit(_ event: String, _ text: String) {
        DispatchQueue.main.async { [weak self] in
            self?.notifyListeners(event, data: ["text": text])
        }
    }

    // MARK: - 模型与识别器

    static func modelAssetURL(_ name: String) -> URL? {
        Bundle.main.url(forResource: "sherpa-asr/\(name)", withExtension: nil)
            ?? Bundle.main.url(forResource: name, withExtension: nil, subdirectory: "sherpa-asr")
    }

    static func createRecognizer() throws -> SherpaOnnxRecognizer {
        guard
            let encoder = modelAssetURL("encoder.int8.onnx")?.path,
            let decoder = modelAssetURL("decoder.onnx")?.path,
            let joiner = modelAssetURL("joiner.int8.onnx")?.path,
            let tokens = modelAssetURL("tokens.txt")?.path
        else {
            throw NSError(domain: "NativeSpeech", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "内置识别模型缺失，请重新安装 App"])
        }
        let modelConfig = sherpaOnnxOnlineModelConfig(
            tokens: tokens,
            transducer: sherpaOnnxOnlineTransducerModelConfig(
                encoder: encoder, decoder: decoder, joiner: joiner),
            numThreads: 2,
            debug: 0,
            modelType: "zipformer"
        )
        let config = sherpaOnnxOnlineRecognizerConfig(
            featConfig: sherpaOnnxFeatureConfig(),
            modelConfig: modelConfig,
            enableEndpoint: true,
            rule1MinTrailingSilence: 2.4,
            rule2MinTrailingSilence: 1.2,
            rule3MinUtteranceLength: 30
        )
        var cfg = config
        return SherpaOnnxRecognizer(config: &cfg)
    }
}

/// 简单原子标志（Swift 无原子布尔，用信号量保护）
final class AtomicFlag {
    private let sem = DispatchSemaphore(value: 1)
    private var value = false

    @discardableResult
    func set() -> Bool {
        sem.wait()
        defer { sem.signal() }
        if value { return false }
        value = true
        return true
    }

    func clear() {
        sem.wait()
        value = false
        sem.signal()
    }

    func get() -> Bool {
        sem.wait()
        defer { sem.signal() }
        return value
    }
}
