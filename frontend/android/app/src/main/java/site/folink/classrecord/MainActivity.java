package site.folink.classrecord;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // 注册自定义通知权限插件，必须在 super.onCreate 之前
        registerPlugin(NotificationPermissionPlugin.class);
        // 应用内更新插件
        registerPlugin(AutoUpdatePlugin.class);
        super.onCreate(savedInstanceState);
    }
}
