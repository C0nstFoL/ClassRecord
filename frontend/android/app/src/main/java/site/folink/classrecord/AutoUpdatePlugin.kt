package site.folink.classrecord

import android.os.Build
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin

/**
 * 应用内更新插件：查询当前版本、检查安装权限、下载 APK 并发起安装。
 * Web 层在启动时调用 /api/app/check-update，有新版本时调用本插件完成更新。
 */
@CapacitorPlugin(name = "AutoUpdate")
class AutoUpdatePlugin : Plugin() {

    private lateinit var implementation: AutoUpdate

    override fun load() {
        implementation = AutoUpdate(context)
    }

    @PluginMethod
    fun getAppInfo(call: PluginCall) {
        val pi = context.packageManager.getPackageInfo(context.packageName, 0)
        val ret = JSObject()
        ret.put("versionName", pi.versionName ?: "")
        val code = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            pi.longVersionCode
        } else {
            @Suppress("DEPRECATION")
            pi.versionCode.toLong()
        }
        ret.put("versionCode", code)
        call.resolve(ret)
    }

    @PluginMethod
    fun canInstall(call: PluginCall) {
        val ret = JSObject()
        ret.put("granted", implementation.canRequestInstall())
        call.resolve(ret)
    }

    @PluginMethod
    fun openInstallSettings(call: PluginCall) {
        implementation.openInstallSettings()
        call.resolve()
    }

    @PluginMethod
    fun downloadAndInstall(call: PluginCall) {
        val url = call.getString("url")
        if (url.isNullOrBlank()) {
            call.reject("缺少 url 参数")
            return
        }
        try {
            val file = implementation.downloadApk(url) { received, total ->
                val data = JSObject()
                data.put("received", received)
                data.put("total", total)
                data.put("progress", if (total > 0) received * 100 / total else 0)
                notifyListeners("downloadProgress", data)
            }
            implementation.installApk(file)
            call.resolve()
        } catch (e: Exception) {
            call.reject("下载安装失败：${e.message}")
        }
    }
}
