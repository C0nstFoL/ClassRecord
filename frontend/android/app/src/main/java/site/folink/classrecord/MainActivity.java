package site.folink.classrecord;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // 注册自定义通知权限插件，必须在 super.onCreate 之前
        registerPlugin(NotificationPermissionPlugin.class);
        // 注册系统语音识别插件
        registerPlugin(NativeSpeechPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
