# 课堂记录助手

语音转写 · AI 智能总结 · 内容问答。支持上传录音与浏览器实时录制两种模式。

## 功能

- 上传音频并生成转写文本
- 浏览器或 Android 后台实时录音与转写
- 使用 OpenAI 兼容接口生成课堂总结、问答和作业整理
- 按记录搜索、查看详情，并生成限时只读分享链接

## 项目结构

- `backend/`：FastAPI 服务、认证、音频处理和数据存储
- `frontend/`：React + Vite Web 前端及 Capacitor 移动端工程
- `backend/data/`：本地数据库、录音、APK 和模型文件，不纳入 Git

## Web 部署（Docker）

### 环境要求

- Docker Compose
- NVIDIA Container Toolkit（使用 GPU 推理时）
- Node.js 20+ 和 npm

前端需先构建产物（输出到 `backend/static`，由 FastAPI 同源托管）：

```bash
cd frontend && npm install && npm run build
```

然后构建并启动容器（监听 8002 端口，需要宿主机已配置 nvidia-container-runtime）：

```bash
docker compose up -d --build
```

配置通过 `backend/.env` 注入容器（参考 `.env.example`）。数据库与录音文件挂载在 `backend/data/`。

首次部署前至少需要配置：

- `APP_SECRET_KEY`：长度足够且随机的生产密钥，不能使用示例值
- `PUBLIC_BASE_URL`：对外访问的 HTTPS 地址
- `ZITADEL_ISSUER`、`ZITADEL_CLIENT_ID`：OIDC 登录配置
- `LLM_API_KEY`：大模型服务密钥（如启用 AI 总结）

不要将 `backend/.env`、录音、数据库或模型缓存提交到 Git。生产环境应使用 HTTPS，并保持 `COOKIE_SECURE=true`、`DISABLE_AUTH=false`。

## 本地开发

后端依赖 Python 3.11+。前端开发服务器和后端分别启动：

```bash
cd frontend && npm install && npm run dev
```

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

开发环境配置仍参考 `.env.example`；前端 API 地址按 `frontend/src/api.ts` 中的配置使用。

## Android App（Capacitor）

App 为远程壳形态：WebView 直接加载线上站点，登录/录音/推流全部复用 Web 链路，后端零改动。实时录制时启动 `microphone` 类型前台服务，锁屏/切后台可继续录制。

### 环境要求

- Node 20+
- JDK 17+
- Android SDK（`ANDROID_HOME` 或 `frontend/android/local.properties` 中 `sdk.dir` 指向）

### 构建 APK

```bash
cd frontend
npm install
npm run build          # 生成 dist（占位页，App 实际加载线上站点）
npx cap sync android   # 同步插件与配置
cd android && ./gradlew assembleDebug   # 调试包
# 或 ./gradlew assembleRelease          # 发布包（需自行配置签名）
```

产物位于 `frontend/android/app/build/outputs/apk/`。

### 后台录制说明

- AndroidManifest 已声明 `RECORD_AUDIO`、`FOREGROUND_SERVICE_MICROPHONE` 等权限
- 录制开始时由 `frontend/src/nativeRecording.ts` 启动前台服务并保持 CPU 唤醒，结束/断连时自动停止
- 若部分厂商 ROM 后台限制过强导致采集中断，请在系统设置中为 App 关闭电池优化

## iOS（预留，需 Mac）

在 macOS 上执行：

```bash
cd frontend
npm install @capacitor/ios
npx cap add ios
npx cap open ios   # Xcode 中配置签名
```

并在 Xcode 的 Signing & Capabilities 中添加 `Background Modes -> Audio` 后台音频能力，同时在前端登录链路中处理 `AVAudioSession`（`playAndRecord` 类别）以维持锁屏录音。本仓库当前未包含 iOS 工程（Linux 环境无法生成/构建）。
