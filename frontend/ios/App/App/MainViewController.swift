import UIKit
import Capacitor

/// 自定义桥接控制器：在这里注册 App 内自定义的原生插件（NativeSpeechPlugin）
class MainViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        super.capacitorDidLoad()
        bridge?.registerPluginInstance(NativeSpeechPlugin())
    }
}
