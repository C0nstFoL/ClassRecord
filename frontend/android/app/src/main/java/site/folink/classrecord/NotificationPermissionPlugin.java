package site.folink.classrecord;

import android.Manifest;
import android.content.pm.PackageManager;
import android.os.Build;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.core.content.ContextCompat;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * 直接以原生方式检查与请求通知权限 (POST_NOTIFICATIONS)。
 * 绕开 Capacitor 抽象，避免在某些 ROM (如华为 EMUI) 上系统弹窗被吞的问题。
 */
@CapacitorPlugin(name = "NotificationPermission")
public class NotificationPermissionPlugin extends Plugin {

    private static final String PERM = "android.permission.POST_NOTIFICATIONS";
    private static final int REQ_CODE = 9001;

    private ActivityResultLauncher<String> requestLauncher;

    @Override
    public void load() {
        super.load();
        // Android 13+ 才需要运行时通知权限；老系统永远视为 granted
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestLauncher = getActivity().registerForActivityResult(
                new ActivityResultContracts.RequestPermission(),
                granted -> {
                    JSObject ret = new JSObject();
                    ret.put("granted", Boolean.TRUE.equals(granted));
                    notifyListeners("permissionResult", ret);
                }
            );
        }
    }

    @PluginMethod
    public void check(PluginCall call) {
        JSObject ret = new JSObject();
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            ret.put("granted", true);
            ret.put("sdkInt", Build.VERSION.SDK_INT);
            call.resolve(ret);
            return;
        }
        int state = ContextCompat.checkSelfPermission(getContext(), PERM);
        boolean granted = state == PackageManager.PERMISSION_GRANTED;
        ret.put("granted", granted);
        ret.put("sdkInt", Build.VERSION.SDK_INT);
        call.resolve(ret);
    }

    @PluginMethod
    public void request(PluginCall call) {
        // 立即返回当前状态；真正的弹窗由 ActivityResultLauncher 异步触发
        JSObject ret = new JSObject();
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            ret.put("granted", true);
            ret.put("triggered", false);
            call.resolve(ret);
            return;
        }
        int state = ContextCompat.checkSelfPermission(getContext(), PERM);
        if (state == PackageManager.PERMISSION_GRANTED) {
            ret.put("granted", true);
            ret.put("triggered", false);
            call.resolve(ret);
            return;
        }
        // 异步触发系统弹窗
        if (requestLauncher != null) {
            requestLauncher.launch(PERM);
            ret.put("granted", false);
            ret.put("triggered", true);
        } else {
            ret.put("granted", false);
            ret.put("triggered", false);
        }
        call.resolve(ret);
    }
}
