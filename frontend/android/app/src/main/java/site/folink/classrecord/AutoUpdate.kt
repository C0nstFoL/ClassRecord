package site.folink.classrecord

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * 应用内更新实现：下载 APK 到缓存目录并通过 FileProvider 发起系统安装。
 */
class AutoUpdate(private val context: Context) {

    /** Android 8.0+ 需要用户授予「安装未知应用」权限。 */
    fun canRequestInstall(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            context.packageManager.canRequestPackageInstalls()
        else true

    fun openInstallSettings() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val intent = Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:${context.packageName}")
            ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
        }
    }

    /** 下载 APK，按百分比回调进度（total 为 -1 表示服务器未返回长度）。 */
    fun downloadApk(url: String, onProgress: (Long, Long) -> Unit): File {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 15000
        conn.readTimeout = 30000
        conn.instanceFollowRedirects = true
        try {
            val total = conn.contentLengthLong
            val file = File(context.cacheDir, "update.apk")
            conn.inputStream.use { input ->
                file.outputStream().use { output ->
                    val buf = ByteArray(64 * 1024)
                    var received = 0L
                    var lastPct = -1L
                    while (true) {
                        val n = input.read(buf)
                        if (n == -1) break
                        output.write(buf, 0, n)
                        received += n
                        val pct = if (total > 0) received * 100 / total else 0
                        if (pct != lastPct) {
                            lastPct = pct
                            onProgress(received, total)
                        }
                    }
                    output.flush()
                }
            }
            if (total > 0 && file.length() < total) {
                file.delete()
                throw IllegalStateException("下载不完整：${file.length()}/$total")
            }
            return file
        } finally {
            conn.disconnect()
        }
    }

    /** 通过 FileProvider 发起系统安装器。 */
    fun installApk(file: File) {
        val uri: Uri = FileProvider.getUriForFile(
            context, "${context.packageName}.fileprovider", file
        )
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
    }
}
